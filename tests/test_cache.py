import os
import stat
from datetime import timedelta

import pytest

from gsg_trace.cache import SearchCache
from gsg_trace.models import SourceRecord, SourceTier, SourceType
from gsg_trace.normalize import normalize_identifier


def rec(clock):
    return SourceRecord("src", "SYNTHETIC src", SourceType.OFFICIAL_REGISTRY, SourceTier.OFFICIAL, "R1",
                        "https://x.example.test/R1", clock(), "E1", {"legal_name": "X LLC"}, "legal_name=X LLC",
                        "h", synthetic=True)


def test_positive_cache_roundtrip(cache, clock):
    ident = normalize_identifier("phone", "603-555-0101")
    cache.put("ns", "src", ident, "NH", "business_identity", [rec(clock)])
    hit = cache.get("ns", "src", ident, "NH", "business_identity")
    assert hit.status == "positive" and hit.records[0].record_id == "R1"
    assert cache.metrics.positive_hits == 1


def test_negative_cache_is_scoped_and_expires_sooner(cache, clock):
    ident = normalize_identifier("phone", "603-555-0199")
    cache.put("ns", "src", ident, "NH", "business_identity", [])
    hit = cache.get("ns", "src", ident, "NH", "business_identity")
    assert hit.status == "negative" and hit.records == []
    # scope and jurisdiction are part of the key
    assert cache.get("ns", "src", ident, "MA", "business_identity") is None
    assert cache.get("ns", "src", ident, "NH", "licensing") is None
    assert cache.get("ns", "other-src", ident, "NH", "business_identity") is None
    clock.advance(days=8)
    assert cache.get("ns", "src", ident, "NH", "business_identity") is None
    assert cache.metrics.expired == 1


def test_positive_cache_expiry(cache, clock):
    ident = normalize_identifier("phone", "603-555-0101")
    cache.put("ns", "src", ident, "NH", "s", [rec(clock)])
    clock.advance(days=29)
    assert cache.get("ns", "src", ident, "NH", "s") is not None
    clock.advance(days=2)
    assert cache.get("ns", "src", ident, "NH", "s") is None


def test_source_ttl_override(tmp_path, clock):
    c = SearchCache(tmp_path / "d" / "c.db", clock=clock,
                    source_ttl_overrides={"src": (timedelta(hours=1), timedelta(minutes=5))})
    ident = normalize_identifier("phone", "603-555-0101")
    c.put("ns", "src", ident, None, "s", [])
    clock.advance(minutes=6)
    assert c.get("ns", "src", ident, None, "s") is None


def test_namespaces_are_isolated(cache, clock):
    ident = normalize_identifier("phone", "603-555-0101")
    cache.put("client-a", "src", ident, "NH", "s", [rec(clock)])
    assert cache.get("client-b", "src", ident, "NH", "s") is None


def test_invalidation_by_query_and_source(cache, clock):
    a = normalize_identifier("phone", "603-555-0101")
    b = normalize_identifier("phone", "603-555-0122")
    cache.put("ns", "src", a, "NH", "s", [rec(clock)])
    cache.put("ns", "src", b, "NH", "s", [])
    assert cache.invalidate("ns", normalized_query=a.normalized) == 1
    assert cache.get("ns", "src", a, "NH", "s") is None
    assert cache.invalidate("ns", source_id="src") == 1
    with pytest.raises(ValueError):
        cache.invalidate("ns")
    cache.put("ns", "src", a, "NH", "s", [])
    assert cache.purge_namespace("ns") == 1


def test_metrics_hit_rate_and_cost_avoided(cache, clock):
    ident = normalize_identifier("phone", "603-555-0101")
    cache.get("ns", "src", ident, None, "s")
    cache.put("ns", "src", ident, None, "s", [])
    cache.get("ns", "src", ident, None, "s", cost_if_missed=0.25)
    d = cache.metrics.to_dict()
    assert d["hit_rate"] == 0.5 and d["external_calls_avoided"] == 1
    assert d["estimated_cost_avoided_usd"] == 0.25
    assert d["false_positive_rate"] is None  # not invented without a labelled benchmark


def test_peek_does_not_count(cache):
    ident = normalize_identifier("phone", "603-555-0101")
    cache.get("ns", "src", ident, None, "s", count=False)
    assert cache.metrics.lookups == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_cache_file_permissions(cache, tmp_path):
    mode = stat.S_IMODE(os.stat(tmp_path / "data" / "cache.sqlite3").st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "data").st_mode) == 0o700
