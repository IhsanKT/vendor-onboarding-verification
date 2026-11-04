"""Format rules for Indian tax and banking identifiers, plus reference data.

Every rule is a pure function so it can be unit-tested and explained on its
own. The docstrings give the business reason for each rule.
"""

from __future__ import annotations

import re
from typing import Optional

from core.schemas import EntityType

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
ACCOUNT_RE = re.compile(r"^[0-9]{9,18}$")
PHONE_RE = re.compile(r"^(?:\+?91|0)?[6-9][0-9]{9}$")

GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
#: GST state codes -> state / UT name (as published by GSTN).
GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

#: Legacy codes still printed on older registrations and still valid.
LEGACY_STATE_CODES: dict[str, str] = {
    "25": "Dadra and Nagar Haveli and Daman and Diu",  # old Daman & Diu code
    "28": "Andhra Pradesh",  # pre-bifurcation Andhra Pradesh
}

INDIAN_STATES: list[str] = sorted(set(GST_STATE_CODES.values()))

#: First digit of a PIN code identifies the postal zone.
PINCODE_ZONES: dict[str, set[str]] = {
    "1": {"Delhi", "Haryana", "Punjab", "Himachal Pradesh", "Jammu and Kashmir",
          "Chandigarh", "Ladakh"},
    "2": {"Uttar Pradesh", "Uttarakhand"},
    "3": {"Rajasthan", "Gujarat", "Dadra and Nagar Haveli and Daman and Diu"},
    "4": {"Maharashtra", "Madhya Pradesh", "Chhattisgarh", "Goa"},
    "5": {"Andhra Pradesh", "Telangana", "Karnataka", "Puducherry"},
    "6": {"Tamil Nadu", "Kerala", "Puducherry", "Lakshadweep"},
    "7": {"West Bengal", "Odisha", "Assam", "Arunachal Pradesh", "Nagaland",
          "Manipur", "Mizoram", "Tripura", "Meghalaya", "Sikkim",
          "Andaman and Nicobar Islands"},
    "8": {"Bihar", "Jharkhand"},
}

#: The 4th character of a PAN encodes the holder's legal status.
PAN_ENTITY_CHAR: dict[EntityType, str] = {
    EntityType.PRIVATE_LIMITED: "C",
    EntityType.PUBLIC_LIMITED: "C",
    EntityType.LLP: "F",
    EntityType.PARTNERSHIP: "F",
    EntityType.PROPRIETORSHIP: "P",
}

PAN_HOLDER_TYPES: dict[str, str] = {
    "C": "Company", "F": "Firm / LLP", "P": "Individual", "H": "HUF",
    "A": "Association of Persons", "T": "Trust", "B": "Body of Individuals",
    "L": "Local Authority", "J": "Artificial Juridical Person", "G": "Government",
}


# --------------------------------------------------------------------------- #
# Format rules
# --------------------------------------------------------------------------- #
def is_valid_pan(pan: str) -> bool:
    """PAN must be 5 letters, 4 digits, 1 letter.

    Why: PAN is the vendor's income-tax identity. TDS is deducted and reported
    against it, so a malformed PAN means TDS filings fail and higher withholding
    rates apply.
    """
    return bool(PAN_RE.match(pan or ""))


def gstin_check_char(first14: str) -> str:
    """Compute the GSTIN check character (standard GSTN mod-36 algorithm).

    Each of the first 14 characters is mapped to a value 0-35 and multiplied by
    an alternating factor of 1 and 2. Each product is split into quotient and
    remainder modulo 36, and the two are added. The check character is
    (36 - total mod 36) mod 36, mapped back to the charset.
    """
    if len(first14) != 14:
        raise ValueError("GSTIN body must be exactly 14 characters")
    total = 0
    for index, char in enumerate(first14.upper()):
        value = GSTIN_CHARSET.index(char)
        product = value * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_CHARSET[(36 - total % 36) % 36]


def is_valid_gstin_format(gstin: str) -> bool:
    """GSTIN must match the 15-character GSTN structure.

    Why: the GSTIN printed on the vendor's invoices is what lets us claim input
    tax credit. If it's malformed, every invoice from this vendor is
    unclaimable.
    """
    return bool(GSTIN_RE.match(gstin or ""))


def is_valid_gstin_checksum(gstin: str) -> bool:
    """The 15th GSTIN character must equal the computed check character.

    Why: a single mistyped digit can still pass the regex. The checksum catches
    almost all single-character typos and transpositions without any API call.
    """
    if not is_valid_gstin_format(gstin):
        return False
    return gstin_check_char(gstin[:14]) == gstin[14]


def build_gstin(state_code: str, pan: str, entity_number: str = "1") -> str:
    """Build a checksum-valid GSTIN (used to generate test data, never hand-typed)."""
    body = f"{state_code}{pan}{entity_number}Z"
    return body + gstin_check_char(body)


def is_valid_ifsc(ifsc: str) -> bool:
    """IFSC must be 4 bank letters, a literal 0, then 6 branch characters.

    Why: a malformed IFSC causes NEFT/RTGS payments to bounce, which delays the
    vendor's payment and creates reconciliation work.
    """
    return bool(IFSC_RE.match(ifsc or ""))


def is_valid_email(email: str) -> bool:
    """Basic email shape check.

    Why: this address receives POs, remittance advice and our follow-ups. A
    typo here means the vendor never hears back from us.
    """
    return bool(EMAIL_RE.match(email or ""))


def normalise_phone(phone: str) -> str:
    """Strip spaces, hyphens and brackets from a phone number."""
    return re.sub(r"[\s\-()]", "", phone or "")


def is_valid_phone(phone: str) -> bool:
    """Indian mobile number: optional +91/0 prefix, then 10 digits starting 6-9.

    Why: procurement needs to call the vendor back to confirm bank changes. That
    call is a key control against payment-diversion fraud.
    """
    return bool(PHONE_RE.match(normalise_phone(phone)))


def is_valid_pincode(pincode: str) -> bool:
    """Indian PIN code: 6 digits, not starting with 0."""
    return bool(PINCODE_RE.match(pincode or ""))


def is_valid_account_number(account: str) -> bool:
    """Indian bank account numbers are 9-18 digits."""
    return bool(ACCOUNT_RE.match(account or ""))


def is_known_state(state: str) -> bool:
    """The declared state must be a real Indian state / UT name."""
    return state in INDIAN_STATES


# --------------------------------------------------------------------------- #
# Reference lookups used by cross-field rules
# --------------------------------------------------------------------------- #
def state_for_gst_code(code: str) -> Optional[str]:
    """Map a 2-digit GST state code to its state / UT name."""
    return GST_STATE_CODES.get(code) or LEGACY_STATE_CODES.get(code)


def pincode_matches_state(pincode: str, state: str) -> bool:
    """Is the PIN code's postal zone consistent with the declared state?"""
    return state in PINCODE_ZONES.get((pincode or " ")[0], set())
