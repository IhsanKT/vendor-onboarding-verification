"""Vendor communication: a Gemini-drafted email with a deterministic template fallback.

Guardrails, by design:
  * The LLM never makes or changes the decision. It only words the email.
  * The LLM only sees vendor-safe action items. Internal fraud reasons are never
    put in the prompt, so it cannot leak them.
  * Its output is checked: it must mention every requested item and must not
    contain sensitive words. Otherwise we fall back to the template.
  * No key, no network, a timeout or any exception -> template. The demo never breaks.
  * The internal procurement note is always template-generated, because it must be exact.
"""

from __future__ import annotations

import os
from typing import Optional, Union

from core.schemas import (
    Communications,
    Decision,
    FinalStatus,
    StageResult,
    StageStatus,
    VendorSubmission,
)

DEFAULT_MODEL = "gemini-2.5-flash"
SENDER_SIGNATURE = "Vendor Onboarding Team\nProcurement Department"
PROCUREMENT_CONTACT = "procurement@buyer.example"

#: Words that must never appear in a vendor-facing email.
BANNED_WORDS = ("fraud", "suspicious", "diversion", "registry", "mismatch", "duplicate",
                "shell", "blacklist", "collusion", "vendor master")

#: Keyword that must appear in the email for each requested field / document.
FIELD_KEYWORDS: dict[str, str] = {
    "GST_CERTIFICATE": "gst", "PAN_CARD": "pan", "CANCELLED_CHEQUE": "cheque",
    "INCORPORATION_CERT": "incorporation", "pan": "pan", "gstin": "gstin", "ifsc": "ifsc",
    "bank_account_number": "account", "bank_account_holder_name": "account",
    "contact_email": "email", "contact_phone": "phone", "pincode": "pin", "legal_name": "name",
    "address": "address", "city": "city", "state": "state", "contact_name": "contact",
}

#: Recommended internal next step per finding code.
NEXT_STEPS: dict[str, str] = {
    "GSTIN_PAN_MISMATCH": "Look up the GSTIN on the GST portal (Search Taxpayer) to see which legal entity it "
                          "belongs to. Do not accept invoices under this GSTIN.",
    "GSTIN_STATE_MISMATCH": "Confirm the vendor's state of registration on the GST portal. Wrong-state GST "
                            "means wrong tax type (IGST vs CGST+SGST) on every invoice.",
    "PAN_ENTITY_MISMATCH": "Ask for constitution documents to establish who the legal counterparty is. "
                           "TDS rates differ by holder type.",
    "BANK_NAME_MISMATCH": "Do NOT pay into this account. Call the vendor on a number taken from an independent "
                          "source (GST portal / existing correspondence), not from this form, to confirm bank details.",
    "SHARED_BANK_ACCOUNT": "Escalate to the finance controller / internal audit. Two vendor records paying into "
                           "one account is a shell-vendor or collusion pattern. Review the existing vendor's history.",
    "DUPLICATE_GSTIN": "Vendor already exists. Point the requester to the existing record instead of creating a "
                       "duplicate (duplicate masters cause duplicate payments).",
    "SAME_PAN_OTHER_GSTIN": "Confirm this is a new GST branch of the existing vendor, then link it to the parent record.",
    "BANK_NAME_PARTIAL": "Activate payments only after a bank confirmation letter (or penny-drop) confirms the holder name.",
    "PAN_INITIAL_MISMATCH": "Check the PAN card copy against the legal name. Ask for a name-change certificate if renamed.",
}

