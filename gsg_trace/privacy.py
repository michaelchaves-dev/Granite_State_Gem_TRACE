"""Private-data isolation (spec §13, §15).

Default-deny rules:
* Investigation data (cache DB, run records) may never live inside this public
  code repository.
* It may not live inside *any* git work tree unless that work tree's root carries
  a `.gsg_trace_private` marker (e.g. a clone of the private GSG_TRACE_Data repo).
* Directories are created 0700 and files 0600 on POSIX.
* Application logs carry hashed identifiers, never raw values.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

PUBLIC_REPO_MARKER = ".gsg_trace_public"
PRIVATE_REPO_MARKER = ".gsg_trace_private"
DATA_DIR_ENV = "GSG_TRACE_DATA_DIR"


class PrivacyViolation(RuntimeError):
    pass


def _git_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _public_repo_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / PUBLIC_REPO_MARKER).exists():
            return parent
    return None


def assert_private_location(path: str | os.PathLike[str]) -> Path:
    """Raise PrivacyViolation unless `path` is an acceptable home for private data."""
    resolved = Path(path).expanduser().resolve()
    if _public_repo_root(resolved) is not None:
        raise PrivacyViolation(f"refusing to store private data inside the public TRACE repository: {resolved}")
    git_root = _git_root(resolved)
    if git_root is not None and not (git_root / PRIVATE_REPO_MARKER).exists():
        raise PrivacyViolation(
            f"refusing to store private data inside git work tree {git_root}; "
            f"add a {PRIVATE_REPO_MARKER} marker only to an access-controlled private repository"
        )
    return resolved


def default_data_dir() -> Path:
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "gsg_trace"


def ensure_private_dir(path: str | os.PathLike[str]) -> Path:
    resolved = assert_private_location(path)
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(resolved, 0o700)
    return resolved


def restrict_file(path: str | os.PathLike[str]) -> None:
    if os.name == "posix" and Path(path).exists():
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def write_private_file(path: str | os.PathLike[str], content: str) -> Path:
    target = Path(path)
    ensure_private_dir(target.parent)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    restrict_file(target)
    return target


def hash_for_log(value: str) -> str:
    """Stable, non-reversible handle for logs. Salt via env to resist dictionary lookups."""
    salt = os.environ.get("GSG_TRACE_LOG_SALT", "gsg-trace-v0")
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()[:12]


_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def redact_sensitive(text: str) -> str:
    """Remove government-ID and payment-card-shaped numbers from free text before storage/display."""
    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[REDACTED-NUMBER]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

    text = _CARD.sub(card, text)
    return _SSN.sub("[REDACTED-ID]", text)
