"""Recursive Work Memory: positive + negative search cache (spec §8).

Storage: SQLite (stdlib, $0). Swap for Postgres behind the same interface in
production — see docs/CURSOR_BUILD_SLICES.md.

Semantics that matter:
* A negative entry means "nothing relevant in THIS source, for THIS normalized
  query, jurisdiction and scope, as of retrieved_at". It never means the
  business does not exist, and it expires sooner than positive entries.
* Source errors are never cached (an outage is not a negative result).
* Entries are partitioned by `namespace` (client/tenant). Nothing is shared
  across namespaces unless the caller explicitly reads another namespace.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .models import SCHEMA_VERSION, Identifier, SourceRecord, iso, parse_iso, sha256_json, utcnow
from .privacy import ensure_private_dir, restrict_file

DEFAULT_POSITIVE_TTL = timedelta(days=30)
DEFAULT_NEGATIVE_TTL = timedelta(days=7)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    namespace         TEXT NOT NULL,
    cache_key         TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    id_type           TEXT NOT NULL,
    normalized_query  TEXT NOT NULL,
    jurisdiction      TEXT,
    scope             TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('positive','negative')),
    payload_json      TEXT NOT NULL,
    retrieved_at      TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    recheck_conditions TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    PRIMARY KEY (namespace, cache_key)
);
CREATE INDEX IF NOT EXISTS ix_cache_query ON cache_entries(namespace, normalized_query);
CREATE INDEX IF NOT EXISTS ix_cache_source ON cache_entries(namespace, source_id);
"""

NEGATIVE_RECHECK = [
    "entry expired",
    "source records changed",
    "new evidence references this identifier",
    "search scope or jurisdiction changed",
    "correction or deletion request",
]


@dataclass
class CacheEntry:
    status: str                  # 'positive' | 'negative'
    records: list[SourceRecord]
    retrieved_at: datetime
    expires_at: datetime
    source_id: str
    normalized_query: str
    jurisdiction: str | None
    scope: str


@dataclass
class CacheMetrics:
    lookups: int = 0
    positive_hits: int = 0
    negative_hits: int = 0
    misses: int = 0
    expired: int = 0
    writes: int = 0
    invalidations: int = 0
    estimated_cost_avoided_usd: float = 0.0
    revalidations_attempted: int = 0
    revalidations_succeeded: int = 0
    # Require a labelled benchmark (spec Phase 8); left None rather than invented.
    false_positive_rate: float | None = None
    false_negative_rate: float | None = None

    @property
    def hits(self) -> int:
        return self.positive_hits + self.negative_hits

    @property
    def hit_rate(self) -> float:
        return self.hits / self.lookups if self.lookups else 0.0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.update(hits=self.hits, hit_rate=round(self.hit_rate, 4), external_calls_avoided=self.hits,
                 estimated_token_savings=0)  # v0.1 has no LLM calls; nothing to save
        return d


def cache_key(source_id: str, ident: Identifier, jurisdiction: str | None, scope: str) -> str:
    return sha256_json([SCHEMA_VERSION, source_id, ident.type.value, ident.normalized, jurisdiction, scope])


