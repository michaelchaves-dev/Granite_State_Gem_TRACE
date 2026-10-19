"""Source adapter contract (spec §9).

An adapter wraps exactly one source. It must:
* declare tier, type, supported identifier types, per-request cost and whether
  the owner has approved it (terms reviewed, lawful access);
* respect its own rate limit;
* return SourceRecords with a stable record id, a reference URL, an excerpt or
  locator, the retrieval timestamp and a content hash;
* raise SourceUnavailable on errors — never return [] for an outage.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..models import Identifier, IdentifierType, SourceRecord, SourceTier, SourceType, utcnow


class SourceUnavailable(RuntimeError):
    """The source could not be searched. Not a negative result; never cached."""


@dataclass
class RateLimiter:
    min_interval_s: float = 0.0
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._last: float | None = None

    def wait(self) -> None:
        if self.min_interval_s <= 0:
            return
        now = self.monotonic()
        if self._last is not None:
            delta = now - self._last
            if delta < self.min_interval_s:
                self.sleep(self.min_interval_s - delta)
        self._last = self.monotonic()


class SourceAdapter(ABC):
    source_id: str
    source_name: str
    source_type: SourceType
    tier: SourceTier
    jurisdictions: frozenset[str] = frozenset()        # empty = jurisdiction-agnostic
    supported_types: frozenset[IdentifierType] = frozenset()
    cost_per_request_usd: float = 0.0
    approved: bool = False                              # owner sign-off on terms/permission
    synthetic: bool = False
    scope: str = "business_identity"

    def __init__(self, rate_limiter: RateLimiter | None = None, clock: Callable[[], datetime] = utcnow):
        self.rate_limiter = rate_limiter or RateLimiter()
        self.clock = clock

    def supports(self, ident: Identifier) -> bool:
        return ident.type in self.supported_types

    def covers(self, jurisdiction: str | None) -> bool:
        return not self.jurisdictions or jurisdiction is None or jurisdiction in self.jurisdictions

    def search(self, ident: Identifier, jurisdiction: str | None) -> list[SourceRecord]:
        if not self.approved:
            raise SourceUnavailable(f"{self.source_id} is not approved for use")
        self.rate_limiter.wait()
        try:
            return self._search(ident, jurisdiction)
        except SourceUnavailable:
            raise
        except Exception as exc:  # adapters must not leak arbitrary errors into the engine
            raise SourceUnavailable(f"{self.source_id} failed: {type(exc).__name__}") from exc

    @abstractmethod
    def _search(self, ident: Identifier, jurisdiction: str | None) -> list[SourceRecord]:
        ...
