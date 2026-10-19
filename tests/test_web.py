import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from gsg_trace.web.server import make_handler, parse_search_body


def test_parse_body_validation():
    req = parse_search_body({"identifiers": {"phone": "603-555-0101", "business_name": ""}, "jurisdiction": "NH"})
    assert req.inputs == [("phone", "603-555-0101")] and req.jurisdiction == "NH"
    for bad in [[], {"identifiers": {}}, {"identifiers": {"ssn": "1"}}, {"identifiers": {"phone": 5}},
                {"identifiers": {"phone": " "}}, {"identifiers": {"phone": "x"}, "jurisdiction": 3}]:
        with pytest.raises(ValueError):
            parse_search_body(bad)


@pytest.fixture
def server(make_engine, registry):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: make_engine([registry])))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def post(url, body, ctype="application/json"):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def test_page_and_security_headers(server):
    with urllib.request.urlopen(server + "/") as r:
        html = r.read().decode()
        assert "GSG" in html and r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_search_api(server):
    status, _, data = post(server + "/api/search",
                           json.dumps({"identifiers": {"phone": "(603) 555-0101"}, "jurisdiction": "NH"}).encode())
    assert status == 200 and data["receipts"]["citation_completeness"] == 1.0
    assert "## H. Source register" in data["markdown"]


def test_api_rejects_bad_requests(server):
    assert post(server + "/api/search", b'{"identifiers":{}}')[0] == 400
    assert post(server + "/api/search", b"x", ctype="text/plain")[0] == 415
    assert post(server + "/api/search", b"{" + b" " * 20000 + b"}")[0] == 413
    assert post(server + "/api/nope", b"{}")[0] == 404
