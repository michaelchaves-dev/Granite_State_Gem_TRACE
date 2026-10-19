from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gsg_trace.cache import SearchCache
from gsg_trace.engine import DiscoveryEngine
from gsg_trace.models import SearchLimits
from gsg_trace.sources import FixtureAdapter

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_nh_registry.json"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)
        self.mono = 0.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)
        self.mono += timedelta(**kw).total_seconds()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def registry_data() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def registry(registry_data, clock) -> FixtureAdapter:
    return FixtureAdapter(copy.deepcopy(registry_data), clock=clock)


@pytest.fixture
def cache(tmp_path, clock) -> SearchCache:
    c = SearchCache(tmp_path / "data" / "cache.sqlite3", clock=clock)
    yield c
    c.close()


@pytest.fixture
def make_engine(cache, clock):
    def _make(adapters, **limit_kw):
        return DiscoveryEngine(adapters, cache, namespace="test", limits=SearchLimits(**limit_kw),
                               clock=clock, monotonic=clock.monotonic)
    return _make


def adapter(source_id: str, records: list[dict], *, tier: int = 1, source_type: str = "official_registry",
            cost: float = 0.0, approved: bool = True, clock=None) -> FixtureAdapter:
    data = {"source": {"source_id": source_id, "source_name": f"SYNTHETIC {source_id}", "tier": tier,
                       "source_type": source_type, "jurisdictions": ["NH"], "cost_per_request_usd": cost,
                       "approved": approved},
            "records": records}
    kw = {"clock": clock} if clock else {}
    return FixtureAdapter(data, **kw)
