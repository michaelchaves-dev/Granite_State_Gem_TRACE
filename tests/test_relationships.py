from gsg_trace.models import LinkAssertion, LinkPolarity, RelationshipStatus as S, SourceTier as T
from gsg_trace.relationships import classify


def la(a, b, t, tier=T.OFFICIAL, origin="o1", pol=LinkPolarity.ASSERTS, rec="r1"):
    return LinkAssertion(a, b, t, pol, rec, origin, tier, "SYNTHETIC src")


def test_tier1_legal_link_is_verified():
    rel = classify("A", "B", [la("A", "B", "successor_of")], [])
    assert rel.status is S.VERIFIED_LEGAL and rel.supporting_records == ["r1"]


def test_direction_independent():
    assert classify("B", "A", [la("A", "B", "merged_into")], []).status is S.VERIFIED_LEGAL


def test_operational_link_from_business_site_is_not_ownership():
    rel = classify("A", "B", [la("A", "B", "division_of", tier=T.BUSINESS_CONTROLLED)], [])
    assert rel.status is S.DOCUMENTED_OPERATIONAL
    assert "not legal ownership" in rel.basis


def test_shared_phone_is_only_unresolved():
    rel = classify("A", "B", [], ["business phone: +16035550101"])
    assert rel.status is S.UNRESOLVED
    assert "do not establish common ownership" in rel.basis


def test_repetition_does_not_upgrade():
    copies = [la("A", "B", "successor_of", tier=T.DIRECTORY_REVIEW, origin="press-release-1", rec=f"dir{i}")
              for i in range(10)]
    rel = classify("A", "B", copies, [])
    assert rel.status is S.UNRESOLVED and rel.independent_origins == 1


def test_many_independent_low_tier_claims_still_unresolved():
    claims = [la("A", "B", "successor_of", tier=T.DIRECTORY_REVIEW, origin=f"o{i}", rec=f"d{i}") for i in range(5)]
    rel = classify("A", "B", claims, [])
    assert rel.status is S.UNRESOLVED and rel.independent_origins == 5


def test_authoritative_denial_contradicts_lower_tier_claim():
    rel = classify("A", "B", [la("A", "B", "successor_of", tier=T.DIRECTORY_REVIEW, rec="dir1"),
                              la("A", "B", "successor_of", tier=T.OFFICIAL, pol=LinkPolarity.DENIES,
                                 origin="court-1", rec="court1")], [])
    assert rel.status is S.CONTRADICTED
    assert rel.contradicting_records == ["court1"] and rel.supporting_records == ["dir1"]


def test_lower_tier_denial_does_not_override_official_record_but_is_disclosed():
    rel = classify("A", "B", [la("A", "B", "successor_of"),
                              la("A", "B", "successor_of", tier=T.DIRECTORY_REVIEW, pol=LinkPolarity.DENIES,
                                 origin="dir", rec="dir1")], [])
    assert rel.status is S.VERIFIED_LEGAL and rel.contradicting_records == ["dir1"]
    assert "contradictory evidence" in rel.basis


def test_denial_with_shared_identifiers_is_contradicted():
    rel = classify("A", "B", [la("A", "B", "affiliate_of", pol=LinkPolarity.DENIES)], ["business phone: x"])
    assert rel.status is S.CONTRADICTED


def test_name_only_distinct_registered_entities_unrelated():
    rel = classify("A", "B", [], [], name_overlap=["legal name: granite peak roofing"], both_registered_distinct=True)
    assert rel.status is S.UNRELATED


def test_nothing_is_insufficient():
    assert classify("A", "B", [], []).status is S.INSUFFICIENT
    assert classify("A", "B", [], [], name_overlap=["legal name: x"]).status is S.INSUFFICIENT
