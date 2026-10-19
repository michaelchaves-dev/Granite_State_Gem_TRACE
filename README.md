# Granite State Gem TRACE

**Contractor-specific public-record discovery and evidence-linked reporting**  
Subtract Architect Studios — *Intelligence by Subtraction*

## Mission
Help consumers find and understand documented contractor business histories by searching fragmented public information, preserving citations, and presenting only what the evidence establishes. TRACE does **not** judge character, assign guilt, speculate about motives, or turn allegations into facts. Consumers make their own decisions.

**Status:** v0.1 engine implemented and tested against **synthetic** data only. No live registry integration, no deployment, no real investigations. See *Implementation status* below and the build plan in [`docs/CURSOR_BUILD_SLICES.md`](docs/CURSOR_BUILD_SLICES.md).

## Independent modular boundaries
TRACE is its own contractor-focused service. The following are **optional, independently maintained** capabilities—not components merged into TRACE:
- [Compliance Health Check & Assurance](https://github.com/michaelchaves-dev/Compliance_Health_Check_Assurance): requirement-specific vendor compliance, credentials, renewals, monitoring, and remediation.
- [Subtract Architect Provenance Shield](https://github.com/michaelchaves-dev/Subtract_Architect_Provenance_Shield): optional provenance/integrity support when an implementation is available.
- [GSG TRACE Data](https://github.com/michaelchaves-dev/GSG_TRACE_Data): **private** evidence and search-result workspace. It is not a public dataset or an automatic dependency.

Integration, when useful, must be explicit, permissioned, scoped, reversible, and through versioned interfaces. No cross-repository copying or automatic sharing of private records. Each project remains independently operable.

## Contractor-specific search scope
- Registered legal names, DBAs, historical business names, filings, and registration dates.
- Publicly documented owners, officers, and operational affiliations.
- Public business phone numbers, business addresses, websites, domains, and business social profiles.
- Public trade licenses and relevant municipal permits; compliance determinations are delegated to the separate Compliance service if requested.
- Public reviews, complaints, responses, resolutions, official enforcement actions, and court outcomes, accurately attributed and dated.
- Historical records and name changes, without assuming continuity of ownership.

## Evidence-only publication rules
1. **Every published factual finding must cite its source**, observation date, and the precise claim supported.
2. Distinguish verified legal identity, documented operational affiliation, unresolved lead, and contradicted or unrelated records.
3. A shared name, surname, address, phone, registered agent, or domain is **not by itself proof** of common ownership or misconduct.
4. A review or complaint is an allegation or account, **not** a judicial or regulatory finding. Include available responses, dispositions, corrections, and contradictory evidence.
5. Do not infer motives, label people as predators/scammers, assign subjective trust scores, or publish unverified accusations.
6. Missing search results mean **not found in the sources checked at that time**, never proof of nonexistence.
7. Family or associate relationships are considered **case by case** only where a relevant public, documented business connection justifies the check; no relatives-by-default, minors, or private family profiling.
8. Apply source permissions, privacy and retention controls, appropriate human review, and a documented correction/appeal channel before publishing potentially adverse findings.

## Planned bounded discovery pipeline
```text
Contractor seed (public business identifier)
  -> Negative Step Zero (scope, necessity, budget, permissions)
  -> Source search / cached-result lookup
  -> Extract public business identifiers
  -> Normalize + deduplicate + evidence-check
  -> Bounded recursive expansion (depth, time, cost, relevance)
  -> Reconcile conflicts and independent corroboration
  -> Human review where needed
  -> Evidence-cited, neutral report
```

### Recursive Work Memory
Cache positive and zero-result searches using normalized query, source, jurisdiction, search scope, retrieval time, freshness/TTL, and invalidation triggers. A negative cache entry is a **time-bounded observation**, not a permanent assertion. Respect source licensing and deletion/correction requests. Measure cache hits, avoided calls, cost, and false-positive/false-negative rates.

### Minimum evidence receipt (proposed)
```json
{
  "claim_id": "uuid",
  "subject_business_id": "internal-id",
  "claim_text": "Narrow factual observation",
  "relationship_status": "verified_legal|documented_operational|unresolved|contradicted|unrelated",
  "source_type": "official_registry|court|business_site|review|other",
  "source_url_or_reference": "source reference",
  "source_record_id": "optional",
  "observed_at_utc": "ISO-8601",
  "source_event_date": "ISO-8601 or null",
  "excerpt_or_locator": "short relevant passage/locator",
  "limitations": "scope and uncertainty",
  "review_status": "pending|approved|rejected|corrected"
}
```

## Data security
Do **not** commit investigation dossiers, contact exports, client records, raw query results, secrets, or sensitive personal data to this public repository. The separate **private** GSG_TRACE_Data repository is a restricted development workspace, not automatically a suitable production database. Production evidence should use encrypted access-controlled storage, retention/deletion rules, audit logs, and least-privilege access. Public reports contain only vetted, appropriately disclosed findings.

## MVP acceptance criteria
- One scoped contractor/business identifier can be searched against an approved public source.
- Each displayed factual finding has a working citation and timestamp.
- Recursive expansion has strict depth, relevance, and spend limits.
- Conflicting identifiers do not auto-merge entities.
- Negative-result caching includes scope and expiry.
- A neutral report clearly separates facts, allegations, and unknowns.
- Private records cannot leak into public builds or logs.

## Roadmap
1. Define evidence schema, source registry, privacy policy, and test fixtures.
2. Implement one approved source adapter and deterministic normalization.
3. Add bounded recursion and positive/negative caching.
4. Add evidence receipts, review queue, correction workflow, and neutral reporting.
5. Test optional versioned integrations with independent Compliance and Provenance services.

## Implementation status (v0.1 — Contractor Identity Discovery)
Implemented in `gsg_trace/` (Python 3.11+, standard library only, no paid dependencies):

| Module | What it does |
|---|---|
| `normalize.py` | Phone (E.164), business name (case/punctuation/legal-suffix/alias aware), domain, email, address, social, registry ID; dedupe that preserves original spellings |
| `engine.py` | Negative Step Zero preflight; bounded breadth-first discovery (depth, branches, requests, time, cost, entities); per-step reason log; deferred-lead log |
| `cache.py` | Positive + negative search memory with TTLs, per-client namespaces, invalidation, metrics; errors are never cached |
| `relationships.py` | Six relationship statuses; corroboration counted by independent origin; contradiction handling |
| `receipts.py` | Evidence receipts with the spec's fields; publication gate (complete citation + approval); adverse and injection-flagged findings held for review |
| `report.py` | Neutral report sections A–I in Markdown and JSON with a neutral-language lint |
| `privacy.py`, `store.py` | Refuses to store data in this repo or any unmarked git work tree; 0700/0600 files; hashed identifiers in logs |
| `sources/` | Adapter contract + a synthetic registry fixture adapter (the only source in v0.1) |
| `web/` | Local dev UI and JSON API (`127.0.0.1` only) |

```bash
pip install -e ".[dev]"
python -m pytest
python -m gsg_trace search --phone "(603) 555-0101" --jurisdiction NH --source-fixture fixtures/synthetic_nh_registry.json
python -m gsg_trace serve --source-fixture fixtures/synthetic_nh_registry.json   # http://127.0.0.1:8765
```

Private data goes to `$GSG_TRACE_DATA_DIR` (default `~/.local/share/gsg_trace`), never into this repository. The private GSG_TRACE_Data repo can be used as a data dir only if its root contains a `.gsg_trace_private` marker.

**Principle: We find records, verify documented connections, cite sources, and let consumers decide.**
