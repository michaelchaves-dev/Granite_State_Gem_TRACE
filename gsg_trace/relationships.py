"""Relationship classification (spec §6).

Decision order for a pair of entities (A, B):

1. Gather link assertions between A and B (either direction).
2. Corroboration is counted by distinct *origin*, never by copies: ten
   directory listings repeating one claim are one origin.
3. A denial at least as authoritative as the best support  -> CONTRADICTED.
4. A Tier-1 assertion of a legal link type                  -> VERIFIED_LEGAL.
5. A Tier-1/2 assertion of an operational link type         -> DOCUMENTED_OPERATIONAL.
6. Any other assertion (Tier 3/4, or unknown type)          -> UNRESOLVED.
   Lower-tier claims stay unresolved no matter how often they are repeated.
7. No assertions:
   * shared identifiers (phone, address, agent, person name, ...)
       -> UNRESOLVED, explicitly "not evidence of common ownership";
       -> CONTRADICTED if an authoritative record denies the link.
   * distinct registered entities whose only overlap is a similar name
       -> UNRELATED (the records concern different entities).
   * otherwise -> INSUFFICIENT.
"""
from __future__ import annotations

from .models import (LEGAL_LINK_TYPES, OPERATIONAL_LINK_TYPES, LinkAssertion, LinkPolarity, Relationship,
                     RelationshipStatus, SourceRecord, SourceTier)

_NOT_PROOF = "Shared identifiers are leads only; they do not establish common ownership or control."


def _pair(assertions: list[LinkAssertion], a: str, b: str) -> list[LinkAssertion]:
    return [x for x in assertions if {x.from_entity, x.to_entity} == {a, b}]


def classify(a: str, b: str, assertions: list[LinkAssertion], shared: list[str],
             name_overlap: list[str] | None = None, both_registered_distinct: bool = False) -> Relationship:
    """`shared` = non-name identifier overlap; `name_overlap` = similar-name overlap (weakest signal)."""
    name_overlap = list(name_overlap or [])
    pair = _pair(assertions, a, b)
    support = [x for x in pair if x.polarity is LinkPolarity.ASSERTS]
    denial = [x for x in pair if x.polarity is LinkPolarity.DENIES]
    support_origins = {x.origin for x in support}
    rel = Relationship(a, b, RelationshipStatus.INSUFFICIENT, "", shared_identifiers=list(shared) + name_overlap,
                       independent_origins=len(support_origins),
                       link_types=sorted({x.link_type for x in support}),
                       supporting_records=sorted({x.record_key for x in support}),
                       contradicting_records=sorted({x.record_key for x in denial}))

    best_support = min((x.tier for x in support), default=None)
    best_denial = min((x.tier for x in denial), default=None)

    if denial and (best_support is None or best_denial <= best_support):
        if support or shared:
            rel.status = RelationshipStatus.CONTRADICTED
            rel.basis = (f"A {SourceTier(best_denial).name.lower()}-tier record denies this link"
                         + (f", conflicting with {len(support_origins)} supporting origin(s)." if support
                            else "; the only proposed connection was shared identifiers."))
        else:
            rel.status = RelationshipStatus.UNRELATED
            rel.basis = "An authoritative record states these entities are not linked."
        return rel

    if support:
        legal_t1 = [x for x in support if x.tier == SourceTier.OFFICIAL and x.link_type in LEGAL_LINK_TYPES]
        oper = [x for x in support if x.tier <= SourceTier.BUSINESS_CONTROLLED and
                (x.link_type in OPERATIONAL_LINK_TYPES or x.link_type in LEGAL_LINK_TYPES)]
        caveat = (f" A lower-tier denial exists ({', '.join(rel.contradicting_records)}) and is listed as "
                  "contradictory evidence." if denial else "")
        if legal_t1:
            rel.status = RelationshipStatus.VERIFIED_LEGAL
            rel.basis = (f"Official record(s) state a '{legal_t1[0].link_type}' link "
                         f"({', '.join(sorted({x.record_key for x in legal_t1}))})." + caveat)
        elif oper:
            rel.status = RelationshipStatus.DOCUMENTED_OPERATIONAL
            rel.basis = (f"{oper[0].source_name} states a '{oper[0].link_type}' connection. "
                         "This documents an operational connection, not legal ownership." + caveat)
        else:
            rel.status = RelationshipStatus.UNRESOLVED
            rel.basis = (f"Only lower-tier or non-legal claims support this link "
                         f"({len(support_origins)} independent origin(s) across {len(support)} copies). "
                         "Repetition does not verify a relationship.")
        return rel

    if shared:
        rel.status = RelationshipStatus.UNRESOLVED
        rel.basis = f"Records share: {'; '.join(shared)}. {_NOT_PROOF}"
    elif name_overlap and both_registered_distinct:
        rel.status = RelationshipStatus.UNRELATED
        rel.basis = ("Distinct registered entities with separate registry identifiers; the only overlap is "
                     "a similar name, which does not indicate a connection.")
    elif name_overlap:
        rel.status = RelationshipStatus.INSUFFICIENT
        rel.basis = "Only a similar name connects these records; that cannot support a determination."
    else:
        rel.status = RelationshipStatus.INSUFFICIENT
        rel.basis = "No documented link or shared identifier was found in the sources checked."
    return rel


def entity_is_registered(records: list[SourceRecord]) -> bool:
    return any(r.tier == SourceTier.OFFICIAL and r.entity_id for r in records)