@dataclass
class SearchCache:
    path: str | Path
    clock: Callable[[], datetime] = utcnow
    positive_ttl: timedelta = DEFAULT_POSITIVE_TTL
    negative_ttl: timedelta = DEFAULT_NEGATIVE_TTL
    source_ttl_overrides: dict[str, tuple[timedelta, timedelta]] = field(default_factory=dict)
    metrics: CacheMetrics = field(default_factory=CacheMetrics)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        if str(self.path) == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            p = Path(self.path)
            ensure_private_dir(p.parent)
            self._conn = sqlite3.connect(p, check_same_thread=False)
            restrict_file(p)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ helpers
    def _ttls(self, source_id: str) -> tuple[timedelta, timedelta]:
        return self.source_ttl_overrides.get(source_id, (self.positive_ttl, self.negative_ttl))

    # ------------------------------------------------------------ read
    def get(self, namespace: str, source_id: str, ident: Identifier, jurisdiction: str | None,
            scope: str, cost_if_missed: float = 0.0, *, count: bool = True) -> CacheEntry | None:
        """Return a fresh entry or None. `count=False` peeks without touching metrics (preflight)."""
        key = cache_key(source_id, ident, jurisdiction, scope)
        with self._lock:
            row = self._conn.execute(
                "SELECT status, payload_json, retrieved_at, expires_at, jurisdiction, scope "
                "FROM cache_entries WHERE namespace=? AND cache_key=?", (namespace, key)).fetchone()
            m = self.metrics if count else CacheMetrics()
            m.lookups += 1
            if row is None:
                m.misses += 1
                return None
            status, payload, retrieved, expires, jur, scp = row
            if parse_iso(expires) <= self.clock():
                m.expired += 1
                m.misses += 1
                m.revalidations_attempted += 1
                return None
            if status == "positive":
                m.positive_hits += 1
            else:
                m.negative_hits += 1
            m.estimated_cost_avoided_usd += cost_if_missed
        records = [SourceRecord.from_dict(r) for r in json.loads(payload)]
        return CacheEntry(status, records, parse_iso(retrieved), parse_iso(expires), source_id,
                          ident.normalized, jur, scp)

    # ------------------------------------------------------------ write
    def put(self, namespace: str, source_id: str, ident: Identifier, jurisdiction: str | None,
            scope: str, records: list[SourceRecord], retrieved_at: datetime | None = None,
            revalidation: bool = False) -> CacheEntry:
        retrieved_at = retrieved_at or self.clock()
        pos_ttl, neg_ttl = self._ttls(source_id)
        status = "positive" if records else "negative"
        expires_at = retrieved_at + (pos_ttl if records else neg_ttl)
        recheck = NEGATIVE_RECHECK if not records else ["entry expired", "source record changed", "correction request"]
        key = cache_key(source_id, ident, jurisdiction, scope)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (namespace, key, source_id, ident.type.value, ident.normalized, jurisdiction, scope, status,
                 json.dumps([r.to_dict() for r in records]), iso(retrieved_at), iso(expires_at),
                 json.dumps(recheck), SCHEMA_VERSION))
            self._conn.commit()
            self.metrics.writes += 1
            if revalidation:
                self.metrics.revalidations_succeeded += 1
        return CacheEntry(status, records, retrieved_at, expires_at, source_id, ident.normalized, jurisdiction, scope)

    # ------------------------------------------------------------ invalidation
    def invalidate(self, namespace: str, *, normalized_query: str | None = None,
                   source_id: str | None = None) -> int:
        """Invalidate by query, by source, or both. At least one filter is required."""
        if normalized_query is None and source_id is None:
            raise ValueError("invalidate requires normalized_query and/or source_id")
        clauses, params = ["namespace=?"], [namespace]
        if normalized_query is not None:
            clauses.append("normalized_query=?")
            params.append(normalized_query)
        if source_id is not None:
            clauses.append("source_id=?")
            params.append(source_id)
        with self._lock:
            cur = self._conn.execute(f"DELETE FROM cache_entries WHERE {' AND '.join(clauses)}", params)
            self._conn.commit()
            self.metrics.invalidations += cur.rowcount
            return cur.rowcount

    def purge_namespace(self, namespace: str) -> int:
        """Deletion-request support: remove everything held for a client."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM cache_entries WHERE namespace=?", (namespace,))
            self._conn.commit()
            self.metrics.invalidations += cur.rowcount
            return cur.rowcount

    def freshness(self, namespace: str) -> dict[str, int]:
        now = iso(self.clock())
        with self._lock:
            fresh = self._conn.execute("SELECT COUNT(*) FROM cache_entries WHERE namespace=? AND expires_at>?",
                                       (namespace, now)).fetchone()[0]
            total = self._conn.execute("SELECT COUNT(*) FROM cache_entries WHERE namespace=?",
                                       (namespace,)).fetchone()[0]
        return {"entries": total, "fresh": fresh, "stale": total - fresh}

    def close(self) -> None:
        self._conn.close()
