"""Unit tests for identifier format rules and the GSTIN checksum."""

import pytest

from core import validators as v


# A widely published, checksum-valid example GSTIN.
KNOWN_VALID_GSTIN = "27AAPFU0939F1ZV"


def test_known_gstin_checksum() -> None:
    assert v.gstin_check_char(KNOWN_VALID_GSTIN[:14]) == "V"
    assert v.is_valid_gstin_checksum(KNOWN_VALID_GSTIN)


def test_single_char_typo_breaks_checksum() -> None:
    typo = KNOWN_VALID_GSTIN[:7] + "1" + KNOWN_VALID_GSTIN[8:]  # 0939 -> 0139
    assert v.is_valid_gstin_format(typo)
    assert not v.is_valid_gstin_checksum(typo)


def test_build_gstin_round_trip() -> None:
    gstin = v.build_gstin("29", "AAHCK4821M")
    assert len(gstin) == 15
    assert gstin[2:12] == "AAHCK4821M"
    assert v.is_valid_gstin_checksum(gstin)


@pytest.mark.parametrize("gstin", ["27AAPFU0939F1Z", "27AAPFU0939F0ZV", "AAAPFU0939F1ZV", ""])
def test_gstin_bad_format(gstin: str) -> None:
    assert not v.is_valid_gstin_format(gstin)


@pytest.mark.parametrize(
    "pan,ok",
    [("AAHCK4821M", True), ("AAHCK4821", False), ("aahck4821m", False), ("1AHCK4821M", False)],
)
def test_pan(pan: str, ok: bool) -> None:
    assert v.is_valid_pan(pan) is ok


@pytest.mark.parametrize(
    "ifsc,ok", [("HDFC0001234", True), ("SBIN0ABC123", True), ("HDFC1001234", False), ("HDF0001234", False)]
)
def test_ifsc(ifsc: str, ok: bool) -> None:
    assert v.is_valid_ifsc(ifsc) is ok


@pytest.mark.parametrize(
    "phone,ok",
    [("+91 98450 12345", True), ("09845012345", True), ("9845012345", True), ("12345", False), ("5845012345", False)],
)
def test_phone(phone: str, ok: bool) -> None:
    assert v.is_valid_phone(phone) is ok


def test_email_pincode_account() -> None:
    assert v.is_valid_email("accounts@kaverifoods.in")
    assert not v.is_valid_email("accounts@kaverifoods")
    assert v.is_valid_pincode("560058")
    assert not v.is_valid_pincode("060058")
    assert v.is_valid_account_number("50200012345671")
    assert not v.is_valid_account_number("1234")


def test_state_lookup_and_pincode_zone() -> None:
    assert v.state_for_gst_code("29") == "Karnataka"
    assert v.state_for_gst_code("28") == "Andhra Pradesh"
    assert v.state_for_gst_code("99") is None
    assert v.pincode_matches_state("560058", "Karnataka")
    assert not v.pincode_matches_state("110020", "Karnataka")
