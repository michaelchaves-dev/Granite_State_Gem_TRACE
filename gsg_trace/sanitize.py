"""Untrusted-content handling (spec §15: retrieved pages are data, not instructions).

v0.1 performs deterministic extraction only, so retrieved text is never executed
as a prompt. These helpers still (a) neutralize text before it reaches a report
or any future LLM step and (b) flag instruction-like content for reviewers.
"""
from __future__ import annotations

import re

from .normalize import _CONTROL
from .privacy import redact_sensitive

_INJECTION_PATTERNS = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|directions)|disregard (the )?(previous|above|system)"
    r"|system prompt|you are now|act as (an?|the) |new instructions|developer mode|mark (this|it) (as )?verified"
    r"|<\s*/?\s*(script|system|assistant|instructions?)\b)",
    re.IGNORECASE,
)
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#|!~])")
QUOTE_OPEN, QUOTE_CLOSE = "“", "”"


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_PATTERNS.search(text or ""))


def clean_untrusted(text: object, max_len: int = 300) -> str:
    """Strip control chars, redact sensitive numbers, collapse whitespace, truncate."""
    s = "" if text is None else str(text)
    s = _CONTROL.sub(" ", s)
    s = s.replace(QUOTE_OPEN, '"').replace(QUOTE_CLOSE, '"')
    s = redact_sensitive(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def src(value: object, max_len: int = 300) -> str:
    """Render a source-derived value for a report: cleaned, escaped, and wrapped in curly quotes.

    Curly quotes delimit source content so the neutral-language lint can tell
    engine-authored wording from quoted record content.
    """
    return f"{QUOTE_OPEN}{md_escape(clean_untrusted(value, max_len))}{QUOTE_CLOSE}"