_AUTO = object()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_api_key() -> Optional[str]:
    """Read GEMINI_API_KEY from the environment, then from Streamlit secrets, if present."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        return st.secrets.get("GEMINI_API_KEY")  # type: ignore[no-any-return]
    except Exception:  # no secrets file, or not running under Streamlit
        return None


def draft_communications(
    sub: VendorSubmission,
    stages: list[StageResult],
    decision: Decision,
    api_key: Union[Optional[str], object] = _AUTO,
    model: Optional[str] = None,
) -> Communications:
    """Draft the vendor email (Gemini if available, else template) plus the internal note."""
    actions = vendor_actions(stages)
    subject, body = template_vendor_email(sub, decision, actions)
    note = internal_note(sub, stages, decision, actions)
    key = get_api_key() if api_key is _AUTO else api_key
    if not key:
        return Communications(vendor_subject=subject, vendor_email=body, internal_note=note,
                              source="template", llm_error="No GEMINI_API_KEY configured")
    try:
        text = _call_gemini(str(key), _build_prompt(sub, decision, actions),
                            model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
        llm_subject, llm_body = _parse_and_guard(text, decision, stages, fallback_subject=subject)
        return Communications(vendor_subject=llm_subject, vendor_email=llm_body,
                              internal_note=note, source="gemini")
    except Exception as exc:  # any failure -> deterministic template
        return Communications(vendor_subject=subject, vendor_email=body, internal_note=note,
                              source="template", llm_error=f"{type(exc).__name__}: {exc}"[:300])


def vendor_actions(stages: list[StageResult]) -> list[str]:
    """Vendor-safe requests, de-duplicated, in stage order."""
    seen: list[str] = []
    for stage in stages:
        for finding in stage.findings:
            if finding.vendor_action and finding.vendor_action not in seen:
                seen.append(finding.vendor_action)
    return seen


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def template_vendor_email(sub: VendorSubmission, decision: Decision, actions: list[str]) -> tuple[str, str]:
    """Deterministic vendor email for each outcome."""
    greeting = f"Dear {sub.contact_name or 'Sir/Madam'},"
    name = sub.display_name
    if decision.status == FinalStatus.APPROVED:
        acct = f" ending {sub.bank_account_number[-4:]}" if sub.bank_account_number else ""
        return (f"Welcome aboard: {name} is now a registered vendor", "\n".join([
            greeting, "",
            f"Thank you for completing your vendor registration. {name} has been verified and is now "
            "set up in our system.", "",
            f"- Payments will be made to your registered bank account{acct}.",
            "- Please quote our Purchase Order number on every invoice.",
            "- For your security, any future change to your bank details must be confirmed by a "
            "call-back from our team. We will never ask you to change bank details by email alone.", "",
            "We look forward to working with you.", "", "Kind regards,", SENDER_SIGNATURE]))

    if decision.status == FinalStatus.PENDING:
        items = actions or ["Our team needs to review a few details and will contact you shortly."]
        numbered = [f"{i}. {a}" for i, a in enumerate(items, 1)]
        return (f"Action needed to complete your vendor registration ({name})", "\n".join([
            greeting, "",
            f"Thank you for submitting your vendor registration for {name}. We have reviewed it and need "
            f"the following before we can complete your onboarding:", "", *numbered, "",
            "Please reply to this email with the above. Once we receive everything, we will re-verify "
            "your registration, usually within one business day.", "",
            "Kind regards,", SENDER_SIGNATURE]))

    return (f"Update on your vendor registration ({name})", "\n".join([
        greeting, "",
        f"Thank you for your interest in working with us. We were unable to verify some of the details "
        f"provided in the vendor registration for {name}, so we cannot complete your onboarding at this time.", "",
        f"Please contact our procurement team at {PROCUREMENT_CONTACT}, quoting your GSTIN "
        f"({sub.gstin or 'as submitted'}), and we will advise on the next steps.", "",
        "Kind regards,", SENDER_SIGNATURE]))


def internal_note(sub: VendorSubmission, stages: list[StageResult], decision: Decision,
                  actions: list[str]) -> str:
    """Exact, internal-only note for procurement with the real reasons and next steps."""
    lines = [
        "INTERNAL: Procurement only. Do not forward to the vendor.", "",
        f"Vendor:        {sub.display_name} ({sub.entity_type.value}, {sub.state or 'state not given'})",
        f"GSTIN / PAN:   {sub.gstin or '-'} / {sub.pan or '-'}",
        f"Decision:      {decision.status.value}"
        + (f" (first raised at: {decision.failing_stage})" if decision.failing_stage else ""), "",
    ]
    fails = [r for r in decision.reasons if r.severity == StageStatus.FAIL]
    warns = [r for r in decision.reasons if r.severity == StageStatus.WARN]
    if fails:
        lines += ["Blocking issues:", *[f"  - [{r.stage}] {r.message}" for r in fails], ""]
    if warns:
        lines += ["Items to resolve:", *[f"  - [{r.stage}] {r.message}" for r in warns], ""]
    if decision.status == FinalStatus.PENDING and actions:
        lines += ["Requested from vendor:", *[f"  - {a}" for a in actions], ""]
    codes = [f.code for s in stages for f in s.findings]
    steps = list(dict.fromkeys(NEXT_STEPS[c] for c in codes if c in NEXT_STEPS))
    if steps:
        lines += ["Recommended next steps:", *[f"  - {s}" for s in steps], ""]
    if decision.status == FinalStatus.APPROVED:
        lines += ["All checks passed. Create the vendor record and activate payments."]
    elif decision.status == FinalStatus.REJECTED:
        lines += ["The vendor has been sent a neutral notice that does not disclose the reasons above."]
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
def _build_prompt(sub: VendorSubmission, decision: Decision, actions: list[str]) -> str:
    """Prompt containing ONLY vendor-safe facts. No internal reasons are included."""
    if decision.status == FinalStatus.PENDING:
        task = ("Ask the vendor, politely and specifically, for EVERY item below, as a numbered list. "
                "Keep the specifics (document names, dates, account endings) exactly as given. "
                "Do not add requests that are not on the list.\nItems:\n"
                + "\n".join(f"- {a}" for a in actions))
    elif decision.status == FinalStatus.REJECTED:
        task = ("Tell the vendor neutrally that we were unable to verify some of the details in their "
                f"registration and cannot complete onboarding now. Ask them to contact {PROCUREMENT_CONTACT}, "
                f"quoting their GSTIN {sub.gstin}. Do NOT speculate about or list any reasons.")
    else:
        acct = sub.bank_account_number[-4:] if sub.bank_account_number else "on file"
        task = (f"Write a short welcome email. Mention payments go to the registered account ending {acct}, "
                "invoices must quote our PO number, and bank-detail changes are only accepted after a "
                "call-back from our team.")
    return (
        "You write emails for a company's procurement team to vendors in India. "
        "Tone: professional, warm and concise (under 180 words). Plain text, no markdown.\n"
        f"Vendor: {sub.display_name}. Contact person: {sub.contact_name or 'Sir/Madam'}.\n"
        f"Greet the contact exactly as 'Dear {sub.contact_name or 'Sir/Madam'},' (no Mr/Ms or other titles). "
        "Refer to us only as 'our procurement team' or 'we'. Never use placeholders or square brackets.\n"
        f"{task}\n"
        f"Sign off as:\n{SENDER_SIGNATURE}\n"
        "Output format: first line 'Subject: <subject>', then a blank line, then the body."
    )


def _call_gemini(api_key: str, prompt: str, model: str) -> str:
    """Call Gemini with a short timeout. Raises on any problem."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20_000))
    config_kwargs: dict = {"temperature": 0.2, "max_output_tokens": 1024}
    if "flash" in model:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    response = client.models.generate_content(
        model=model, contents=prompt, config=types.GenerateContentConfig(**config_kwargs))
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Empty response from Gemini")
    return text


