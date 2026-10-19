import copy
import logging

import pytest

from gsg_trace.engine import SearchRequest, identifier_inputs
from gsg_trace.models import RelationshipStatus as S
from gsg_trace.sources.base import SourceUnavailable
from gsg_trace.sources.fixture import FixtureAdapter

from conftest import adapter

PHONE = [("phone", "(603) 555-0101")]


def rels(res):
    return {frozenset((r.subject_entity.split(":")[1], r.related_entity.split(":")[1])): r.status
            for r in res.relationships}


def test_phone_search_end_to_end(make_engine, registry):
    res = make_engine([registry]).run(SearchRequest(PHONE, "NH"))
    assert res.stop_reason == "queue_exhausted"
    assert set(res.subject_keys) == {"synthetic-nh-sos:S-0900", "synthetic-nh-sos:S-1001", "synthetic-nh-sos:S-2002"}
    r = rels(res)
    assert r[frozenset(("S-0900", "S-1001"))] is S.VERIFIED_LEGAL
    assert r[frozenset(("S-1001", "S-2002"))] is S.UNRESOLVED        # shared phone only
    assert r[frozenset(("S-1001", "S-3003"))] is S.UNRELATED         # similar name, distinct registrations
    # No ownership asserted from a shared phone
    assert not any(x.status is S.VERIFIED_LEGAL and "S-2002" in x.related_entity + x.subject_entity
                   for x in res.relationships)
    assert all(e.reason for e in res.search_log)                     # every search records why


def test_entities_not_merged_on_matching_normalized_name(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("business_name", "granite peak roofing")], "NH"))
    assert set(res.subject_keys) == {"synthetic-nh-sos:S-1001", "synthetic-nh-sos:S-3003"}
    assert len(res.entities) == 2


def test_duplicate_inputs_searched_once(make_engine, registry):
    eng = make_engine([registry], max_depth=0)
    res = eng.run(SearchRequest([("phone", "(603) 555-0101"), ("phone", "603.555.0101"), ("phone", "+1 603 555 0101")], "NH"))
    assert registry.calls == 1 and len(res.preflight.identifiers) == 1
    assert len(res.preflight.identifiers[0]["originals"]) == 3


def test_depth_limit_enforced(make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest(PHONE, "NH"))
    assert res.metrics["max_depth_reached"] == 0 and registry.calls == 1
    assert "synthetic-nh-sos:S-4004" not in res.entities             # reachable only via a second phone hop
    assert "synthetic-nh-sos:S-3003" not in res.entities
    assert any(d["not_searched_because"] == "depth limit" for d in res.deferred_leads)


def test_request_limit_enforced(make_engine, registry):
    res = make_engine([registry], max_requests=2).run(SearchRequest(PHONE, "NH"))
    assert res.stop_reason == "max_requests" and registry.calls == 2
    assert any("search stopped" in d["not_searched_because"] for d in res.deferred_leads)


def test_time_limit_enforced(make_engine, registry, clock):
    original = registry._search

    def slow(ident, jur):
        clock.advance(seconds=3)
        return original(ident, jur)

    registry._search = slow
    res = make_engine([registry], max_seconds=5).run(SearchRequest(PHONE, "NH"))
    assert res.stop_reason == "max_seconds" and registry.calls <= 3


def test_branch_limit(make_engine, registry):
    res = make_engine([registry], max_branches_per_node=1).run(SearchRequest(PHONE, "NH"))
    assert any(d["not_searched_because"] == "branch limit" for d in res.deferred_leads)


def test_entity_limit(make_engine, registry):
    res = make_engine([registry], max_entities=2).run(SearchRequest(PHONE, "NH"))
    assert res.stop_reason == "max_entities" and len(res.entities) == 2


def test_cyclic_links_terminate(make_engine, clock):
    recs = [{"entity_id": "A", "legal_name": "Alpha Roofing LLC", "links": [{"type": "successor_of", "target": "B"}]},
            {"entity_id": "B", "legal_name": "Beta Roofing LLC", "links": [{"type": "predecessor_of", "target": "A"}]}]
    a = adapter("cyc", recs, clock=clock)
    res = make_engine([a], max_depth=5, max_requests=100).run(SearchRequest([("registry_id", "A")], "NH"))
    assert res.stop_reason == "queue_exhausted" and a.calls <= 4


def test_paid_source_excluded_at_zero_budget(make_engine, registry, clock):
    paid = adapter("paid", [], cost=0.05, clock=clock)
    plan, _, selected = make_engine([registry, paid]).preflight(SearchRequest(PHONE, "NH"))
    assert [a.source_id for a in selected] == ["synthetic-nh-sos"]
    assert any("paid source" in x["reason"] for x in plan.sources_excluded)


