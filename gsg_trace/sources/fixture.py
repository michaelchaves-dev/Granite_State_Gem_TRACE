"""Synthetic fixture adapter.

This is the v0.1 "approved source": a local JSON file that mimics the shape of
a state business registry. It is always flagged `synthetic` and every report
built from it says so. Replace it with a real adapter only after the owner has
reviewed that source's terms of use (see docs/CURSOR_BUILD_SLICES.md, Slice 2).

Record fields (all optional except entity_id / legal_name):
  entity_id, legal_name, status, entity_type, registered_on, jurisdiction,
  trade_names[{name, registered_on, status}], prior_names[{name, changed_on}],
  phones[], addresses[], website, emails[], officers[{name, role}],
  registered_agent, links[{type, target, polarity}], note
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Identifier, IdentifierType, SourceRecord, SourceTier, SourceType, sha256_json
from ..normalize import (NormalizationError, name_match_key, normalize_address, normalize_domain,
                         normalize_email, normalize_phone, normalize_registry_id)
from .base import SourceAdapter


def _safe(fn, value) -> str | None:
    try:
        return fn(value)
    except (NormalizationError, ValueError, TypeError):
        return None


class FixtureAdapter(SourceAdapter):
    supported_types = frozenset({IdentifierType.BUSINESS_NAME, IdentifierType.PHONE, IdentifierType.DOMAIN,
                                 IdentifierType.EMAIL, IdentifierType.ADDRESS, IdentifierType.REGISTRY_ID})
    synthetic = True

    def __init__(self, data: dict[str, Any] | str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not isinstance(data, dict):
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        src = data["source"]
        self.source_id = src["source_id"]
        self.source_name = src["source_name"]
        if "synthetic" not in self.source_name.lower():
            self.source_name = f"{self.source_name} [SYNTHETIC]"
        self.source_type = SourceType(src.get("source_type", "official_registry"))
        self.tier = SourceTier(int(src.get("tier", 1)))
        self.jurisdictions = frozenset(j.upper() for j in src.get("jurisdictions", []))
        self.base_url = src.get("base_url", "https://registry.example.test/entity/")
        self.cost_per_request_usd = float(src.get("cost_per_request_usd", 0.0))
        self.approved = bool(src.get("approved", True))
        self.scope = src.get("scope", "business_identity")
        self.records: list[dict[str, Any]] = data.get("records", [])
        self._index: dict[tuple[str, str], list[int]] = {}
        for i, rec in enumerate(self.records):
            for key in self._keys_for(rec):
                self._index.setdefault(key, []).append(i)
        self.calls = 0

    def _keys_for(self, rec: dict[str, Any]) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        names = [rec.get("legal_name")] + [t.get("name") for t in rec.get("trade_names", [])] + \
                [p.get("name") for p in rec.get("prior_names", [])]
        for n in filter(None, names):
            if (k := _safe(name_match_key, n)):
                keys.add((IdentifierType.BUSINESS_NAME.value, k))
        for p in rec.get("phones", []):
            if (k := _safe(normalize_phone, p)):
                keys.add((IdentifierType.PHONE.value, k))
        for a in rec.get("addresses", []):
            if (k := _safe(normalize_address, a)):
                keys.add((IdentifierType.ADDRESS.value, k))
        for e in rec.get("emails", []):
            if (k := _safe(normalize_email, e)):
                keys.add((IdentifierType.EMAIL.value, k))
        if rec.get("website") and (k := _safe(normalize_domain, rec["website"])):
            keys.add((IdentifierType.DOMAIN.value, k))
        if rec.get("entity_id") and (k := _safe(normalize_registry_id, rec["entity_id"])):
            keys.add((IdentifierType.REGISTRY_ID.value, k))
        return keys

    def _lookup_key(self, ident: Identifier) -> tuple[str, str]:
        if ident.type is IdentifierType.BUSINESS_NAME:
            return (ident.type.value, name_match_key(ident.normalized))
        return (ident.type.value, ident.normalized)

    def _search(self, ident: Identifier, jurisdiction: str | None) -> list[SourceRecord]:
        self.calls += 1
        now = self.clock()
        out: list[SourceRecord] = []
        for i in self._index.get(self._lookup_key(ident), []):
            rec = self.records[i]
            rec_jur = (rec.get("jurisdiction") or next(iter(self.jurisdictions), None))
            if jurisdiction and rec_jur and rec_jur.upper() != jurisdiction:
                continue
            excerpt = "; ".join(f"{k}={rec[k]}" for k in ("legal_name", "status", "entity_type", "registered_on")
                                if rec.get(k))
            out.append(SourceRecord(
                source_id=self.source_id, source_name=self.source_name, source_type=self.source_type,
                tier=self.tier, record_id=str(rec.get("record_id", rec["entity_id"])),
                url=f"{self.base_url}{rec['entity_id']}", retrieved_at=now, entity_id=str(rec["entity_id"]),
                fields={k: v for k, v in rec.items() if k not in ("record_id",)}, excerpt=excerpt,
                content_hash=sha256_json(rec), event_date=rec.get("registered_on"),
                jurisdiction=rec_jur.upper() if rec_jur else None, origin_key=rec.get("origin_key"),
                synthetic=True))
        return out
