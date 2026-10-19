# GSG TRACE — Build Slices for Cursor (and Claude Code)

From the working v0.1 engine in this repo to a published search website.

**How to use this file.** Do the slices in order. For each one, open a new Cursor Agent chat in this repo, paste the **Prompt** block verbatim, and do not start the next slice until every **Done when** item passes. `.cursor/rules/trace-non-negotiables.mdc` loads automatically in every Cursor chat; it carries the evidence and privacy rules so you don't repeat them. Each slice is one branch and one pull request (`slice/NN-short-name`). Claude Code can run any slice the same way: paste the prompt into a session attached to this repo.

**Owner decisions are marked 🔑.** These are the points where an agent must stop and wait for you — real data sources, money, accounts, and going public.

---

## Where things stand (v0.1, built and tested)

| Area | Status |
|---|---|
| Identifier normalization + dedupe | Done — phone (E.164), name (suffix/punctuation/alias aware), domain, email, address, social, registry ID |
| Negative Step Zero preflight | Done — source approval, $0 budget, jurisdiction, cache reuse, stop conditions |
| Bounded recursive discovery | Done — depth, branches, requests, time, cost, entity caps; deferred-lead log; cycle-safe |
| Positive/negative cache | Done — SQLite, TTL (30d / 7d), per-client namespaces, invalidation, metrics |
| Relationship classification | Done — six statuses, origin-based corroboration, contradiction handling |
| Evidence receipts + publication gate | Done — citation completeness, adverse findings held for review, injection flagging |
| Neutral report A–I (Markdown + JSON) | Done — neutral-language lint, synthetic-data labelling |
| Private storage guard | Done — refuses repo/git paths, 0700/0600, hashed log identifiers |
| Local web UI + JSON API | Done — `python -m gsg_trace serve` (dev only, 127.0.0.1) |
| Real public data source | **Not built** — only a synthetic fixture (Slice 2) |
| Production database | **Not built** — SQLite dev store (Slice 1) |
| Production API + hosting | **Not built** (Slices 3, 7) |
| Accounts, corrections, review queue | **Not built** (Slices 5, 6) |
| Reviews/complaints, licensing, permits, courts | **Not built** (Slice 8) |

Run it locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                       # 102 tests
python -m gsg_trace serve --source-fixture fixtures/synthetic_nh_registry.json
# open http://127.0.0.1:8765 and search phone (603) 555-0101
```

---

## Target architecture

```text
Browser (static site, textContent rendering, CSP)
   │  POST /api/search
   ▼
