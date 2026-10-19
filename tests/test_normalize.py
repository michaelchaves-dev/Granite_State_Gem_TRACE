import pytest

from gsg_trace.models import IdentifierType
from gsg_trace.normalize import (NormalizationError, name_match_key, normalize_address, normalize_business_name,
                                 normalize_domain, normalize_email, normalize_inputs, normalize_jurisdiction,
                                 normalize_phone, normalize_social)


@pytest.mark.parametrize("raw", ["(603) 555-0101", "603.555.0101", "+1 603 555 0101", "1-603-555-0101",
                                 "6035550101", "603-555-0101 ext. 22", "603 555 0101 x9"])
def test_phone_variants_normalize_to_e164(raw):
    assert normalize_phone(raw) == "+16035550101"


@pytest.mark.parametrize("raw", ["555-0101", "(103) 555-0101", "603-155-0101", "abc", "+12"])
def test_invalid_phones_rejected(raw):
    with pytest.raises(NormalizationError):
        normalize_phone(raw)


def test_international_phone_kept_with_plus():
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


@pytest.mark.parametrize("a,b", [
    ("Granite Peak Roofing LLC", "granite peak roofing, l.l.c."),
    ("GRANITE PEAK ROOFING L.L.C.", "Granite  Peak Roofing llc"),
    ("Smith & Sons Inc.", "smith and sons incorporated"),
    ("Bob's Siding Co", "BOBS SIDING COMPANY"),
])
def test_business_name_case_punctuation_suffix_insensitive(a, b):
    assert normalize_business_name(a) == normalize_business_name(b)


def test_legal_suffix_kept_in_normalized_but_not_match_key():
    assert normalize_business_name("Granite Peak Roofing LLC") != normalize_business_name("Granite Peak Roofing Inc")
    assert name_match_key("Granite Peak Roofing LLC") == name_match_key("Granite Peak Roofing, Inc.")


def test_alternate_spellings_share_match_key():
    assert name_match_key("The Johnson Brothers Construction") == name_match_key("Johnson Bros Const LLC")
    assert name_match_key("DBA Peak Roof Pros") == name_match_key("Peak Roof Pros")


@pytest.mark.parametrize("raw", ["https://www.Example.com/about?x=1", "example.com", "WWW.EXAMPLE.COM.",
                                 "http://user:pw@example.com:8080/"])
def test_domain_normalization(raw):
    assert normalize_domain(raw) == "example.com"


@pytest.mark.parametrize("raw", ["localhost", "not a domain", "http://", "-bad.com"])
def test_invalid_domains(raw):
    with pytest.raises(NormalizationError):
        normalize_domain(raw)


def test_email_and_social():
    assert normalize_email("mailto:Office@WWW.Example.com") == "office@example.com"
    with pytest.raises(NormalizationError):
        normalize_email("a@@b.com")
    assert normalize_social("facebook.com/GranitePeak/") == "facebook.com/granitepeak"


def test_address_normalization():
    assert normalize_address("12 Main Street, Suite #4, Concord, NH 03301-1234") == \
        normalize_address("12 MAIN ST STE UNIT 4 CONCORD NH 03301")  # '#'->unit, suite->ste
    assert normalize_address("40 River Road") == "40 RIVER RD"


def test_jurisdiction():
    assert normalize_jurisdiction("us-nh") == "NH"
    assert normalize_jurisdiction(None) is None
    with pytest.raises(NormalizationError):
        normalize_jurisdiction("New Hampshire")


def test_inputs_dedupe_preserves_original_values():
    ids, rejected = normalize_inputs([("phone", "(603) 555-0101"), ("phone", "603.555.0101"),
                                      ("business_name", "Peak Roof Pros"), ("phone", "bad")])
    assert len(ids) == 2
    phone = next(i for i in ids if i.type is IdentifierType.PHONE)
    assert phone.originals == ("(603) 555-0101", "603.555.0101")
    assert rejected and rejected[0]["type"] == "phone"


def test_input_length_and_control_chars():
    with pytest.raises(NormalizationError):
        normalize_business_name("x" * 600)
    assert normalize_business_name("Peak\x00 Roof​ Pros") == "peak roof pros"
