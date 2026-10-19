"""Evidence receipts and the publication gate (spec §10, §11).

Each finding is a narrow claim about what a specific record states — never a
characterization of a person. A receipt is publishable only when:
  * every citation field is present, and
  * review_status == approved.
Material adverse findings and anything with suspected instruction-injection
content are held at `pending` for human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .engine import DiscoveryResult
from .models import (EvidenceReceipt, RelationshipStatus, ReviewStatus, SourceRecord, SourceTier,
                     VerificationStatus, iso, new_id)
from .sanitize import QUOTE_CLOSE, QUOTE_OPEN, clean_untrusted, looks_like_injection

ADVERSE_STATUSES = {"revoked", "suspended", "administratively dissolved", "admin dissolved", "forfeited",
                    "not in good standing", "delinquent", "terminated by state"}
ADVERSE_CATEGORIES = {"registration_status_adverse", "complaint", "enforcement", "court", "lien"}


def q(value: object) -> str:
    return f"{QUOTE_OPEN}{clean_untrusted(value, 200)}{QUOTE_CLOSE}"


def _verification(rec: SourceRecord) -> VerificationStatus:
    if rec.source_type.value == "review":
        return VerificationStatus.ALLEGATION
    if rec.tier == SourceTier.OFFICIAL:
        return VerificationStatus.VERIFIED
    return VerificationStatus.SOURCE_REPORTED


def _limitations(rec: SourceRecord) -> list[str]:
    lim = [f"Reflects {rec.source_name} as retrieved {iso(rec.retrieved_at)}; the record may have changed since."]
    if rec.synthetic:
        lim.insert(0, "SYNTHETIC test record — not a real public record. Do not rely on it.")
    if rec.tier > SourceTier.OFFICIAL:
        lim.append("Not an authoritative government record.")
    blob = " ".join(str(v) for v in rec.fields.values()) + " " + rec.excerpt
    if looks_like_injection(blob):
        lim.append("Record contains instruction-like text; treated strictly as data and held for review.")
    return lim


@dataclass
class ReceiptBook:
    receipts: list[EvidenceReceipt] = field(default_factory=list)

    @property
    def published(self) -> list[EvidenceReceipt]:
        return [r for r in self.receipts if r.citation_complete and r.review_status == ReviewStatus.APPROVED.value]

    @property
    def pending_review(self) -> list[EvidenceReceipt]:
        return [r for r in self.receipts if r.citation_complete and r.review_status == ReviewStatus.PENDING.value]

    @property
    def incomplete(self) -> list[EvidenceReceipt]:
        return [r for r in self.receipts if not r.citation_complete]

    @property
    def citation_completeness(self) -> float:
        return (len(self.receipts) - len(self.incomplete)) / len(self.receipts) if self.receipts else 1.0

    def for_subject(self, key: str, category_prefix: str = "") -> list[EvidenceReceipt]:
        return [r for r in self.published if r.subject_business_id == key and r.claim_category.startswith(category_prefix)]


def _receipt(subject: str, rec: SourceRecord, claim: str, category: str, locator: str, scope: str,
             relationship_status: str | None = None, contradictory: list[str] | None = None) -> EvidenceReceipt:
    lim = _limitations(rec)
    injection = any("instruction-like" in x for x in lim)
    adverse = category in ADVERSE_CATEGORIES
    approve = rec.tier == SourceTier.OFFICIAL and not adverse and not injection
    r = EvidenceReceipt(
        claim_id=new_id(), subject_business_id=subject, claim_text=claim, claim_category=category,
        source_type=rec.source_type.value, source_name=rec.source_name, source_url_or_reference=rec.url,
        source_record_identifier=rec.record_id, observed_at_utc=iso(rec.retrieved_at) or "",
        source_event_date=rec.event_date, relevant_excerpt_or_locator=f"{locator} | {clean_untrusted(rec.excerpt, 240)}",
        jurisdiction=rec.jurisdiction, search_scope=scope, relationship_status=relationship_status,
        verification_status=_verification(rec).value, limitations=lim,
        contradictory_evidence=contradictory or [],
        review_status=(ReviewStatus.APPROVED if approve else ReviewStatus.PENDING).value,
        content_hash=rec.content_hash, synthetic_source=rec.synthetic)
    if adverse:
        r.limitations.append("Potentially adverse finding: requires human review before publication.")
    return r


def identity_receipts(subject: str, rec: SourceRecord, scope: str) -> list[EvidenceReceipt]:
    f = rec.fields
    who = f"{rec.source_name} record {rec.record_id}"
    out: list[EvidenceReceipt] = []

    def add(claim: str, category: str, locator: str) -> None:
        out.append(_receipt(subject, rec, claim, category, locator, scope))

    if f.get("legal_name"):
        add(f"{who} lists the legal name {q(f['legal_name'])}.", "identity.legal_name", "field: legal_name")
    if f.get("entity_type"):
        add(f"{who} lists the entity type {q(f['entity_type'])}.", "identity.entity_type", "field: entity_type")
    if f.get("status"):
        status = clean_untrusted(f["status"]).casefold()
        cat = "registration_status_adverse" if status in ADVERSE_STATUSES else "history.status"
        add(f"{who} lists the registration status {q(f['status'])}.", cat, "field: status")
    if f.get("registered_on"):
        add(f"{who} lists a registration date of {q(f['registered_on'])}.", "history.registered_on",
            "field: registered_on")
    for t in f.get("trade_names") or []:
        if isinstance(t, dict) and t.get("name"):
            when = f" registered {q(t['registered_on'])}" if t.get("registered_on") else ""
            st = f" with status {q(t['status'])}" if t.get("status") else ""
            add(f"{who} lists the trade name {q(t['name'])}{when}{st}.", "identity.trade_name", "field: trade_names")
    for p in f.get("prior_names") or []:
        if isinstance(p, dict) and p.get("name"):
            when = f", changed {q(p['changed_on'])}" if p.get("changed_on") else ""
            add(f"{who} lists the prior name {q(p['name'])}{when}. A name change is a routine filing and does "
                "not by itself indicate anything about the business.", "history.prior_name", "field: prior_names")
    for ph in f.get("phones") or []:
        add(f"{who} lists the business phone {q(ph)}.", "identity.phone", "field: phones")
    for ad in f.get("addresses") or []:
        add(f"{who} lists the business address {q(ad)}.", "identity.address", "field: addresses")
    for em in f.get("emails") or []:
        add(f"{who} lists the business email {q(em)}.", "identity.email", "field: emails")
    if f.get("website"):
        add(f"{who} lists the website {q(f['website'])}.", "identity.website", "field: website")
    if f.get("registered_agent"):
        add(f"{who} lists the registered agent {q(f['registered_agent'])}.", "identity.registered_agent",
            "field: registered_agent")
    for o in f.get("officers") or []:
        if isinstance(o, dict) and o.get("name"):
            role = f" as {q(o['role'])}" if o.get("role") else ""
            add(f"{who} lists {q(o['name'])}{role}.", "identity.listed_person", "field: officers")
    return out


def relationship_receipts(res: DiscoveryResult, scope: str) -> list[EvidenceReceipt]:
    by_key = {r.record_key: r for recs in res.entities.values() for r in recs}
    out: list[EvidenceReceipt] = []
    for rel in res.relationships:
        if rel.status == RelationshipStatus.INSUFFICIENT and not rel.shared_identifiers:
            continue
        cite_keys = rel.supporting_records or rel.contradicting_records
        rec = by_key.get(cite_keys[0]) if cite_keys else None
        if rec is None:  # shared-identifier relationships cite the related entity's own record
            recs = res.entities.get(rel.related_entity) or res.entities.get(rel.subject_entity) or []
            rec = recs[0] if recs else None
        if rec is None:
            continue
        claim = (f"Relationship between {q(rel.subject_entity)} and {q(rel.related_entity)}: "
                 f"{rel.status.value.replace('_', ' ')}. {rel.basis}")
        r = _receipt(rel.subject_entity, rec, claim, f"relationship.{rel.status.value}",
                     f"links/identifiers on {rec.record_key}", scope, rel.status.value, rel.contradicting_records)
        # Only authoritative legal links and determinations from official records auto-approve.
        if rel.status in (RelationshipStatus.DOCUMENTED_OPERATIONAL,) and rec.tier > SourceTier.OFFICIAL:
            r.review_status = ReviewStatus.PENDING.value
        out.append(r)
    return out


def build_receipts(res: DiscoveryResult) -> ReceiptBook:
    scope = res.preflight.scope
    book = ReceiptBook()
    for subject in res.subject_keys:
        for rec in res.entities.get(subject, []):
            book.receipts.extend(identity_receipts(subject, rec, scope))
    # Identity facts for verified/operationally related entities, so history is citable.
    related = {r.related_entity for r in res.relationships
               if r.status in (RelationshipStatus.VERIFIED_LEGAL, RelationshipStatus.DOCUMENTED_OPERATIONAL)}
    for key in sorted(related - set(res.subject_keys)):
        for rec in res.entities.get(key, []):
            book.receipts.extend(identity_receipts(key, rec, scope))
    book.receipts.extend(relationship_receipts(res, scope))
    return book