def _parse_and_guard(text: str, decision: Decision, stages: list[StageResult],
                     fallback_subject: str) -> tuple[str, str]:
    """Split subject/body and reject output that leaks or omits anything."""
    lines = text.replace("\r\n", "\n").split("\n")
    subject = fallback_subject
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip() or fallback_subject
        lines = lines[1:]
    body = "\n".join(lines).strip().replace("**", "")
    if not 60 <= len(body) <= 3000:
        raise ValueError(f"LLM body length {len(body)} outside bounds")
    if "[" in body or "[" in subject:
        raise ValueError("LLM output contained a placeholder in square brackets")
    lowered = body.lower()
    # Welcome emails may legitimately mention fraud protection; only non-approvals must not leak.
    leaked = [w for w in BANNED_WORDS if w in lowered] if decision.status != FinalStatus.APPROVED else []
    if leaked:
        raise ValueError(f"LLM output contained banned words: {leaked}")
    if decision.status == FinalStatus.PENDING:
        required = {FIELD_KEYWORDS[f.field] for s in stages for f in s.findings
                    if f.vendor_action and f.field in FIELD_KEYWORDS}
        missing = [k for k in required if k not in lowered]
        if missing:
            raise ValueError(f"LLM output omitted requested items: {missing}")
    return subject, body
