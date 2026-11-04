"""The ordered verification pipeline.

Each stage is a small pure function: (StageContext) -> StageOutcome. The
`iter_stages` generator runs them in order and yields a timed `StageResult`
after each one, which is what lets the UI render stages live, one by one.

Severity policy (the same everywhere):
  * WARN = something the vendor can fix, or a human should glance at. -> PENDING
  * FAIL = an integrity, fraud or compliance signal. We must not onboard. -> REJECTED
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterator, Optional

from core import validators as v
from core.name_match import best_match, normalise_name
from core.schemas import (
    DOC_LABELS,
    DocType,
    EntityType,
    ExistingVendor,
    Finding,
    StageResult,
    StageStatus,
    VendorSubmission,
)

# --------------------------------------------------------------------------- #
# Configuration (business thresholds, kept in one place so they're explainable)
# --------------------------------------------------------------------------- #
NAME_MATCH_PASS = 90.0  # at or above: same entity, cosmetic differences only
NAME_MATCH_WARN = 70.0  # 70-89: plausibly same entity, needs a bank letter
EXPIRY_SOON_DAYS = 30  # flagged in evidence so ops can chase renewals early

FIELD_LABELS: dict[str, str] = {
    "legal_name": "Legal name",
    "address": "Registered address",
    "city": "City",
    "state": "State",
    "pincode": "PIN code",
    "contact_name": "Contact person name",
    "contact_email": "Contact email",
    "contact_phone": "Contact phone",
    "gstin": "GSTIN",
    "pan": "PAN",
    "bank_account_holder_name": "Bank account holder name",
    "bank_account_number": "Bank account number",
    "ifsc": "IFSC code",
}

REQUIRED_DOCS: dict[EntityType, list[DocType]] = {
    EntityType.PRIVATE_LIMITED: [DocType.GST_CERTIFICATE, DocType.PAN_CARD,
                                 DocType.CANCELLED_CHEQUE, DocType.INCORPORATION_CERT],
    EntityType.PUBLIC_LIMITED: [DocType.GST_CERTIFICATE, DocType.PAN_CARD,
                                DocType.CANCELLED_CHEQUE, DocType.INCORPORATION_CERT],
    EntityType.LLP: [DocType.GST_CERTIFICATE, DocType.PAN_CARD,
                     DocType.CANCELLED_CHEQUE, DocType.INCORPORATION_CERT],
    # Registering a partnership firm is optional in India, so no incorporation cert.
    EntityType.PARTNERSHIP: [DocType.GST_CERTIFICATE, DocType.PAN_CARD, DocType.CANCELLED_CHEQUE],
    EntityType.PROPRIETORSHIP: [DocType.GST_CERTIFICATE, DocType.PAN_CARD, DocType.CANCELLED_CHEQUE],
}

#: Words that a legal name must contain for each entity type (any one of the list).
ENTITY_NAME_MARKERS: dict[EntityType, list[str]] = {
    EntityType.PRIVATE_LIMITED: ["private limited"],
    EntityType.PUBLIC_LIMITED: ["limited"],
    EntityType.LLP: ["llp", "limited liability partnership"],
}


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #
@dataclass
class StageContext:
    """Everything a stage may look at. Passing `today` in keeps tests deterministic."""

    submission: VendorSubmission
    registry: list[ExistingVendor]
    today: date


@dataclass
class StageOutcome:
    """What a stage function returns; timing and naming are added by the runner."""

    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> StageStatus:
        """Stage status is the worst severity among its findings."""
        severities = {f.severity for f in self.findings}
        if StageStatus.FAIL in severities:
            return StageStatus.FAIL
        if StageStatus.WARN in severities:
            return StageStatus.WARN
        return StageStatus.PASS


def _summarise(findings: list[Finding], ok_message: str) -> str:
    """One-line stage message: the single issue, or a count, or the OK text."""
    if not findings:
        return ok_message
    if len(findings) == 1:
        return findings[0].message
    fails = sum(f.severity == StageStatus.FAIL for f in findings)
    warns = len(findings) - fails
    parts = [f"{fails} blocking" if fails else "", f"{warns} to fix" if warns else ""]
    return f"{len(findings)} issues found ({', '.join(p for p in parts if p)})"


def noun(label: str) -> str:
    """Lower-case a label for use mid-sentence, but keep acronyms (GST, PAN, IFSC) intact."""
    first = label.split(" ")[0]
    return label if first.isupper() else label[0].lower() + label[1:]


MATCH_LABELS: dict[str, str] = {"legal_name": "legal name", "trade_name": "trade name",
                                "contact_name": "proprietor (contact name)"}


def _mask(account: str) -> str:
    """Show only the last 4 digits of an account number in vendor-facing text."""
    return f"XXXX{account[-4:]}" if len(account) >= 4 else account


# --------------------------------------------------------------------------- #
# Stage 1 - Completeness
# --------------------------------------------------------------------------- #
def check_completeness(ctx: StageContext) -> StageOutcome:
    """All mandatory fields are filled in and all required documents are uploaded.

    Why: an incomplete file can't be verified at all, and chasing the vendor one
    missing item at a time adds days. We list everything that's missing in a
    single request. Missing items are WARN, because the vendor can fix them.
    """
    sub = ctx.submission
    findings: list[Finding] = []
    missing_fields = [name for name in FIELD_LABELS if not getattr(sub, name)]
    for name in missing_fields:
        findings.append(Finding(
            code="MISSING_FIELD", severity=StageStatus.WARN, field=name,
            message=f"{FIELD_LABELS[name]} is missing",
            vendor_action=f"Please provide your {noun(FIELD_LABELS[name])}.",
        ))

    required = REQUIRED_DOCS[sub.entity_type]
    missing_docs = [d for d in required if not (sub.document(d) and sub.document(d).uploaded)]
    for doc in missing_docs:
        findings.append(Finding(
            code="MISSING_DOCUMENT", severity=StageStatus.WARN, field=doc.value,
            message=f"{DOC_LABELS[doc]} not uploaded",
            vendor_action=_doc_request(doc, sub),
        ))

    evidence = {
        "required_fields_checked": len(FIELD_LABELS),
        "missing_fields": [FIELD_LABELS[n] for n in missing_fields],
        "entity_type": sub.entity_type.value,
        "required_documents": [DOC_LABELS[d] for d in required],
        "missing_documents": [DOC_LABELS[d] for d in missing_docs],
    }
    ok = f"All {len(FIELD_LABELS)} required fields and {len(required)} required documents present"
    return StageOutcome(_summarise(findings, ok), evidence, findings)


def _doc_request(doc: DocType, sub: VendorSubmission) -> str:
    """A specific, actionable request for one missing document."""
    if doc == DocType.CANCELLED_CHEQUE:
        acct = f" for the account ending {sub.bank_account_number[-4:]}" if sub.bank_account_number else ""
        return (f"Please upload a cancelled cheque{acct}, showing the account holder name "
                "(or a bank confirmation letter on the bank's letterhead).")
    if doc == DocType.INCORPORATION_CERT:
        return "Please upload your certificate of incorporation (issued by the MCA / Registrar of Companies)."
    if doc == DocType.GST_CERTIFICATE:
        return "Please upload your GST registration certificate (Form GST REG-06)."
    return f"Please upload a copy of your {noun(DOC_LABELS[doc])}."


# --------------------------------------------------------------------------- #
# Stage 2 - Format validation
# --------------------------------------------------------------------------- #
FORMAT_RULES: list[tuple[str, Callable[[str], bool], str]] = [
    ("pan", v.is_valid_pan, "5 letters + 4 digits + 1 letter"),
    ("ifsc", v.is_valid_ifsc, "4 letters + '0' + 6 characters"),
    ("bank_account_number", v.is_valid_account_number, "9-18 digits"),
    ("contact_email", v.is_valid_email, "name@domain.tld"),
    ("contact_phone", v.is_valid_phone, "Indian mobile, 10 digits starting 6-9"),
    ("pincode", v.is_valid_pincode, "6 digits, not starting with 0"),
]


def check_formats(ctx: StageContext) -> StageOutcome:
    """Each identifier has a valid shape, and the GSTIN checksum is correct.

    Why: malformed IDs mean failed TDS filings (PAN), unclaimable GST credit
    (GSTIN) or bounced payments (IFSC / account). These are almost always typos,
    so they're WARN: we ask the vendor to correct them instead of rejecting.
    Empty fields are skipped here, because Completeness already asked for them.
    """
    sub = ctx.submission
    findings: list[Finding] = []
    evidence: dict[str, Any] = {}

    for name, rule, shape in FORMAT_RULES:
        value = getattr(sub, name)
        if not value:
            continue
        ok = rule(value)
        evidence[FIELD_LABELS[name]] = {"value": value, "valid": ok, "expected": shape}
        if not ok:
            findings.append(Finding(
                code="BAD_FORMAT", severity=StageStatus.WARN, field=name,
                message=f"{FIELD_LABELS[name]} '{value}' is not in the expected format ({shape})",
                vendor_action=f"The {FIELD_LABELS[name]} you entered ('{value}') doesn't look right. "
                              f"Please check it and send it again.",
            ))

    if sub.gstin:
        findings.extend(_gstin_format_findings(sub.gstin, evidence))

    if sub.state:
        known = v.is_known_state(sub.state)
        evidence["State"] = {"value": sub.state, "valid": known, "expected": "Indian state / UT name"}
        if not known:
            findings.append(Finding(
                code="UNKNOWN_STATE", severity=StageStatus.WARN, field="state",
                message=f"State '{sub.state}' is not a recognised Indian state / UT",
                vendor_action="Please confirm the state of your registered address.",
            ))

    ok = f"All {len(evidence)} identifiers well-formed; GSTIN checksum valid"
    return StageOutcome(_summarise(findings, ok), evidence, findings)


def _gstin_format_findings(gstin: str, evidence: dict[str, Any]) -> list[Finding]:
    """Check the GSTIN regex first, then the mod-36 check digit."""
    action = (f"The GSTIN you entered ('{gstin}') doesn't appear to be valid. Please check it "
              "against your GST registration certificate and send it again.")
    if not v.is_valid_gstin_format(gstin):
        evidence["GSTIN"] = {"value": gstin, "valid": False, "expected": "15-char GSTN structure"}
        return [Finding(code="BAD_FORMAT", severity=StageStatus.WARN, field="gstin",
                        message=f"GSTIN '{gstin}' does not match the 15-character GSTN structure",
                        vendor_action=action)]
    expected = v.gstin_check_char(gstin[:14])
    ok = expected == gstin[14]
    evidence["GSTIN"] = {"value": gstin, "valid": ok, "checksum_expected": expected,
                         "checksum_found": gstin[14]}
    if ok:
        return []
    return [Finding(code="GSTIN_CHECKSUM", severity=StageStatus.WARN, field="gstin",
                    message=f"GSTIN check digit is '{gstin[14]}' but should be '{expected}' (likely a typo)",
                    vendor_action=action)]


# --------------------------------------------------------------------------- #
# Stage 3 - Cross-field consistency
# --------------------------------------------------------------------------- #
def check_cross_field(ctx: StageContext) -> StageOutcome:
    """Fields that each look valid on their own must agree with each other.

    This is where submissions that "look fine on the surface" are caught. A real
    GSTIN copied from another company, or a state that doesn't match the GST
    registration, passes every format check. It only shows up when the fields
    are compared against each other.
    """
    sub = ctx.submission
    pan_ok = v.is_valid_pan(sub.pan)
    gstin_ok = v.is_valid_gstin_format(sub.gstin)
    checks: list[dict[str, str]] = []
    findings: list[Finding] = []

    def record(check: str, finding: Optional[Finding], detail: str, skipped: bool = False) -> None:
        status = "SKIPPED" if skipped else (finding.severity.value if finding else "PASS")
        checks.append({"check": check, "result": status, "detail": detail})
        if finding:
            findings.append(finding)

    if gstin_ok and pan_ok:
        record("GSTIN embeds PAN", *_rule_gstin_pan(sub))
    else:
        record("GSTIN embeds PAN", None, "GSTIN or PAN malformed - see Format validation", skipped=True)

    if gstin_ok and v.is_known_state(sub.state):
        record("GSTIN state code matches state", *_rule_gstin_state(sub))
    else:
        record("GSTIN state code matches state", None, "GSTIN or state invalid", skipped=True)

    if pan_ok:
        record("PAN holder type matches entity type", *_rule_pan_entity(sub))
        if sub.entity_type != EntityType.PROPRIETORSHIP and sub.legal_name:
            record("PAN name initial matches legal name", *_rule_pan_initial(sub))
    else:
        record("PAN holder type matches entity type", None, "PAN malformed", skipped=True)

    if sub.entity_type in ENTITY_NAME_MARKERS and sub.legal_name:
        record("Legal name suffix matches entity type", *_rule_name_suffix(sub))

    if v.is_valid_pincode(sub.pincode) and v.is_known_state(sub.state):
        record("PIN code zone matches state", *_rule_pincode_state(sub))

    skipped = [c["check"] for c in checks if c["result"] == "SKIPPED"]
    if skipped:
        findings.append(Finding(
            code="CROSS_CHECK_SKIPPED", severity=StageStatus.WARN,
            message=f"{len(skipped)} cross-check(s) could not run until IDs are corrected",
        ))
    ok = f"All {len(checks)} cross-field checks consistent"
    return StageOutcome(_summarise(findings, ok), {"checks": checks}, findings)


def _rule_gstin_pan(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """GSTIN characters 3-12 must be the vendor's PAN.

    Why: a GSTIN is issued *against* a PAN. If they differ, the GST number
    belongs to a different legal entity. Either it was borrowed, which is a fraud
    signal, or the invoices will carry another company's tax identity, which
    puts our input tax credit at risk. Both are blocking -> FAIL.
    """
    embedded = sub.gstin[2:12]
    detail = f"GSTIN[3-12] = {embedded}, declared PAN = {sub.pan}"
    if embedded == sub.pan:
        return None, detail
    return Finding(code="GSTIN_PAN_MISMATCH", severity=StageStatus.FAIL, field="gstin",
                   message=f"GSTIN {sub.gstin} is registered to PAN {embedded}, not the declared PAN {sub.pan}"), detail


def _rule_gstin_state(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """The GSTIN's first two digits (state code) must match the declared state.

    Why: GST registration is state-specific. A Karnataka GSTIN for a vendor that
    says it's in Delhi means the GSTIN isn't theirs, or the tax will be charged
    under the wrong regime (IGST vs CGST+SGST). Either way, compliance breaks -> FAIL.
    """
    code = sub.gstin[:2]
    gst_state = v.state_for_gst_code(code)
    detail = f"GSTIN state code {code} = {gst_state or 'unknown'}, declared state = {sub.state}"
    if gst_state == sub.state:
        return None, detail
    return Finding(code="GSTIN_STATE_MISMATCH", severity=StageStatus.FAIL, field="state",
                   message=f"GSTIN state code {code} ({gst_state or 'invalid code'}) does not match declared state {sub.state}"), detail


def _rule_pan_entity(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """PAN 4th character must fit the entity type (C company, F firm/LLP, P individual).

    Why: the 4th character is the holder's legal status as recorded by the
    Income Tax department. A 'Private Limited' vendor with an individual's PAN
    is misrepresenting who we'd be contracting with, and it changes the TDS rate.
    -> FAIL.
    """
    expected = v.PAN_ENTITY_CHAR[sub.entity_type]
    actual = sub.pan[3]
    holder = v.PAN_HOLDER_TYPES.get(actual, "unknown")
    detail = f"PAN 4th char '{actual}' ({holder}); {sub.entity_type.value} requires '{expected}'"
    if actual == expected:
        return None, detail
    return Finding(code="PAN_ENTITY_MISMATCH", severity=StageStatus.FAIL, field="pan",
                   message=f"PAN {sub.pan} belongs to a {holder}, but entity type is {sub.entity_type.value}"), detail


def _rule_pan_initial(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """For non-individuals, the PAN's 5th character is the first letter of the entity name.

    Why: this is a cheap signal that the PAN was issued to this entity at all.
    It's a WARN, not a FAIL, because renamed companies keep their old PAN.
    """
    words = normalise_name(sub.legal_name).upper().split()
    if len(words) > 1 and words[0] == "THE":
        initials = {"T", words[1][0]}  # practice varies for names starting with "The"
    else:
        initials = {words[0][0]} if words else set()
    detail = f"PAN 5th char '{sub.pan[4]}', legal name starts with '{'/'.join(sorted(initials))}'"
    if sub.pan[4] in initials:
        return None, detail
    return Finding(code="PAN_INITIAL_MISMATCH", severity=StageStatus.WARN, field="pan",
                   message=f"PAN 5th character '{sub.pan[4]}' doesn't match the legal name's initial",
                   vendor_action=f"Please confirm that PAN {sub.pan} is issued to {sub.legal_name}, and upload "
                                 "the PAN card. If the company was renamed, please include the name-change certificate."), detail


def _rule_name_suffix(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """A company's or LLP's legal name must carry its statutory suffix.

    Why: the Companies Act and the LLP Act require it. A 'Private Limited' with
    no 'Private Limited' in its name usually means the vendor typed its brand
    name instead of its legal name. Contracts and payments must use the legal name.
    """
    markers = ENTITY_NAME_MARKERS[sub.entity_type]
    normalised = normalise_name(sub.legal_name)
    detail = f"'{normalised}' should contain one of {markers}"
    if any(m in normalised for m in markers) and not (
        sub.entity_type == EntityType.PUBLIC_LIMITED and "private limited" in normalised
    ):
        return None, detail
    return Finding(code="NAME_SUFFIX_MISMATCH", severity=StageStatus.WARN, field="legal_name",
                   message=f"Legal name '{sub.legal_name}' lacks the suffix expected for a {sub.entity_type.value}",
                   vendor_action="Please give your full registered legal name exactly as it appears on your "
                                 "certificate of incorporation."), detail


def _rule_pincode_state(sub: VendorSubmission) -> tuple[Optional[Finding], str]:
    """The PIN code's postal zone (first digit) must be consistent with the state.

    Why: it catches a wrong address or a copy-pasted form. It's a WARN, because
    a PIN code typo is common and harmless once corrected.
    """
    zone_states = sorted(v.PINCODE_ZONES.get(sub.pincode[0], set()))
    detail = f"PIN zone {sub.pincode[0]} covers {', '.join(zone_states)}"
    if v.pincode_matches_state(sub.pincode, sub.state):
        return None, detail
    return Finding(code="PINCODE_STATE_MISMATCH", severity=StageStatus.WARN, field="pincode",
                   message=f"PIN code {sub.pincode} is not in {sub.state}",
                   vendor_action=f"Please confirm the PIN code of your registered address in {sub.state}."), detail


# --------------------------------------------------------------------------- #
# Stage 4 - Bank account verification
# --------------------------------------------------------------------------- #
def check_bank_account(ctx: StageContext) -> StageOutcome:
    """The bank account must belong to the vendor: the holder name must match the legal name.

    Why: the most common B2B payment fraud is account substitution. The vendor
    looks real, but the money goes to someone else's account. We fuzzy-match the
    names after normalising them, so that 'Pvt. Ltd.' and 'Private Limited' don't
    trigger false alarms. Thresholds:
      >= 90 PASS (cosmetic differences only)
      70-89 WARN (plausible, e.g. a truncated bank name; ask for a bank letter)
      < 70 FAIL (a different party; possible payment diversion)
    For proprietorships, the account is often in the owner's personal name, so we
    also accept a match against the contact person.
    """
    sub = ctx.submission
    if not sub.bank_account_holder_name or not sub.legal_name:
        f = Finding(code="BANK_NOT_VERIFIED", severity=StageStatus.WARN,
                    message="Bank account holder name could not be verified (missing data)")
        return StageOutcome(f.message, {}, [f])

    candidates = {"legal_name": sub.legal_name}
    if sub.entity_type == EntityType.PROPRIETORSHIP:
        candidates["trade_name"] = sub.trade_name or ""
        candidates["contact_name"] = sub.contact_name
    match = best_match(candidates, sub.bank_account_holder_name)

    evidence = {
        "holder_name_raw": match.holder_raw,
        "holder_name_normalised": match.holder_normalised,
        "matched_against": match.candidate_label,
        "declared_name_raw": match.candidate_raw,
        "declared_name_normalised": match.candidate_normalised,
        "score_before_normalisation": match.raw_score,
        "score_after_normalisation": match.score,
        "thresholds": {"pass": f">= {NAME_MATCH_PASS:.0f}", "warn": f"{NAME_MATCH_WARN:.0f}-{NAME_MATCH_PASS - 1:.0f}",
                       "fail": f"< {NAME_MATCH_WARN:.0f}"},
        "account": _mask(sub.bank_account_number),
        "ifsc": sub.ifsc,
    }
    if match.score >= NAME_MATCH_PASS:
        msg = f"Account holder matches {MATCH_LABELS.get(match.candidate_label, match.candidate_label)} (score {match.score:.0f}/100)"
        return StageOutcome(msg, evidence, [])
    if match.score >= NAME_MATCH_WARN:
        f = Finding(code="BANK_NAME_PARTIAL", severity=StageStatus.WARN, field="bank_account_holder_name",
                    message=f"Account holder '{sub.bank_account_holder_name}' only partially matches "
                            f"'{match.candidate_raw}' (score {match.score:.0f}/100); needs a bank letter",
                    vendor_action=f"The bank account holder name you gave ('{sub.bank_account_holder_name}') "
                                  f"doesn't fully match your legal name ('{sub.legal_name}'). Please send a "
                                  "bank confirmation letter, or a cancelled cheque printed with the account holder name.")
        return StageOutcome(f.message, evidence, [f])
    f = Finding(code="BANK_NAME_MISMATCH", severity=StageStatus.FAIL, field="bank_account_holder_name",
                message=f"Account holder '{sub.bank_account_holder_name}' does not match vendor "
                        f"'{match.candidate_raw}' (score {match.score:.0f}/100) - possible payment diversion")
    return StageOutcome(f.message, evidence, [f])


# --------------------------------------------------------------------------- #
# Stage 5 - Duplicate / fraud check
# --------------------------------------------------------------------------- #
def check_duplicates(ctx: StageContext) -> StageOutcome:
    """Compare the submission against the existing vendor master.

    Why:
      * Same GSTIN already onboarded -> FAIL: a duplicate vendor record is how
        invoices get paid twice.
      * Same bank account (same bank) under a *different* PAN -> FAIL: two
        "different" vendors paid into one account is a classic shell-vendor or
        collusion pattern.
      * Same PAN with a new GSTIN -> WARN: usually a legitimate new state branch
        of a vendor we already use, but procurement should link the records.
    """
    sub = ctx.submission
    findings: list[Finding] = []
    matches: list[dict[str, str]] = []

    for vendor in ctx.registry:
        if sub.gstin and vendor.gstin == sub.gstin:
            matches.append(_match_row("GSTIN", vendor))
            findings.append(Finding(
                code="DUPLICATE_GSTIN", severity=StageStatus.FAIL, field="gstin",
                message=f"GSTIN {sub.gstin} already exists in vendor master as "
                        f"{vendor.vendor_id} {vendor.vendor_name} (onboarded {vendor.onboarded_on})"))
        same_bank = (sub.bank_account_number and vendor.bank_account_number == sub.bank_account_number
                     and vendor.ifsc[:4] == sub.ifsc[:4])
        if same_bank and vendor.pan != sub.pan:
            matches.append(_match_row("Bank account", vendor))
            findings.append(Finding(
                code="SHARED_BANK_ACCOUNT", severity=StageStatus.FAIL, field="bank_account_number",
                message=f"Bank account {_mask(sub.bank_account_number)} ({sub.ifsc[:4]}) is already registered to a "
                        f"different vendor: {vendor.vendor_id} {vendor.vendor_name}"))
        if sub.pan and vendor.pan == sub.pan and vendor.gstin != sub.gstin:
            matches.append(_match_row("PAN", vendor))
            findings.append(Finding(
                code="SAME_PAN_OTHER_GSTIN", severity=StageStatus.WARN, field="pan",
                message=f"PAN {sub.pan} already onboarded under GSTIN {vendor.gstin} "
                        f"({vendor.vendor_id} {vendor.vendor_name}) - likely another branch, needs review",
                vendor_action="Our records show your organisation is already registered with us under another "
                              "GSTIN. Please confirm whether this is a new branch or registration, so we can link the two."))

    evidence = {"vendors_in_master": len(ctx.registry), "matches": matches,
                "keys_compared": ["GSTIN", "PAN", "bank account number + bank (IFSC prefix)"]}
    ok = f"No conflicts across {len(ctx.registry)} existing vendors"
    return StageOutcome(_summarise(findings, ok), evidence, findings)


def _match_row(key: str, vendor: ExistingVendor) -> dict[str, str]:
    """Evidence row describing which existing vendor matched, and on what."""
    return {"matched_on": key, "vendor_id": vendor.vendor_id, "vendor_name": vendor.vendor_name,
            "gstin": vendor.gstin, "onboarded_on": str(vendor.onboarded_on)}


# --------------------------------------------------------------------------- #
# Stage 6 - Document validity
# --------------------------------------------------------------------------- #
def check_documents(ctx: StageContext) -> StageOutcome:
    """Uploaded documents must still be valid on the date we check them.

    Why: an expired certificate, such as an old provisional GST certificate, is
    not evidence of anything today. Auditors will flag a vendor file built on
    lapsed documents. It's a WARN: the vendor just re-uploads a current copy.
    Presence of documents is checked in Completeness. This stage looks only at
    validity, so the vendor isn't asked for the same thing twice.
    """
    sub, today = ctx.submission, ctx.today
    findings: list[Finding] = []
    rows: list[dict[str, str]] = []
    for doc in sub.documents:
        label = DOC_LABELS[doc.doc_type]
        if not doc.uploaded:
            rows.append({"document": label, "status": "not uploaded", "valid_until": "-"})
            continue
        until = str(doc.expiry_date) if doc.expiry_date else "no expiry"
        if doc.expiry_date and doc.expiry_date < today:
            rows.append({"document": label, "status": "EXPIRED", "valid_until": until})
            findings.append(Finding(
                code="DOCUMENT_EXPIRED", severity=StageStatus.WARN, field=doc.doc_type.value,
                message=f"{label} expired on {doc.expiry_date:%d %b %Y}",
                vendor_action=f"The {noun(label)} you uploaded expired on {doc.expiry_date:%d %b %Y}. "
                              "Please upload a current, valid copy."))
        elif doc.expiry_date and doc.expiry_date <= today + timedelta(days=EXPIRY_SOON_DAYS):
            rows.append({"document": label, "status": "valid (expires soon)", "valid_until": until})
        else:
            rows.append({"document": label, "status": "valid", "valid_until": until})

    uploaded = sum(d.uploaded for d in sub.documents)
    evidence = {"checked_on": str(today), "documents": rows}
    ok = f"All {uploaded} uploaded documents are within validity"
    return StageOutcome(_summarise(findings, ok), evidence, findings)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
StageFn = Callable[[StageContext], StageOutcome]

STAGES: list[tuple[str, str, StageFn]] = [
    ("completeness", "Completeness", check_completeness),
    ("format", "Format validation", check_formats),
    ("cross_field", "Cross-field consistency", check_cross_field),
    ("bank", "Bank account verification", check_bank_account),
    ("duplicate", "Duplicate & fraud check", check_duplicates),
    ("documents", "Document validity", check_documents),
]

STAGE_DESCRIPTIONS: dict[str, str] = {
    "completeness": "Are all required fields and documents present?",
    "format": "Are PAN, GSTIN (with checksum), IFSC, email, phone and PIN well-formed?",
    "cross_field": "Do GSTIN, PAN, state and entity type agree with each other?",
    "bank": "Does the bank account belong to this vendor?",
    "duplicate": "Is this GSTIN, PAN or bank account already in our vendor master?",
    "documents": "Are the uploaded documents still valid today?",
}


def iter_stages(
    submission: VendorSubmission,
    registry: list[ExistingVendor],
    today: Optional[date] = None,
) -> Iterator[StageResult]:
    """Run every stage in order, yielding each timed result as soon as it completes.

    All stages run even after a FAIL, so the reviewer gets the complete picture
    and the vendor gets one consolidated request instead of several in a row.
    """
    ctx = StageContext(submission=submission, registry=registry, today=today or date.today())
    for key, name, fn in STAGES:
        start = time.perf_counter()
        outcome = fn(ctx)
        duration = (time.perf_counter() - start) * 1000
        yield StageResult(key=key, name=name, status=outcome.status, message=outcome.message,
                          evidence=outcome.evidence, findings=outcome.findings,
                          duration_ms=round(duration, 2))


def run_checks(
    submission: VendorSubmission,
    registry: list[ExistingVendor],
    today: Optional[date] = None,
) -> list[StageResult]:
    """Convenience wrapper: run all stages and return them as a list."""
    return list(iter_stages(submission, registry, today))


def now() -> datetime:
    """Single source of 'now' for run timestamps."""
    return datetime.now().replace(microsecond=0)
