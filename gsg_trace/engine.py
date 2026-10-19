"""Bounded recursive discovery engine (spec §4 Negative Step Zero, §5 recursion).

Breadth-first over (identifier, source) pairs with hard limits on depth,
branches per node, external requests, wall time, spend and entity count.
Every search decision is logged with its reason; every identifier that was
found but not searched is kept as a deferred lead so the report can say so.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from .cache import SearchCache
from .extract import discover, link_assertions, shared_identifiers
from .models import (Identifier, IdentifierType, LinkAssertion, Relationship, SearchLimits, SearchLogEntry,
                     SourceRecord, iso, new_id, utcnow)
from .normalize import NormalizationError, normalize_inputs, normalize_jurisdiction
from .privacy import hash_for_log
from .relationships import classify, entity_is_registered
from .sources.base import SourceAdapter, SourceUnavailable

log = logging.getLogger("gsg_trace.engine")
NAME_LABELS = ("legal name", "trade name", "prior name")


@dataclass
class SearchRequest:
    inputs: list[tuple[str, str]]
    jurisdiction: str | None = None
    objective: str = "Contractor identity discovery: who is this business on the public record?"
    scope: str = "business_identity"


@dataclass
class PreflightPlan:
    objective: str
    scope: str
    jurisdiction: str | None
    identifiers: list[dict[str, Any]]
    rejected_inputs: list[dict[str, str]]
    sources_selected: list[dict[str, Any]]
    sources_excluded: list[dict[str, str]]
    cached_pairs: int
    uncached_pairs: int
    estimated_seed_requests: int
    estimated_max_cost_usd: float
    limits: dict[str, Any]
    stop_conditions: list[str]
    can_proceed: bool
    blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryResult:
    run_id: str
    started_at: str
    finished_at: str | None
    request: dict[str, Any]
    preflight: PreflightPlan
    entities: dict[str, list[SourceRecord]] = field(default_factory=dict)
    discovery_paths: dict[str, dict[str, Any]] = field(default_factory=dict)
    subject_keys: list[str] = field(default_factory=list)
    assertions: list[LinkAssertion] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    search_log: list[SearchLogEntry] = field(default_factory=list)
    sources_checked: list[str] = field(default_factory=list)
    sources_unavailable: list[dict[str, str]] = field(default_factory=list)
    negative_results: list[dict[str, Any]] = field(default_factory=list)
    deferred_leads: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "not_started"
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "started_at": self.started_at, "finished_at": self.finished_at,
            "request": self.request, "preflight": self.preflight.to_dict(),
            "entities": {k: [r.to_dict() for r in v] for k, v in self.entities.items()},
            "discovery_paths": self.discovery_paths, "subject_keys": self.subject_keys,
            "assertions": [dict(asdict(a), polarity=a.polarity.value, tier=int(a.tier)) for a in self.assertions],
            "relationships": [r.to_dict() for r in self.relationships],
            "search_log": [asdict(e) for e in self.search_log],
            "sources_checked": self.sources_checked, "sources_unavailable": self.sources_unavailable,
            "negative_results": self.negative_results, "deferred_leads": self.deferred_leads,
            "stop_reason": self.stop_reason, "metrics": self.metrics,
        }


class _Stop(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DiscoveryEngine:
    def __init__(self, adapters: Iterable[SourceAdapter], cache: SearchCache, *, namespace: str = "default",
                 limits: SearchLimits | None = None, clock: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic):
        self.adapters = list(adapters)
        ids = [a.source_id for a in self.adapters]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source_id among adapters")
        self.cache = cache
        if not namespace or len(namespace) > 64:
            raise ValueError("namespace must be 1-64 characters")
        self.namespace = namespace
        self.limits = limits or SearchLimits()
        self.limits.validate()
        self.clock = clock
        self.monotonic = monotonic

    # ================================================================ Negative Step Zero
    def preflight(self, req: SearchRequest) -> tuple[PreflightPlan, list[Identifier], list[SourceAdapter]]:
        blockers: list[str] = []
        try:
            jurisdiction = normalize_jurisdiction(req.jurisdiction)
        except NormalizationError as exc:
            jurisdiction, blockers = None, [f"jurisdiction: {exc}"]
        if not req.inputs:
            blockers.append("no identifiers supplied")
        if len(req.inputs) > 20:
            blockers.append("too many identifiers in one request (max 20)")
        idents, rejected = normalize_inputs(req.inputs[:20])
        if req.inputs and not idents:
            blockers.append("no supplied identifier passed validation")

        selected, excluded = [], []
        for a in self.adapters:
            reason = None
            if not a.approved:
                reason = "source not approved by owner (terms/permission not confirmed)"
            elif a.cost_per_request_usd > self.limits.max_cost_usd:
                reason = (f"paid source (${a.cost_per_request_usd:.4f}/request) exceeds authorized budget "
                          f"${self.limits.max_cost_usd:.2f}")
            elif not a.covers(jurisdiction):
                reason = f"does not cover jurisdiction {jurisdiction}"
            elif not any(a.supports(i) for i in idents):
                reason = "supports none of the supplied identifier types"
            if reason:
                excluded.append({"source_id": a.source_id, "reason": reason})
            else:
                selected.append(a)
        if idents and not selected:
            blockers.append("no approved, in-budget source can search these identifiers")

        cached = uncached = 0
        est_cost = 0.0
        for i in idents:
            for a in selected:
                if not a.supports(i):
                    continue
                hit = self.cache.get(self.namespace, a.source_id, i, jurisdiction, a.scope, count=False)
                if hit:
                    cached += 1
                else:
                    uncached += 1
                    est_cost += a.cost_per_request_usd
        max_cost = min(self.limits.max_cost_usd,
                       max((a.cost_per_request_usd for a in selected), default=0.0) * self.limits.max_requests)
        plan = PreflightPlan(
            objective=req.objective, scope=req.scope, jurisdiction=jurisdiction,
            identifiers=[i.to_dict() for i in idents], rejected_inputs=rejected,
            sources_selected=[{"source_id": a.source_id, "source_name": a.source_name, "tier": int(a.tier),
                               "cost_per_request_usd": a.cost_per_request_usd, "synthetic": a.synthetic}
                              for a in selected],
            sources_excluded=excluded, cached_pairs=cached, uncached_pairs=uncached,
            estimated_seed_requests=uncached, estimated_max_cost_usd=round(max(est_cost, max_cost), 4),
            limits=asdict(self.limits),
            stop_conditions=[f"depth > {self.limits.max_depth}", f"{self.limits.max_requests} external requests",
                             f"{self.limits.max_seconds}s elapsed", f"${self.limits.max_cost_usd:.2f} spend",
                             f"{self.limits.max_entities} entities", "queue exhausted"],
            can_proceed=not blockers, blockers=blockers)
        return plan, idents, selected

    # ================================================================ recursion
    def run(self, req: SearchRequest) -> DiscoveryResult:
        plan, seeds, adapters = self.preflight(req)
        res = DiscoveryResult(run_id=new_id(), started_at=iso(self.clock()), finished_at=None,
                              request={"inputs": [{"type": str(t), "value": v} for t, v in req.inputs],
                                       "jurisdiction": req.jurisdiction, "objective": req.objective,
                                       "scope": req.scope},
                              preflight=plan)
        if not plan.can_proceed:
            res.stop_reason = "preflight_blocked"
            res.finished_at = iso(self.clock())
            return res

        jur = plan.jurisdiction
        lim = self.limits
        t0 = self.monotonic()
        queue: deque[tuple[Identifier, int, str]] = deque((s, 0, "user-supplied seed") for s in seeds)
        enqueued = {s.key for s in seeds}
        searched_pairs: set[tuple[str, str]] = set()
        assertion_keys: set[tuple] = set()
        counters = dict(requests=0, cache_positive=0, cache_negative=0, cost=0.0, max_depth=0, enqueued=len(seeds))
        step = 0

        def logit(ident: Identifier, a: SourceAdapter, depth: int, reason: str, outcome: str,
                  n: int = 0, cost: float = 0.0) -> None:
            nonlocal step
            step += 1
            entry = SearchLogEntry(step, ident.key, hash_for_log(ident.key), ident.type.value, a.source_id, depth,
                                   reason, outcome, n, cost, iso(self.clock()))
            res.search_log.append(entry)
            log.info("step=%d id=%s type=%s source=%s depth=%d outcome=%s n=%d", step,
                     entry.identifier_key_hash, entry.identifier_type, a.source_id, depth, outcome, n)

        def defer(ident: Identifier, reason: str, why: str, relevance: float | None = None) -> None:
            res.deferred_leads.append({"identifier": ident.key, "found_via": reason, "not_searched_because": why,
                                       "relevance": relevance})

        try:
            while queue:
                ident, depth, reason = queue.popleft()
                counters["max_depth"] = max(counters["max_depth"], depth)
                for a in adapters:
                    if not a.supports(ident):
                        continue
                    pair = (ident.key, a.source_id)
                    if pair in searched_pairs:
                        continue
                    searched_pairs.add(pair)

                    entry = self.cache.get(self.namespace, a.source_id, ident, jur, a.scope,
                                           cost_if_missed=a.cost_per_request_usd)
                    if entry is not None:
                        records = entry.records
                        counters["cache_positive" if records else "cache_negative"] += 1
                        logit(ident, a, depth, reason, "cache_hit_positive" if records else "cache_hit_negative",
                              len(records))
                        as_of = iso(entry.retrieved_at)
                    else:
                        if self.monotonic() - t0 >= lim.max_seconds:
                            raise _Stop("max_seconds")
                        if counters["requests"] >= lim.max_requests:
                            raise _Stop("max_requests")
                        if counters["cost"] + a.cost_per_request_usd > lim.max_cost_usd + 1e-9:
                            logit(ident, a, depth, reason, "skipped_budget")
                            continue
                        try:
                            records = a.search(ident, jur)
                        except SourceUnavailable as exc:
                            counters["requests"] += 1
                            res.sources_unavailable.append({"source_id": a.source_id, "error": str(exc)[:200],
                                                            "identifier": ident.key})
                            logit(ident, a, depth, reason, "source_error")
                            continue
                        counters["requests"] += 1
                        counters["cost"] += a.cost_per_request_usd
                        self.cache.put(self.namespace, a.source_id, ident, jur, a.scope, records)
                        logit(ident, a, depth, reason, "searched_found" if records else "searched_none",
                              len(records), a.cost_per_request_usd)
                        as_of = iso(self.clock())
                    if a.source_id not in res.sources_checked:
                        res.sources_checked.append(a.source_id)
                    if not records:
                        res.negative_results.append({"identifier": ident.key, "source_id": a.source_id,
                                                     "source_name": a.source_name, "jurisdiction": jur,
                                                     "scope": a.scope, "as_of": as_of})
                        continue
                    for rec in records:
                        self._absorb(rec, ident, depth, reason, res, assertion_keys)
                        if depth >= lim.max_depth:
                            for d in discover(rec):
                                if d.identifier.key not in enqueued:
                                    defer(d.identifier, d.reason, "depth limit", d.relevance)
                                    enqueued.add(d.identifier.key)
                            continue
                        children = 0
                        for d in sorted(discover(rec), key=lambda x: -x.relevance):
                            if d.identifier.key in enqueued:
                                continue
                            enqueued.add(d.identifier.key)
                            if d.relevance < lim.min_relevance:
                                defer(d.identifier, d.reason, "below relevance threshold", d.relevance)
                            elif children >= lim.max_branches_per_node:
                                defer(d.identifier, d.reason, "branch limit", d.relevance)
                            else:
                                children += 1
                                counters["enqueued"] += 1
                                queue.append((d.identifier, depth + 1, d.reason))
            res.stop_reason = "queue_exhausted"
        except _Stop as stop:
            res.stop_reason = stop.reason
            for ident, _depth, reason in queue:
                defer(ident, reason, f"search stopped: {stop.reason}")

        res.subject_keys = sorted(k for k, p in res.discovery_paths.items() if p["depth"] == 0)
        res.relationships = self._relationships(res)
        res.finished_at = iso(self.clock())
        res.metrics = {
            "external_requests": counters["requests"], "cache_hits_positive": counters["cache_positive"],
            "cache_hits_negative": counters["cache_negative"], "estimated_cost_usd": round(counters["cost"], 4),
            "elapsed_seconds": round(self.monotonic() - t0, 4), "entities_found": len(res.entities),
            "max_depth_reached": counters["max_depth"], "identifiers_enqueued": counters["enqueued"],
            "leads_deferred": len(res.deferred_leads), "llm_tokens_used": 0,
            "cache": self.cache.metrics.to_dict(),
        }
        return res

    def _absorb(self, rec: SourceRecord, ident: Identifier, depth: int, reason: str,
                res: DiscoveryResult, assertion_keys: set[tuple]) -> None:
        ek = rec.entity_key or rec.record_key
        if ek not in res.entities:
            if len(res.entities) >= self.limits.max_entities:
                raise _Stop("max_entities")
            res.entities[ek] = []
            res.discovery_paths[ek] = {"depth": depth, "via_identifier": ident.key, "reason": reason}
        if all(r.record_key != rec.record_key for r in res.entities[ek]):
            res.entities[ek].append(rec)
        for la in link_assertions(rec):
            k = (la.from_entity, la.to_entity, la.link_type, la.polarity, la.origin)
            if k not in assertion_keys:
                assertion_keys.add(k)
                res.assertions.append(la)

    def _relationships(self, res: DiscoveryResult) -> list[Relationship]:
        pairs: list[tuple[str, str]] = []
        seen: set[frozenset[str]] = set()

        def add(a: str, b: str) -> None:
            key = frozenset((a, b))
            if a != b and key not in seen:
                seen.add(key)
                pairs.append((a, b))

        for s in res.subject_keys:
            for e in res.entities:
                add(s, e)
        for la in res.assertions:
            add(la.from_entity, la.to_entity)

        out = []
        for a, b in pairs:
            ra, rb = res.entities.get(a, []), res.entities.get(b, [])
            shared_all = shared_identifiers(ra, rb) if ra and rb else []
            names = [s for s in shared_all if s.startswith(NAME_LABELS)]
            other = [s for s in shared_all if not s.startswith(NAME_LABELS)]
            out.append(classify(a, b, res.assertions, other, name_overlap=names,
                                both_registered_distinct=bool(ra and rb and entity_is_registered(ra)
                                                              and entity_is_registered(rb))))
        return out


def identifier_inputs(**kwargs: str | list[str] | None) -> list[tuple[str, str]]:
    """Convenience: identifier_inputs(business_name="X", phone=["a", "b"])."""
    out: list[tuple[str, str]] = []
    for k, v in kwargs.items():
        if v is None:
            continue
        IdentifierType(k)
        for item in ([v] if isinstance(v, str) else v):
            out.append((k, item))
    return out
