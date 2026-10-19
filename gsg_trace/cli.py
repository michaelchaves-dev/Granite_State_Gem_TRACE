"""Command-line entry point.

  python -m gsg_trace search --phone "(603) 555-0101" --jurisdiction NH \
      --source-fixture fixtures/synthetic_nh_registry.json
  python -m gsg_trace preflight --name "Granite Peak Roofing" --source-fixture ...
  python -m gsg_trace cache-stats
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .cache import SearchCache
from .engine import DiscoveryEngine, SearchRequest
from .models import SearchLimits
from .privacy import PrivacyViolation, assert_private_location, default_data_dir, ensure_private_dir
from .receipts import build_receipts
from .report import render_json, render_markdown
from .sources import FixtureAdapter
from .store import RunStore

ID_FLAGS = {"name": "business_name", "phone": "phone", "domain": "domain", "email": "email",
            "address": "address", "social": "social_profile", "registry_id": "registry_id"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsg_trace", description="GSG TRACE v0.1 — contractor identity discovery")
    p.add_argument("--data-dir", type=Path, default=None, help="private data dir (default: $GSG_TRACE_DATA_DIR or ~/.local/share/gsg_trace)")
    p.add_argument("--client", default="default", help="cache namespace; results are never shared across clients")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("search", "preflight"):
        s = sub.add_parser(name)
        for flag in ID_FLAGS:
            s.add_argument(f"--{flag.replace('_', '-')}", action="append", default=[])
        s.add_argument("--jurisdiction")
        s.add_argument("--source-fixture", type=Path, action="append", default=[],
                       help="synthetic fixture source (v0.1 has no approved live source)")
        s.add_argument("--max-depth", type=int, default=2)
        s.add_argument("--max-requests", type=int, default=25)
        s.add_argument("--max-seconds", type=float, default=30.0)
        s.add_argument("--min-relevance", type=float, default=0.5)
        s.add_argument("--format", choices=["md", "json"], default="md")
        s.add_argument("--out", type=Path, help="write report to a file (must be outside the public repo)")
        s.add_argument("--no-store", action="store_true", help="do not persist the run record")
    sv = sub.add_parser("serve", help="local dev web UI + JSON API (binds 127.0.0.1)")
    sv.add_argument("--source-fixture", type=Path, action="append", default=[])
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sub.add_parser("cache-stats")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    try:
        data_dir = ensure_private_dir(args.data_dir or default_data_dir())
    except PrivacyViolation as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cache = SearchCache(data_dir / "cache.sqlite3")

    if args.cmd == "cache-stats":
        print(json.dumps(cache.freshness(args.client), indent=2))
        return 0

    if not args.source_fixture:
        print("error: v0.1 has no approved live source adapter; pass --source-fixture <synthetic.json>", file=sys.stderr)
        return 2
    adapters = [FixtureAdapter(p) for p in args.source_fixture]

    if args.cmd == "serve":
        from .web.server import serve
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print("warning: binding beyond localhost exposes an unauthenticated dev server", file=sys.stderr)
        serve(lambda: DiscoveryEngine(adapters, cache, namespace=args.client, limits=SearchLimits(max_seconds=20)),
              args.host, args.port)
        return 0

    inputs = [(id_type, v) for flag, id_type in ID_FLAGS.items() for v in getattr(args, flag)]
    limits = SearchLimits(max_depth=args.max_depth, max_requests=args.max_requests,
                          max_seconds=args.max_seconds, min_relevance=args.min_relevance)
    try:
        engine = DiscoveryEngine(adapters, cache, namespace=args.client, limits=limits)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    req = SearchRequest(inputs=inputs, jurisdiction=args.jurisdiction)

    if args.cmd == "preflight":
        plan, _, _ = engine.preflight(req)
        print(json.dumps(plan.to_dict(), indent=2))
        return 0 if plan.can_proceed else 1

    res = engine.run(req)
    book = build_receipts(res)
    text = render_json(res, book) if args.format == "json" else render_markdown(res, book)
    if not args.no_store:
        path = RunStore(data_dir).save(res, book)
        print(f"run stored privately: {path}", file=sys.stderr)
    if args.out:
        try:
            assert_private_location(args.out.parent)
        except PrivacyViolation as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if res.stop_reason != "preflight_blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
