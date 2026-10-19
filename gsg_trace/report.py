"""Neutral, source-cited report (spec §12, sections A–I).

Engine-authored wording is fixed template text. Everything that came from a
source is cleaned, markdown-escaped and wrapped in curly quotes, which lets
`lint_neutral_language` check only the wording TRACE itself wrote.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .engine import DiscoveryResult
from .models import RelationshipStatus
from .receipts import ReceiptBook, build_receipts
from .sanitize import QUOTE_CLOSE, QUOTE_OPEN, md_escape, src

BANNED_TERMS = ("scam", "scammer", "fraud", "fraudulent", "predator", "predatory", "crook", "dishonest",
                "untrustworthy", "trustworthy", "shady", "con artist", "rip-off", "ripoff", "guilty", "criminal",
                "liar", "thief", "red flag", "avoid this", "do not hire", "beware")

DISCLAIMER = (
    "TRACE reports what the records checked in this search state, with a citation for each finding. "
    "It does not evaluate character, assign blame, or recommend whether to hire anyone. "
    "Allegations are labelled as allegations. A missing record means only that nothing was found in the "
    "sources checked at the time of the search. You make the decision."
)


class ReportLintError(ValueError):
    pass


def lint_neutral_language(markdown: str) -> list[str]:
    """Return banned terms found in engine-authored text (quoted source content is excluded)."""
    authored = re.sub(f"{QUOTE_OPEN}[^{QUOTE_CLOSE}]*{QUOTE_CLOSE}", " ", markdown).casefold()
    return [t for t in BANNED_TERMS if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", authored)]


def _label(res: DiscoveryResult, key: str) -> str:
    recs = res.entities.get(key) or []
    name = next((r.fields.get("legal_name") for r in recs if r.fields.get("legal_name")), None)
    return f"{src(name)} (`{md_escape(key)}`)" if name else f"`{md_escape(key)}`"


def _cite(r) -> str:
    return f"[{r.claim_id[:8]}]"


def _row(claim: str, receipt) -> str:
    return f"- {md_escape(claim)} {_cite(receipt)}"


def _source_line(res: DiscoveryResult, sid: str) -> str:
    sel = {s["source_id"]: s for s in res.preflight.sources_selected}
    s = sel.get(sid, {"source_name": sid, "tier": "?", "synthetic": False})
    syn = " — **SYNTHETIC TEST DATA**" if s.get("synthetic") else ""
    return f"- {src(s['source_name'])} (`{md_escape(sid)}`, tier {s['tier']}){syn}"


def render_markdown(res: DiscoveryResult, book: ReceiptBook | None = None) -> str:
    book = book or build_receipts(res)
    L: list[str] = []
    synthetic = any(s.get("synthetic") for s in res.preflight.sources_selected)
    L.append("# GSG TRACE — Contractor Identity Report")
    if synthetic:
        L.append("\n> **Built from synthetic test data.** Nothing in this report describes a real business.")
    L.append(f"\n_{DISCLAIMER}_\n")

    # A ---------------------------------------------------------------
    L.append("## A. Search summary")
    seeds = ", ".join(f"{i['type']} {src(o)}" for i in res.preflight.identifiers for o in i["originals"]) or "none"
    L.append(f"- Searched for: {seeds}")
    L.append(f"- Jurisdiction: {res.preflight.jurisdiction or 'not specified'}")
    L.append(f"- Search started: {res.started_at}; finished: {res.finished_at}")
    L.append(f"- Scope: {md_escape(res.preflight.scope)}; objective: {md_escape(res.preflight.objective)}")
    L.append(f"- Search ended because: {res.stop_reason.replace('_', ' ')}")
    m = res.metrics
    if m:
        L.append(f"- External requests: {m['external_requests']}; cache hits: "
                 f"{m['cache_hits_positive'] + m['cache_hits_negative']}; estimated cost: ${m['estimated_cost_usd']:.2f}; "
                 f"entities found: {m['entities_found']}; max depth reached: {m['max_depth_reached']}")
    L.append("\n**Sources checked**")
    L.extend(_source_line(res, s) for s in res.sources_checked) if res.sources_checked else L.append("- None")
    if res.sources_unavailable:
        L.append("\n**Sources unavailable during this search** (not searched; absence of results from them means nothing)")
        L.extend(f"- `{md_escape(u['source_id'])}`: {src(u['error'])}" for u in res.sources_unavailable)
    if res.preflight.sources_excluded:
        L.append("\n**Sources not used**")
        L.extend(f"- `{md_escape(x['source_id'])}`: {md_escape(x['reason'])}" for x in res.preflight.sources_excluded)
    if res.preflight.rejected_inputs:
        L.append("\n**Inputs rejected by validation**")
        L.extend(f"- {md_escape(r['type'])} {src(r['input'])}: {md_escape(r['error'])}" for r in res.preflight.rejected_inputs)

    # B ---------------------------------------------------------------
    L.append("\n## B. Business identity")
    if not res.subject_keys:
        L.append("- No record matching the supplied identifiers was found in the sources checked.")
    if len(res.subject_keys) > 1:
        L.append(f"- **{len(res.subject_keys)} separate records matched the search.** TRACE did not merge them; "
                 "confirm which one is the business you are dealing with (for example by registry ID).")
    for key in res.subject_keys:
        L.append(f"\n### {_label(res, key)}")
        rows = book.for_subject(key, "identity.")
        L.extend(_row(r.claim_text, r) for r in rows) if rows else L.append("- No publishable identity findings.")

    # C ---------------------------------------------------------------
    L.append("\n## C. Documented business relationships")
    shown = [r for r in res.relationships if r.status in (RelationshipStatus.VERIFIED_LEGAL,
                                                           RelationshipStatus.DOCUMENTED_OPERATIONAL,
                                                           RelationshipStatus.CONTRADICTED,
                                                           RelationshipStatus.UNRELATED)]
    rel_receipts = {(r.subject_business_id, r.claim_text): r for r in book.published
                    if r.claim_category.startswith("relationship.")}
    if not shown:
        L.append("- No documented relationships were established in the sources checked.")
    for rel in shown:
        rec = next((r for (s, t), r in rel_receipts.items() if s == rel.subject_entity and rel.related_entity in t), None)
        cite = f" {_cite(rec)}" if rec else " _(pending review)_"
        L.append(f"- {_label(res, rel.subject_entity)} ↔ {_label(res, rel.related_entity)} — "
                 f"**{rel.status.value.replace('_', ' ')}**"
                 + (f" ({', '.join(md_escape(t) for t in rel.link_types)})" if rel.link_types else "")
                 + f". {md_escape(rel.basis)}{cite}")

    # D ---------------------------------------------------------------
    L.append("\n## D. Public business history")
    hist = [r for r in book.published if r.claim_category.startswith("history.")]
    L.extend(_row(r.claim_text, r) for r in hist) if hist else L.append("- No dated history records found.")

    # E / F -------------------------------------------------------------
    L.append("\n## E. Customer experience records")
    L.append("- Not searched: no approved review or complaint source is configured in this version. "
             "This section says nothing about the business's customer history.")
    L.append("\n## F. Official public records (licensing, permits, court, enforcement)")
    L.append("- Not searched: no approved licensing, permit, court or enforcement source is configured in this version.")

    # G ---------------------------------------------------------------
    L.append("\n## G. Unresolved information")
    unresolved = [r for r in res.relationships if r.status in (RelationshipStatus.UNRESOLVED, RelationshipStatus.INSUFFICIENT)
                  and (r.shared_identifiers or r.supporting_records)]
    for rel in unresolved:
        L.append(f"- {_label(res, rel.subject_entity)} ↔ {_label(res, rel.related_entity)} — "
                 f"**{rel.status.value.replace('_', ' ')}**. {md_escape(rel.basis)}")
    for n in res.negative_results:
        L.append(f"- No matching record for `{md_escape(n['identifier'])}` in {src(n['source_name'])} as of {n['as_of']}. "
                 "This does not establish that no such business or record exists.")
    if res.deferred_leads:
        L.append(f"- {len(res.deferred_leads)} identifier(s) found on records were not searched further "
                 f"(reasons: {', '.join(sorted({md_escape(d['not_searched_because']) for d in res.deferred_leads}))}).")
    if book.pending_review:
        L.append(f"- {len(book.pending_review)} finding(s) are withheld pending human review.")
    if book.incomplete:
        L.append(f"- {len(book.incomplete)} finding(s) were dropped because their citation was incomplete.")
    if len(L) and L[-1] == "\n## G. Unresolved information":
        L.append("- None recorded.")

    # H ---------------------------------------------------------------
    L.append("\n## H. Source register")
    L.append("| Ref | Source | Record | Reference | Retrieved (UTC) | Verification |")
    L.append("|---|---|---|---|---|---|")
    for r in book.published:
        L.append(f"| {_cite(r)} | {md_escape(r.source_name)} | {md_escape(r.source_record_identifier)} | "
                 f"{md_escape(r.source_url_or_reference)} | {r.observed_at_utc} | {r.verification_status} |")

    # I ---------------------------------------------------------------
    L.append("\n## I. Corrections and responses")
    corrections = [r for r in book.receipts if r.correction_history]
    if corrections:
        for r in corrections:
            L.append(f"- {_cite(r)}: {len(r.correction_history)} correction(s) recorded.")
    else:
        L.append("- No corrections or business responses are on file for this search.")
    L.append("- Businesses can request a correction; corrected findings are re-verified against the source.")

    md = "\n".join(L) + "\n"
    found = lint_neutral_language(md)
    if found:
        raise ReportLintError(f"report wording failed neutral-language lint: {found}")
    return md


def render_json(res: DiscoveryResult, book: ReceiptBook | None = None) -> str:
    book = book or build_receipts(res)
    payload: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "run": res.to_dict(),
        "receipts": {
            "published": [r.to_dict() for r in book.published],
            "pending_review": [r.to_dict() for r in book.pending_review],
            "dropped_incomplete": [r.to_dict() for r in book.incomplete],
            "citation_completeness": round(book.citation_completeness, 4),
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
