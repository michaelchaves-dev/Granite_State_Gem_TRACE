"""Deterministic identifier normalization (spec §3).

Normalization produces a *matching key*. It never decides that two entities are
the same — matching keys only decide which records are worth retrieving.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable
from urllib.parse import urlsplit

from .models import Identifier, IdentifierType

MAX_INPUT_LEN = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁯﻿]")


class NormalizationError(ValueError):
    pass


def clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError("identifier must be a string")
    if len(value) > MAX_INPUT_LEN:
        raise NormalizationError(f"identifier exceeds {MAX_INPUT_LEN} characters")
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------- phone
def normalize_phone(raw: str) -> str:
    """Return E.164. NANP numbers are validated; other countries need a leading '+'."""
    text = clean_text(raw)
    text = re.split(r"(?i)\s*(?:ext\.?|extension|x)\s*\d+\s*$", text)[0]
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if has_plus and not digits.startswith("1"):
        if 8 <= len(digits) <= 15:
            return "+" + digits
        raise NormalizationError("international number must have 8-15 digits")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise NormalizationError("US/NANP phone number must have 10 digits")
    if digits[0] in "01" or digits[3] in "01":
        raise NormalizationError("invalid NANP area code or exchange")
    return "+1" + digits


# ---------------------------------------------------------------- business name
_SUFFIXES = {
    "llc": "llc", "lc": "llc", "limited liability company": "llc",
    "pllc": "pllc", "llp": "llp", "lp": "lp", "lllp": "lllp",
    "inc": "inc", "incorporated": "inc",
    "corp": "corp", "corporation": "corp",
    "co": "co", "company": "co",
    "ltd": "ltd", "limited": "ltd",
    "pc": "pc", "pa": "pa",
}
_WORD_ALIASES = {"brothers": "bros", "bro": "bros", "svc": "services", "svcs": "services", "service": "services",
                 "constr": "construction", "const": "construction", "contractors": "contracting",
                 "mtn": "mountain", "mt": "mount"}


def _collapse_single_letters(tokens: list[str]) -> list[str]:
    """Join runs of single-letter tokens: 'l l c' -> 'llc', 'a b c roofing' -> 'abc roofing'."""
    out: list[str] = []
    run: list[str] = []
    for t in tokens:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(t)
    if run:
        out.append("".join(run))
    return out


def _name_tokens(raw: str) -> list[str]:
    text = clean_text(raw).casefold()
    text = re.sub(r"^(?:dba|d/b/a|d\.b\.a\.)\s+", "", text)
    text = text.replace("&", " and ").replace("+", " and ")
    text = re.sub(r"['’`]", "", text)          # bob's -> bobs
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = _collapse_single_letters(text.split())
    if not tokens:
        raise NormalizationError("business name is empty after normalization")
    # Canonicalize a trailing legal-form suffix (possibly two words).
    if len(tokens) >= 3 and " ".join(tokens[-3:]) in _SUFFIXES:
        tokens = tokens[:-3] + [_SUFFIXES[" ".join(tokens[-3:])]]
    elif len(tokens) >= 2 and tokens[-1] in _SUFFIXES:
        tokens[-1] = _SUFFIXES[tokens[-1]]
    return tokens


def normalize_business_name(raw: str) -> str:
    """Case/punctuation-insensitive name with canonical legal suffix. Suffix is kept."""
    return " ".join(_name_tokens(raw))


def name_match_key(raw: str) -> str:
    """Looser key for alternate spellings: no legal suffix, no leading 'the', word aliases applied."""
    tokens = _name_tokens(raw)
    if len(tokens) > 1 and tokens[-1] in set(_SUFFIXES.values()):
        tokens = tokens[:-1]
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    tokens = [_WORD_ALIASES.get(t, t) for t in tokens]
    return " ".join(tokens)


# ---------------------------------------------------------------- domain / email / social
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_domain(raw: str) -> str:
    text = clean_text(raw).strip().lower()
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise NormalizationError("invalid internationalized domain") from exc
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(lbl) for lbl in labels) or labels[-1].isdigit():
        raise NormalizationError("not a valid domain name")
    return host


def normalize_email(raw: str) -> str:
    text = clean_text(raw).lower()
    text = re.sub(r"^mailto:", "", text)
    if text.count("@") != 1:
        raise NormalizationError("email must contain exactly one '@'")
    local, domain = text.split("@")
    if not local or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local) or ".." in local:
        raise NormalizationError("invalid email local part")
    return f"{local}@{normalize_domain(domain)}"


def normalize_social(raw: str) -> str:
    text = clean_text(raw)
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = normalize_domain(parts.hostname or "")
    path = re.sub(r"/+", "/", parts.path or "").rstrip("/").lower()
    if not path:
        raise NormalizationError("social profile URL must include a profile path")
    return f"{host}{path}"


# ---------------------------------------------------------------- address
_ADDR_ABBR = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr", "lane": "ln",
    "boulevard": "blvd", "court": "ct", "place": "pl", "terrace": "ter", "highway": "hwy",
    "parkway": "pkwy", "circle": "cir", "square": "sq", "suite": "ste", "apartment": "apt",
    "building": "bldg", "floor": "fl", "north": "n", "south": "s", "east": "e", "west": "w",
    "route": "rte", "post office box": "po box", "p o box": "po box",
}


def normalize_address(raw: str) -> str:
    text = clean_text(raw).lower()
    text = text.replace("#", " unit ")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for long, short in sorted(_ADDR_ABBR.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{long}\b", short, text)
    text = re.sub(r"\b(\d{5})-\d{4}\b", r"\1", text)
    if not re.search(r"\d", text):
        raise NormalizationError("address must contain a street or ZIP number")
    return text.upper()


def normalize_registry_id(raw: str) -> str:
    text = re.sub(r"\s+", "", clean_text(raw)).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._:-]{0,63}", text):
        raise NormalizationError("registry id has unexpected characters")
    return text


def normalize_jurisdiction(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    text = clean_text(raw).upper().replace("US-", "")
    if not re.fullmatch(r"[A-Z]{2}", text):
        raise NormalizationError("jurisdiction must be a 2-letter state code, e.g. NH")
    return text


_NORMALIZERS = {
    IdentifierType.BUSINESS_NAME: normalize_business_name,
    IdentifierType.PHONE: normalize_phone,
    IdentifierType.DOMAIN: normalize_domain,
    IdentifierType.EMAIL: normalize_email,
    IdentifierType.ADDRESS: normalize_address,
    IdentifierType.SOCIAL_PROFILE: normalize_social,
    IdentifierType.REGISTRY_ID: normalize_registry_id,
}


def normalize_identifier(id_type: IdentifierType | str, raw: str) -> Identifier:
    id_type = IdentifierType(id_type)
    return Identifier(id_type, _NORMALIZERS[id_type](raw), (clean_text(raw),))


def normalize_inputs(
    inputs: Iterable[tuple[IdentifierType | str, str]],
) -> tuple[list[Identifier], list[dict[str, str]]]:
    """Normalize + dedupe. Original spellings are preserved on the surviving Identifier.

    Returns (identifiers, rejected) where rejected lists inputs that failed validation.
    """
    merged: dict[str, Identifier] = {}
    order: list[str] = []
    rejected: list[dict[str, str]] = []
    for id_type, raw in inputs:
        try:
            ident = normalize_identifier(id_type, raw)
        except (NormalizationError, ValueError) as exc:
            rejected.append({"type": str(getattr(id_type, "value", id_type)), "input": str(raw)[:80], "error": str(exc)})
            continue
        if ident.key in merged:
            prev = merged[ident.key]
            originals = prev.originals + tuple(o for o in ident.originals if o not in prev.originals)
            merged[ident.key] = Identifier(prev.type, prev.normalized, originals)
        else:
            merged[ident.key] = ident
            order.append(ident.key)
    return [merged[k] for k in order], rejected
