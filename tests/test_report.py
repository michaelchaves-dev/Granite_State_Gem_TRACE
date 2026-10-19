import json
import re

import pytest

from gsg_trace.engine import SearchRequest
from gsg_trace.models import EvidenceReceipt, ReviewStatus
from gsg_trace.receipts import build_receipts
from gsg_trace.report import BANNED_TERMS, DISCLAIMER, ReportLintError, lint_neutral_language, render_json, render_markdown

PHONE = [("phone", "(603) 555-0101")]


@pytest.fixture
def run(make_engine, registry):
    res = make_engine([registry]).run(SearchRequest(PHONE, "NH"))
    return res, build_receipts(res)


def test_report_has_all_sections(run):
    md = render_markdown(*run)
    for h in ["## A. Search summary", "## B. Business identity", "## C. Documented business relationships",
              "## D. Public business history", "## E. Customer experience records",
              "## F. Official public records", "## G. Unresolved information", "## H. Source register",
              "## I. Corrections and responses"]:
        assert h in md


def test_every_published_finding_is_cited_and_in_register(run):
    res, book = run
    md = render_markdown(res, book)
    assert book.published and book.citation_completeness == 1.0
    for r in book.published:
        assert r.citation_complete
        assert md.count(f"[{r.claim_id[:8]}]") >= 2 or r.claim_category.startswith("relationship.")  # body + register


def test_incomplete_citations_are_not_published(run):
    res, book = run
    book.receipts[0].source_url_or_reference = ""
    assert book.receipts[0] not in book.published and book.receipts[0] in book.incomplete
    assert "dropped because their citation was incomplete" in render_markdown(res, book)


def test_synthetic_data_is_labelled(run):
    md = render_markdown(*run)
    assert "synthetic test data" in md.lower()
    assert all(r.synthetic_source and "SYNTHETIC" in r.limitations[0] for r in run[1].published)


def test_untested_sections_say_not_searched(run):
    md = render_markdown(*run)
    assert "Not searched: no approved review or complaint source" in md
    assert "Not searched: no approved licensing" in md


def test_neutral_language(run):
    md = render_markdown(*run)
    assert lint_neutral_language(md) == []
    assert lint_neutral_language(DISCLAIMER) == []


def test_lint_ignores_quoted_source_content_but_catches_authored_terms():
    assert lint_neutral_language("Record lists the legal name “Fraud Busters LLC”.") == []
    assert lint_neutral_language("This contractor is a scammer.") == ["scammer"]


def test_banned_term_in_template_raises(run, monkeypatch):
    import gsg_trace.report as rep
    monkeypatch.setattr(rep, "DISCLAIMER", "Beware: likely fraud.")
    with pytest.raises(ReportLintError):
        rep.render_markdown(*run)


def test_adverse_status_withheld_for_review(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("registry_id", "S-4004")], "NH"))
    book = build_receipts(res)
    status = [r for r in book.receipts if r.claim_category == "registration_status_adverse"]
    assert status and all(r.review_status == ReviewStatus.PENDING.value for r in status)
    md = render_markdown(res, book)
    assert "Administratively Dissolved" not in md
    assert "withheld pending human review" in md


def test_prompt_injection_text_is_inert(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("registry_id", "S-4004")], "NH"))
    book = build_receipts(res)
    assert all(r.review_status == "pending" for r in book.receipts)       # flagged record held back
    assert any("instruction-like" in l for r in book.receipts for l in r.limitations)
    md = render_markdown(res, book)
    assert "Ignore previous instructions" not in md and "verified and trusted" not in md


def test_markdown_injection_escaped(make_engine, clock):
    from conftest import adapter
    a = adapter("x", [{"entity_id": "E1", "legal_name": "Evil](javascript:alert(1)) <script>x</script> LLC",
                       "phones": ["603-555-0150"]}], clock=clock)
    res = make_engine([a], max_depth=0).run(SearchRequest([("phone", "603-555-0150")], "NH"))
    md = render_markdown(res)
    assert "<script>" not in md and "](javascript:" not in md


def test_negative_result_wording(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("phone", "603-555-0199")], "NH"))
    md = render_markdown(res)
    assert "does not establish that no such business or record exists" in md
    assert "No record matching the supplied identifiers was found in the sources checked" in md


def test_ambiguity_disclosed(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("business_name", "Granite Peak Roofing")], "NH"))
    assert "separate records matched the search" in render_markdown(res)


def test_shared_identifier_caveat_present(run):
    md = render_markdown(*run)
    assert "do not establish common ownership" in md


def test_json_report_roundtrips(run):
    data = json.loads(render_json(*run))
    assert data["receipts"]["citation_completeness"] == 1.0
    assert data["run"]["stop_reason"] == "queue_exhausted"
    r = data["receipts"]["published"][0]
    for f in EvidenceReceipt.REQUIRED_CITATION_FIELDS + ("claim_id", "subject_business_id", "verification_status",
                                                        "review_status", "correction_history", "contradictory_evidence"):
        assert f in r
