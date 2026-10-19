import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gsg_trace.cache import SearchCache
from gsg_trace.engine import SearchRequest
from gsg_trace.privacy import (PRIVATE_REPO_MARKER, PrivacyViolation, assert_private_location, hash_for_log,
                               redact_sensitive)
from gsg_trace.receipts import build_receipts
from gsg_trace.store import RunStore

REPO = Path(__file__).resolve().parent.parent


def test_refuses_data_inside_public_repo():
    with pytest.raises(PrivacyViolation):
        assert_private_location(REPO / "data")
    with pytest.raises(PrivacyViolation):
        SearchCache(REPO / "data" / "cache.sqlite3")
    with pytest.raises(PrivacyViolation):
        RunStore(REPO / "tests")


def test_refuses_any_git_worktree_without_private_marker(tmp_path):
    (tmp_path / "other_repo" / ".git").mkdir(parents=True)
    with pytest.raises(PrivacyViolation):
        assert_private_location(tmp_path / "other_repo" / "data")
    (tmp_path / "other_repo" / PRIVATE_REPO_MARKER).write_text("private")
    assert assert_private_location(tmp_path / "other_repo" / "data")


def test_allows_plain_private_dir(tmp_path):
    assert assert_private_location(tmp_path / "trace-data") == (tmp_path / "trace-data").resolve()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_run_store_permissions_and_delete(tmp_path, make_engine, registry):
    res = make_engine([registry], max_depth=0).run(SearchRequest([("phone", "603-555-0101")], "NH"))
    store = RunStore(tmp_path / "priv")
    path = store.save(res, build_receipts(res))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert store.load(res.run_id)["run"]["run_id"] == res.run_id
    assert store.delete(res.run_id) and not path.exists()
    with pytest.raises(ValueError):
        store.load("../../etc/passwd")


def test_redaction():
    assert "[REDACTED-ID]" in redact_sensitive("SSN 123-45-6789 on file")
    assert "[REDACTED-NUMBER]" in redact_sensitive("card 4111 1111 1111 1111")
    assert redact_sensitive("call 603-555-0101") == "call 603-555-0101"   # business phones untouched


def test_hash_for_log_is_stable_and_opaque():
    assert hash_for_log("phone:+16035550101") == hash_for_log("phone:+16035550101")
    assert "555" not in hash_for_log("phone:+16035550101")


def test_cli_refuses_repo_data_dir():
    out = subprocess.run([sys.executable, "-m", "gsg_trace", "--data-dir", str(REPO / "data"), "cache-stats"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 2 and "refusing" in out.stderr


def test_cli_end_to_end(tmp_path):
    out = subprocess.run([sys.executable, "-m", "gsg_trace", "--data-dir", str(tmp_path / "d"), "search",
                          "--phone", "(603) 555-0101", "--jurisdiction", "NH",
                          "--source-fixture", str(REPO / "fixtures" / "synthetic_nh_registry.json")],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "## H. Source register" in out.stdout
    assert list((tmp_path / "d" / "runs").glob("*.json"))


def test_gitignore_blocks_data_artifacts():
    gi = (REPO / ".gitignore").read_text()
    for pattern in ("*.sqlite3", "*.db", ".env", "runs/", "trace-data/"):
        assert pattern in gi


def test_public_marker_present():
    assert (REPO / ".gsg_trace_public").exists()


def test_fixtures_are_synthetic():
    import json
    for f in (REPO / "fixtures").glob("*.json"):
        data = json.loads(f.read_text())
        assert "SYNTHETIC" in data["_notice"]
        assert "synthetic" in data["source"]["source_name"].lower()
        for rec in data["records"]:
            for p in rec.get("phones", []):
                digits = "".join(c for c in p if c.isdigit())[-7:]
                assert digits.startswith("55501"), f"non-fictional phone in fixture: {p}"
