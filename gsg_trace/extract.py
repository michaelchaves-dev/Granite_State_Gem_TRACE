"""Extract business identifiers and link assertions from source records (spec §5, steps 5-6).

Policy encoded here:
* People (officers, managers, agents) are recorded as attributes of a business
  record but are NEVER turned into search seeds. Spec §7: associates and
  relatives are not investigated by default.
* Registered agents and addresses are commonly shared by unrelated businesses,
  so they get low relevance and are not expanded under default limits.
* Identifiers discovered on lower-tier sources are discounted.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Identifier, IdentifierType, LinkAssertion, LinkPolarity, SourceRecord, SourceTier
from .normalize import NormalizationError, name_match_key, normalize_identifier

# Base relevance by identifier type when found on a Tier-1 record.
BASE_RELEVANCE = {
    IdentifierType.REGISTRY_ID: 1.0,
    IdentifierType.PHONE: 0.8,
    IdentifierType.DOMAIN: 0.8,
    IdentifierType.EMAIL: 0.7,
    IdentifierType.BUSINESS_NAME: 0.7,
    IdentifierType.ADDRESS: 0.4,
    IdentifierType.SOCIAL_PROFILE: 0.6,
}
TIER_FACTOR = {SourceTier.OFFICIAL: 1.0, SourceTier.BUSINESS_CONTROLLED: 0.9,
               SourceTier.DIRECTORY_REVIEW: 0.5, SourceTier.HISTORICAL: 0.5}


@dataclass(frozen=True)
class DiscoveredIdentifier:
    identifier: Identifier
    relevance: float
    reason: str


def _ident(id_type: IdentifierType, raw: object) -> Identifier | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return normalize_identifier(id_type, raw)
    except (NormalizationError, ValueError):
        return None


def record_identifiers(rec: SourceRecord) -> list[tuple[Identifier, str]]:
    """All business identifiers a record states, with the field they came from."""
    f = rec.fields
    found: list[tuple[Identifier, str]] = []

    def add(id_type: IdentifierType, raw: object, label: str) -> None:
        ident = _ident(id_type, raw)
        if ident:
            found.append((ident, label))

    add(IdentifierType.BUSINESS_NAME, f.get("legal_name"), "legal name")
    for t in f.get("trade_names", []) or []:
        add(IdentifierType.BUSINESS_NAME, (t or {}).get("name"), "trade name")
    for p in f.get("prior_names", []) or []:
        add(IdentifierType.BUSINESS_NAME, (p or {}).get("name"), "prior name")
    for p in f.get("phones", []) or []:
        add(IdentifierType.PHONE, p, "business phone")
    for a in f.get("addresses", []) or []:
        add(IdentifierType.ADDRESS, a, "business address")
    for e in f.get("emails", []) or []:
        add(IdentifierType.EMAIL, e, "business email")
    add(IdentifierType.DOMAIN, f.get("website"), "website")
    return found


def link_assertions(rec: SourceRecord) -> list[LinkAssertion]:
    if not rec.entity_key:
        return []
    out = []
    for link in rec.fields.get("links", []) or []:
        target = str((link or {}).get("target", "")).strip()
        link_type = str((link or {}).get("type", "")).strip().lower()
        if not target or not link_type:
            continue
        to_entity = target if ":" in target else f"{rec.source_id}:{target}"
        # A record may state a link between two *other* entities (e.g. a court finding).
        subject = str((link or {}).get("from", "")).strip()
        from_entity = (subject if ":" in subject else f"{rec.source_id}:{subject}") if subject else rec.entity_key
        polarity = LinkPolarity((link.get("polarity") or "asserts").lower())
        out.append(LinkAssertion(from_entity, to_entity, link_type, polarity, rec.record_key,
                                 rec.origin, rec.tier, rec.source_name))
    return out


def discover(rec: SourceRecord) -> list[DiscoveredIdentifier]:
    """Identifiers worth considering for recursive search, scored for relevance."""
    factor = TIER_FACTOR[rec.tier]
    where = f"{rec.source_name} record {rec.record_id}"
    out = [DiscoveredIdentifier(ident, round(BASE_RELEVANCE[ident.type] * factor, 3), f"{label} on {where}")
           for ident, label in record_identifiers(rec)]
    for la in link_assertions(rec):
        if (la.polarity is LinkPolarity.ASSERTS and la.from_entity == rec.entity_key
                and la.to_entity.startswith(rec.source_id + ":")):
            reg = _ident(IdentifierType.REGISTRY_ID, la.to_entity.split(":", 1)[1])
            if reg:
                out.append(DiscoveredIdentifier(reg, round(BASE_RELEVANCE[reg.type] * factor, 3),
                                                f"'{la.link_type}' link on {where}"))
    return out


def shared_identifiers(a: list[SourceRecord], b: list[SourceRecord]) -> list[str]:
    """Overlap between two entities' stated identifiers. Overlap is a lead, never proof."""
    def bag(records: list[SourceRecord]) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in records:
            for ident, label in record_identifiers(r):
                key = ident.key
                if ident.type is IdentifierType.BUSINESS_NAME:
                    key = f"name:{name_match_key(ident.normalized)}"
                out.setdefault(key, label)
            # Registered agents are deliberately excluded: commercial agent services represent
            # thousands of unrelated businesses, so a shared agent is noise, not a lead.
            for o in r.fields.get("officers", []) or []:
                name = (o or {}).get("name")
                if isinstance(name, str) and name.strip():
                    out.setdefault(f"listed_person:{' '.join(name.casefold().split())}", "listed officer/manager name")
        return out

    ba, bb = bag(a), bag(b)
    return sorted(f"{ba[k]}: {k.split(':', 1)[1]}" for k in ba.keys() & bb.keys())