def test_unapproved_and_out_of_jurisdiction_sources_excluded(make_engine, registry, clock):
    unapproved = adapter("unapproved", [], approved=False, clock=clock)
    plan, _, _ = make_engine([registry, unapproved]).preflight(SearchRequest(PHONE, "MA"))
    reasons = {x["source_id"]: x["reason"] for x in plan.sources_excluded}
    assert "not approved" in reasons["unapproved"] and "jurisdiction" in reasons["synthetic-nh-sos"]
    assert not plan.can_proceed


def test_preflight_blocks_bad_input(make_engine, registry):
    res = make_engine([registry]).run(SearchRequest([("phone", "nope")], "NH"))
    assert res.stop_reason == "preflight_blocked" and registry.calls == 0


def test_second_run_served_from_cache(make_engine, registry):
    eng = make_engine([registry])
    first = eng.run(SearchRequest(PHONE, "NH"))
    calls = registry.calls
    plan, _, _ = eng.preflight(SearchRequest(PHONE, "NH"))
    assert plan.cached_pairs == 1 and plan.uncached_pairs == 0
    second = eng.run(SearchRequest(PHONE, "NH"))
    assert registry.calls == calls and second.metrics["external_requests"] == 0
    assert set(second.entities) == set(first.entities)
    assert second.metrics["cache"]["hit_rate"] > 0


def test_negative_result_cached_and_reported(make_engine, registry):
    eng = make_engine([registry], max_depth=0)
    res = eng.run(SearchRequest([("phone", "603-555-0199")], "NH"))
    assert res.negative_results and res.subject_keys == []
    eng.run(SearchRequest([("phone", "603-555-0199")], "NH"))
    assert registry.calls == 1 and eng.cache.metrics.negative_hits == 1


def test_source_error_is_not_cached_as_negative(make_engine, registry):
    def boom(ident, jur):
        raise RuntimeError("upstream 503")

    registry._search = boom
    eng = make_engine([registry], max_depth=0)
    res = eng.run(SearchRequest(PHONE, "NH"))
    assert res.sources_unavailable and not res.negative_results
    assert eng.cache.freshness("test")["entries"] == 0


def test_officers_are_never_search_seeds(make_engine, registry):
    res = make_engine([registry]).run(SearchRequest(PHONE, "NH"))
    searched = {e.identifier_key for e in res.search_log}
    assert not any("alex sample" in k for k in searched)


def test_logs_contain_no_raw_identifiers(make_engine, registry, caplog):
    caplog.set_level(logging.INFO, logger="gsg_trace")
    make_engine([registry]).run(SearchRequest(PHONE, "NH"))
    text = caplog.text
    assert "step=" in text and "5550101" not in text and "granite" not in text.lower()


def test_identifier_inputs_helper():
    assert identifier_inputs(phone=["a", "b"], business_name="x", email=None) == \
        [("phone", "a"), ("phone", "b"), ("business_name", "x")]
    with pytest.raises(ValueError):
        identifier_inputs(ssn="123")


def test_contradiction_across_sources(make_engine, registry, clock):
    directory = adapter("dir", [{"entity_id": "L1", "legal_name": "Seacoast Gutters listing",
                                 "phones": ["603-555-0122"],
                                 "links": [{"type": "successor_of", "target": "synthetic-nh-sos:S-4004"}]}],
                        tier=3, source_type="directory", clock=clock)
    court = adapter("court", [{"entity_id": "C1", "legal_name": "Docket C1", "phones": ["603-555-0122"],
                               "links": [{"from": "dir:L1", "type": "successor_of",
                                          "target": "synthetic-nh-sos:S-4004", "polarity": "denies"}]}],
                    tier=1, source_type="court", clock=clock)
    res = make_engine([registry, directory, court]).run(SearchRequest([("phone", "603-555-0122")], "NH"))
    pair = next(r for r in res.relationships
                if {r.subject_entity, r.related_entity} == {"dir:L1", "synthetic-nh-sos:S-4004"})
    assert pair.status is S.CONTRADICTED
    assert pair.supporting_records == ["dir:L1"] and pair.contradicting_records == ["court:C1"]


def test_low_tier_claim_alone_stays_unresolved(make_engine, registry, clock):
    directory = adapter("dir", [{"entity_id": "L1", "legal_name": "Seacoast Gutters listing",
                                 "phones": ["603-555-0122"],
                                 "links": [{"type": "successor_of", "target": "synthetic-nh-sos:S-4004"}]}],
                        tier=3, source_type="directory", clock=clock)
    res = make_engine([registry, directory]).run(SearchRequest([("phone", "603-555-0122")], "NH"))
    pair = next(r for r in res.relationships
                if {r.subject_entity, r.related_entity} == {"dir:L1", "synthetic-nh-sos:S-4004"})
    assert pair.status is S.UNRESOLVED