FastAPI app on Vercel (one Python function, maxDuration 60s)
   ├─ auth check (Supabase Auth JWT) + per-user rate limit
   ├─ DiscoveryEngine (this repo's gsg_trace package, unchanged core)
   │     ├─ SourceAdapters  ── NH bulk registry table (Slice 2), later licensing/permits/courts
   │     ├─ CacheBackend    ── Postgres (Supabase) cache_entries, per-client namespace
   │     └─ RunStore        ── Postgres runs + receipts (RLS: owner-only)
   └─ report renderer (Markdown/JSON) + review queue + corrections
Supabase Postgres (encrypted at rest, RLS on every table, service key server-side only)
```

Why this shape: the engine is stdlib-only Python, so it runs as a single Vercel Python function with no cold-start weight; Vercel's Python runtime builds a FastAPI app into one function and lets you set `maxDuration` in `vercel.json` ([Vercel docs](https://vercel.com/docs/functions/configuring-functions/duration)). Serverless disks are ephemeral, so SQLite cannot be the production store — Postgres replaces it behind the same interface. Supabase gives Postgres, Auth and row-level security in one place.

---

## Slice 0 — Workspace check (15 min)

**Prompt**
```
Read README.md, docs/SPECIFICATION_v0.1.txt, docs/CURSOR_BUILD_SLICES.md and .cursor/rules/trace-non-negotiables.mdc.
Create a Python 3.12 venv, install with `pip install -e ".[dev]"`, run `python -m pytest`, then run
`python -m gsg_trace serve --source-fixture fixtures/synthetic_nh_registry.json` and confirm /api/health.
Do not change any code. Report: test count and result, Python version, and anything that failed to install.
```
**Done when:** all tests pass locally; the page loads and the phone `(603) 555-0101` returns a report.
**Likely failure:** `python` points at an old interpreter → use `python3.12 -m venv .venv`.

---

## Slice 1 — Storage interfaces + Postgres backend

**Prompt**
```
Goal: make cache and run storage pluggable so production can use Postgres while tests keep SQLite.

1. In gsg_trace/cache.py extract a `CacheBackend` Protocol with exactly the methods DiscoveryEngine and the CLI use
   (get with count=, put, invalidate, purge_namespace, freshness, metrics). Keep SearchCache as the SQLite implementation.
2. In gsg_trace/store.py extract a `RunStoreBackend` Protocol (save, load, delete). Keep the file implementation.
3. Add gsg_trace/backends/postgres.py implementing both with psycopg 3 (optional dependency group "postgres").
   Same semantics as SQLite: per-namespace keys, TTLs, negative entries, no caching of errors, metrics counters.
   Store payloads as JSONB. All SQL parameterized.
4. Add migrations/001_init.sql: tables cache_entries, runs, receipts, review_actions, correction_requests,
   audit_log; indexes matching the SQLite ones; enable row level security on every table with deny-all default
   policies (the API uses the service role server-side only).
5. Select backend from env: GSG_TRACE_DATABASE_URL set -> Postgres, else SQLite in the private data dir.
6. Tests: run the existing cache tests against both backends via a parametrized fixture; Postgres tests are
   skipped unless GSG_TRACE_TEST_DATABASE_URL is set. Add a GitHub Actions job with a postgres:16 service container.
Do not create any cloud resources. Do not add the database URL to any committed file.
```
**Done when:** full suite green on SQLite; Postgres-backed cache tests green in CI; `migrations/001_init.sql` applies cleanly to an empty database twice (idempotent).
**Failure points:** timezone-naive timestamps (use `timestamptz` everywhere); JSONB round-trip of `SourceRecord.retrieved_at`; RLS enabled but no policy → service role still works, anon key blocked (that is intended — verify both).

🔑 **Owner:** create the Supabase project (or approve Claude/Cursor doing it through the Supabase connector) and put `GSG_TRACE_DATABASE_URL` in Vercel env vars, never in the repo.

---

## Slice 2 — First real source: NH business registry (licensed bulk data, not scraping)

🔑 **Owner decision first.** The NH Secretary of State's corporation FAQ lists a "Purchase Business Data" option and says charter documents can be viewed and downloaded individually at no charge; it does not describe an API or the terms for automated access to QuickStart ([NH SOS FAQ](https://sos.nh.gov/corporation-ucc-securities/corporation/faqs)). Automated scraping of QuickStart without written permission is the wrong foundation for a product whose whole premise is lawful, citable sourcing. Email corporate@sos.nh.gov (603-271-3246) and ask: (1) bulk data format, fields, refresh cadence and price, (2) whether redistribution of individual record facts with citation is permitted, (3) whether automated lookups against QuickStart are permitted and at what rate. Record the answer in `docs/sources/nh-sos.md`. Build only what the answer permits.

**Prompt (bulk-file path)**
```
Goal: an approved, real Tier-1 source adapter backed by the licensed NH SOS bulk file.

1. Add docs/sources/nh-sos.md with: source owner, terms summary (from the owner's correspondence), refresh cadence,
   fields used, citation URL pattern for an individual entity page, and retention rules.
2. Add gsg_trace/ingest/nh_sos.py: parse the bulk file into the same record shape FixtureAdapter uses
   (entity_id, legal_name, status, entity_type, registered_on, trade_names, prior_names, addresses,
   officers, registered_agent, links). Map successor/merger/conversion filings to links with legal link types.
   Keep raw rows' sha256 as content_hash. Load into a `registry_nh` table (Slice 1 Postgres) with a
   snapshot_date column. Never load into this repo.
3. Add gsg_trace/sources/nh_sos.py: NhSosRegistryAdapter(SourceAdapter) querying registry_nh by the same index
   keys FixtureAdapter builds (name match key, phone, address, registry id). tier=OFFICIAL,
   source_type=official_registry, approved from env GSG_TRACE_NH_SOS_APPROVED=1, url = the public entity page
   for the record, retrieved_at = snapshot_date (the record is as-of the snapshot, say so in limitations).
4. Tests: a tiny synthetic file in the same format under fixtures/nh_sos_sample.* (fictional data only) covering
   name changes, a merger link, a dissolved entity and an entity with no phone.
Phone numbers are rarely in registry filings — do not fabricate them; the adapter simply won't match phone searches.
```
**Done when:** ingest is idempotent per snapshot; search by name and registry ID returns cited records whose URL opens the real public entity page; the report labels the as-of date.
**Failure points:** registries rarely carry phone numbers, so phone-first searches will mostly return "not found" until a Tier-2 source exists (Slice 8) — the UI must say so instead of implying absence; officer names in bulk data are personal data — keep them out of search indexes (attribute only).

---

## Slice 3 — Production API (FastAPI)

**Prompt**
```
Goal: a production HTTP API around the existing engine, deployable as one Vercel Python function.

1. Add api/main.py (FastAPI). Routes:
   GET  /api/health
   POST /api/search            body: {identifiers: {business_name|phone|domain|email|address|registry_id: str}, jurisdiction}
   GET  /api/runs/{run_id}     owner-only
   POST /api/corrections       body: {run_id?, claim_id?, business_name, contact_email, statement} (Slice 6 consumes it)
   Reuse gsg_trace.web.server.parse_search_body for validation (move it to gsg_trace/api_schema.py and use Pydantic
   models that enforce the same rules: allowed keys only, string values, max 200 chars each, max 20 identifiers).
2. Engine per request with SearchLimits(max_seconds=20, max_requests=25, max_depth=2); cache/run backends from env.
3. Return {report_markdown, report_json} exactly as render_json/render_markdown produce. No extra prose.
4. Security: CORS allow-list from env (none by default), security headers identical to gsg_trace/web/server.py,
   body limit 16 KB, request IDs in logs, logs use hash_for_log only, generic 500 messages.
5. Rate limiting placeholder interface (implemented in Slice 5 with auth).
6. Serve the static site from public/ (Slice 4). Add vercel.json with functions."api/main.py".maxDuration = 60.
7. Tests with FastAPI TestClient mirroring tests/test_web.py, plus a test that a source error returns a report
   listing the source as unavailable (not an empty "not found").
Keep gsg_trace/web/server.py as the zero-dependency local dev server.
```
**Done when:** `uvicorn api.main:app` serves the page and API locally; TestClient suite green; `vercel dev` runs it.
**Failure points:** importing the package from `api/` on Vercel (add `gsg_trace` to the deployment, `pip install -e .` isn't run — use a `requirements.txt` with `-e .` or move to `pyproject` build; verify with `vercel build` locally); long searches → keep `max_seconds` well under `maxDuration`.

---

## Slice 4 — Public website

**Prompt**
```
Goal: the public-facing site, static files served by the FastAPI app.

Pages (public/):
  index.html        landing: what TRACE does / does not do, the four-line principle, a "Search" call to action
  search.html       the search UI — start from gsg_trace/web/static/index.html; keep textContent-only rendering
  methodology.html  sources and tiers, relationship statuses with plain-English definitions, what "not found" means,
                    synthetic vs real data, the evidence receipt fields
  corrections.html  how a business requests a correction; form posts to /api/corrections
  terms.html, privacy.html  placeholders clearly marked DRAFT — NOT LEGAL TEXT until counsel supplies them

Requirements: no third-party scripts or trackers; one shared CSS file; system fonts; works at 360px width;
dark mode; keyboard accessible (labels, focus rings, aria-live status); each citation chip links to its row in
the source register; a visible banner whenever any selected source is synthetic; the "not searched" text for
sections E/F stays until those sources exist. Copy must pass report.lint_neutral_language (add a test that lints
every public/*.html file's visible text).
```
**Done when:** Lighthouse accessibility ≥ 95; lint test covers all pages; no inline event handlers (CSP-friendly).
**Failure points:** marketing copy drifting into judgement ("avoid bad contractors", "spot scams") — the lint test catches the obvious words; you review the rest.

---

## Slice 5 — Accounts, abuse controls, retention

**Prompt**
```
Goal: only signed-in users can search; abuse and retention are controlled.

1. Supabase Auth (email magic link). API verifies the JWT server-side; the namespace for cache/runs is the user id.
2. Per-user limits: 20 searches/hour, 100/day (configurable env). Return 429 with Retry-After.
3. Audit log row per search: user id, run id, identifier hashes (hash_for_log), sources, stop reason. No raw values.
4. Retention: runs and receipts older than GSG_TRACE_RETENTION_DAYS (default 90) are deleted by a scheduled job
   (Vercel Cron hitting an authenticated /api/admin/retention endpoint). Cache entries already expire by TTL.
5. "Delete my data" endpoint: purges the user's runs, receipts and cache namespace.
6. Tests for auth required, limits, audit content (asserts no raw phone/name), retention deletion.
```
**Done when:** anonymous search returns 401; limits enforced; retention job deletes and logs counts.
**Failure points:** using the anon key server-side (must be service role, server only); caching across users (namespace must be the user id, never a shared default).

---

## Slice 6 — Review queue and corrections workflow

**Prompt**
```
Goal: humans approve adverse findings and handle correction requests before anything adverse is shown.

1. Admin role (Supabase custom claim). Admin-only pages: /admin/review lists receipts with review_status=pending
   (adverse statuses, injection-flagged records, operational links from non-official sources), showing claim,
   excerpt, source link, limitations. Actions: approve, reject, correct (with note). Each action writes
   review_actions + audit_log and appends to the receipt's correction_history.
2. Corrections: /api/corrections creates a correction_requests row; admin view to triage; on acceptance the
   affected cache entries are invalidated (cache.invalidate by normalized query and source) and the receipt is
   re-verified on next search. Section I of the report lists corrections and business responses with timestamps.
3. Re-render: approved receipts move into the published set; reports regenerate from stored receipts.
4. Tests: pending never appears in published output; approval publishes; correction invalidates cache and is
   shown in section I.
```
**Done when:** an adverse fixture finding is invisible until approved, then appears with its citation; a correction round-trip shows in section I.

---

## Slice 7 — Deploy 🔑

**Prompt**
```
Goal: a preview deployment, then production, on Vercel. Stop and ask before production.

1. vercel.json: functions."api/main.py".maxDuration 60; headers for public/ matching the API security headers;
   cleanUrls true.
2. Environment variables (Preview and Production separately): GSG_TRACE_DATABASE_URL, SUPABASE_URL,
   SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET, GSG_TRACE_LOG_SALT (random 32 bytes), GSG_TRACE_NH_SOS_APPROVED,
   GSG_TRACE_CORS_ORIGINS. None committed.
3. Deploy a Preview. Run a smoke script against it: health, unauthenticated 401, signed-in search on the synthetic
   source, rate-limit 429, security headers present.
4. Vercel Firewall: rate-limit rule on /api/search per IP as a second layer; attack challenge mode documented.
5. Report the preview URL and smoke results, then STOP and wait for the owner to approve production.
```
🔑 **Owner, before production:** domain choice, Vercel plan, Supabase plan, the NH data agreement in hand, Terms/Privacy reviewed by counsel, and a correction contact address that a human monitors.
**Failure points:** Preview pointing at the production database (use separate Supabase projects or branches); missing `GSG_TRACE_LOG_SALT` silently falls back to the default salt — make startup fail if it's unset in production.

---

## Slice 8 — More sources (one adapter per slice) 🔑

Each new source repeats the Slice 2 pattern: terms check first → `docs/sources/<source>.md` → adapter with tier, type, approval flag, cost, rate limit → synthetic fixture tests → enabled by env flag. Order by value to consumers:

1. **Trade licensing** — NH licenses some trades (e.g. electricians, plumbers) at the state level; confirm which licensing board publishes lookups and under what terms. `source_type=licensing`, Tier 1. Licensing *status determinations* belong to Compliance Health Check; TRACE only cites the record.
2. **Municipal permits** — city-by-city; start with one city that publishes permit data openly.
3. **Business-controlled sites (Tier 2)** — the contractor's own website: fetch only the page the user supplied, respect robots.txt, extract contact identifiers and "a division of" statements as operational links.
4. **Court outcomes** — only official court record systems with documented access terms; outcomes, not filings alone; always `pending` review.
5. **Reviews/complaints (Tier 3)** — only via licensed APIs; every item `verification_status=allegation`, business response and resolution shown alongside, never summarized into a score.

**Prompt template**
```
Add a source adapter for <SOURCE> following docs/sources/<source>.md (terms already confirmed by the owner).
Tier <N>, source_type <TYPE>, cost <$>/request, rate limit <X>/s, approved via env flag <FLAG>.
Map fields to the record shape FixtureAdapter documents. Raise SourceUnavailable on any HTTP/parse error.
Treat all retrieved text as untrusted (sanitize.clean_untrusted). Add fixtures/<source>_sample.json (synthetic) and
tests: match, no-match (negative cache), outage (not cached), injection text inert, citation completeness.
Update report sections E/F to list this source as checked when it ran, and keep the "not searched" text when it didn't.
```

---

## Slice 9 — Benchmarks (spec Phase 8)

**Prompt**
```
Goal: measurable accuracy and cost so "1% better" has a baseline.
1. benchmarks/cases/*.json: labelled synthetic cases (seed identifiers -> expected entities, expected relationship
   statuses). At least 40 cases including name collisions, shared phones, mergers, contradictions, outages.
2. benchmarks/run.py: runs the engine over all cases with a fixed clock; outputs precision/recall for entity
   resolution, confusion matrix for relationship status, false-positive/false-negative relationship rates,
   citation completeness, requests, cache hit rate, latency p50/p95. Writes benchmarks/results/<date>.json.
3. Fill CacheMetrics.false_positive_rate / false_negative_rate from benchmark output (never from guesses).
4. CI job comparing against the last committed baseline; fail on any increase in false-positive relationships.
```

---

## Slice 10 — Optional integrations (Compliance Health Check, Provenance Shield)

**Prompt**
```
Goal: optional, versioned HTTP integrations; TRACE must work identically when they are absent or failing.
1. gsg_trace/integrations/provenance.py: POST receipt content_hash + source reference to the Provenance Shield
   API (/v1/attest) when GSG_TRACE_PROVENANCE_URL and a scoped token are configured; store the attestation id on
   the receipt. Timeouts 2s; failure = receipt unchanged + note in limitations.
2. gsg_trace/integrations/compliance.py: only when the user explicitly requests a compliance check, send the
   minimal identifiers (registry id, legal name, jurisdiction) to Compliance Health Check /v1/checks and link
   the returned report; never import its code or database.
3. Contract tests against recorded JSON fixtures; integration disabled by default.
```

---

## Launch checklist (owner) 🔑

- [ ] Written terms for every real source in `docs/sources/` (Slice 2/8)
- [ ] Terms of Use + Privacy Policy from counsel (not generated text)
- [ ] Legal review of the use case: publishing consumer-facing reports about businesses — many contractors are sole proprietors, so records can be about individuals; confirm defamation, consumer-reporting and state privacy-law exposure before public launch, and keep the product out of employment/tenant/credit/insurance eligibility uses (spec §15)
- [ ] A monitored corrections inbox and a target response time
- [ ] Separate Preview and Production databases
- [ ] Synthetic banner removed only when no synthetic source is enabled in Production
- [ ] Backups enabled on the production database and a tested restore
- [ ] Monitoring: Vercel runtime errors + a weekly review of the audit log

## Operating cost estimate (at launch scale)

v0.1 itself costs $0 to run (stdlib, local SQLite, no LLM calls, no paid sources). Hosted, the realistic floor is the Vercel and Supabase plans you choose plus the NH bulk-data purchase price (unknown until the SOS replies). There are no per-search API fees until you approve a paid source; the engine refuses paid sources while the budget is $0.
