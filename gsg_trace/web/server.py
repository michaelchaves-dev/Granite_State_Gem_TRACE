"""Local development web UI + JSON API (stdlib only).

  python -m gsg_trace serve --source-fixture fixtures/synthetic_nh_registry.json

Binds to 127.0.0.1 by default. This is a dev server, not a production host:
production runs the same engine behind FastAPI (docs/CURSOR_BUILD_SLICES.md, Slice 4).
"""
from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Callable

from ..engine import DiscoveryEngine, SearchRequest
from ..models import IdentifierType
from ..receipts import build_receipts
from ..report import render_json, render_markdown

log = logging.getLogger("gsg_trace.web")
MAX_BODY = 16 * 1024
ALLOWED_FIELDS = {t.value for t in IdentifierType}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                               "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def parse_search_body(body: dict[str, Any]) -> SearchRequest:
    """Validate the API payload: {"identifiers": {"phone": "...", ...}, "jurisdiction": "NH"}."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    ids = body.get("identifiers")
    if not isinstance(ids, dict) or not ids:
        raise ValueError("identifiers must be a non-empty object")
    inputs: list[tuple[str, str]] = []
    for k, v in ids.items():
        if k not in ALLOWED_FIELDS:
            raise ValueError(f"unsupported identifier type: {k}")
        values = v if isinstance(v, list) else [v]
        for item in values:
            if not isinstance(item, str):
                raise ValueError(f"{k} must be a string")
            if item.strip():
                inputs.append((k, item))
    if not inputs:
        raise ValueError("enter at least one identifier")
    jur = body.get("jurisdiction")
    if jur is not None and not isinstance(jur, str):
        raise ValueError("jurisdiction must be a string")
    return SearchRequest(inputs=inputs, jurisdiction=jur or None)


def make_handler(engine_factory: Callable[[], DiscoveryEngine]) -> type[BaseHTTPRequestHandler]:
    page = resources.files("gsg_trace.web").joinpath("static/index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "GSG-TRACE"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # keep request lines (which carry no query data) out of stderr noise
            log.info("%s %s", self.command, self.path.split("?")[0])

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True, "version": "0.1.0"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?")[0] != "/api/search":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "use application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid body size"})
            try:
                req = parse_search_body(json.loads(self.rfile.read(length)))
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
            try:
                res = engine_factory().run(req)
                book = build_receipts(res)
                payload = json.loads(render_json(res, book))
                payload["markdown"] = render_markdown(res, book)
            except Exception:  # never leak internals to the client
                log.exception("search failed")
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "search failed"})
            self._json(HTTPStatus.OK, payload)

    return Handler


def serve(engine_factory: Callable[[], DiscoveryEngine], host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(engine_factory))
    print(f"GSG TRACE dev server on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
