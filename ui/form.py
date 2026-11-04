"""Mapping between the submission form widgets (session state) and VendorSubmission.

Widget values live in st.session_state under `f_*` keys. Samples and
resubmissions are loaded by writing those keys *before* the widgets render.
This is done via a `pending_load` handoff, so it also works across page switches.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import streamlit as st

from core.schemas import DOC_LABELS, Document, DocType, EntityType, VendorSubmission
from core.validators import INDIAN_STATES

TEXT_FIELDS = [
    "legal_name", "trade_name", "address", "city", "pincode", "contact_name", "contact_email",
    "contact_phone", "gstin", "pan", "bank_account_holder_name", "bank_account_number", "ifsc",
]
DOC_TYPES = list(DocType)


def doc_keys(doc: DocType) -> tuple[str, str, str]:
    """Session keys for one document row: uploaded, has-validity-date, validity date."""
    return f"f_doc_{doc.value}_up", f"f_doc_{doc.value}_has_exp", f"f_doc_{doc.value}_exp"


def all_form_keys() -> list[str]:
    """Every session key that belongs to the form."""
    keys = [f"f_{name}" for name in TEXT_FIELDS] + ["f_entity_type", "f_state"]
    for doc in DOC_TYPES:
        keys.extend(doc_keys(doc))
    return keys


def submission_to_state(sub: VendorSubmission) -> dict[str, Any]:
    """Flatten a submission into form widget values."""
    state: dict[str, Any] = {f"f_{name}": getattr(sub, name) or "" for name in TEXT_FIELDS}
    state["f_entity_type"] = sub.entity_type.value
    state["f_state"] = sub.state if sub.state in INDIAN_STATES else None
    for doc in DOC_TYPES:
        up, has_exp, exp = doc_keys(doc)
        meta = sub.document(doc)
        state[up] = bool(meta and meta.uploaded)
        state[has_exp] = bool(meta and meta.expiry_date)
        state[exp] = (meta.expiry_date if meta and meta.expiry_date else date.today())
    return state


def state_to_submission() -> VendorSubmission:
    """Build a VendorSubmission from the current widget values."""
    ss = st.session_state
    data: dict[str, Any] = {name: ss.get(f"f_{name}", "") for name in TEXT_FIELDS}
    data["entity_type"] = ss.get("f_entity_type") or EntityType.PRIVATE_LIMITED.value
    data["state"] = ss.get("f_state") or ""
    docs = []
    for doc in DOC_TYPES:
        up, has_exp, exp = doc_keys(doc)
        expiry: Optional[date] = ss.get(exp) if ss.get(has_exp) else None
        docs.append(Document(doc_type=doc, uploaded=bool(ss.get(up)), expiry_date=expiry))
    data["documents"] = docs
    return VendorSubmission.model_validate(data)


def request_load(sub: VendorSubmission, label: str, resubmitted_from: Optional[int] = None,
                 note: Optional[str] = None) -> None:
    """Queue a submission to be loaded into the form on the next render."""
    st.session_state["pending_load"] = {
        "state": submission_to_state(sub), "label": label,
        "resubmitted_from": resubmitted_from, "note": note,
    }


def apply_pending_load() -> None:
    """Apply a queued load (sample or resubmission), or restore the form after navigation."""
    ss = st.session_state
    pending = ss.pop("pending_load", None)
    if pending:
        for key, value in pending["state"].items():
            ss[key] = value
        ss["form_source_label"] = pending["label"]
        ss["form_resubmitted_from"] = pending["resubmitted_from"]
        ss["form_note"] = pending["note"]
        ss["show_run_id"] = None  # a freshly loaded form hides the previous run
        ss["form_snapshot"] = pending["state"]
        return
    for key, value in ss.get("form_snapshot", {}).items():  # widget state is dropped on page switch
        if key not in ss:
            ss[key] = value


def snapshot_form() -> None:
    """Remember the form values, so they survive navigating to another page and back."""
    st.session_state["form_snapshot"] = {k: st.session_state[k] for k in all_form_keys()
                                         if k in st.session_state}


def render_form() -> None:
    """The editable submission form, grouped into tabs."""
    company, contact, tax_bank, documents = st.tabs(
        ["🏢 Company", "📍 Address & contact", "🧾 Tax & bank", "📎 Documents"])
    with company:
        c1, c2 = st.columns(2)
        c1.text_input("Legal name *", key="f_legal_name", placeholder="As on certificate of incorporation")
        c2.text_input("Trade name", key="f_trade_name", placeholder="Optional brand / trading name")
        c1.selectbox("Entity type *", [e.value for e in EntityType], key="f_entity_type")
    with contact:
        st.text_input("Registered address *", key="f_address")
        c1, c2, c3 = st.columns(3)
        c1.text_input("City *", key="f_city")
        c2.selectbox("State / UT *", INDIAN_STATES, key="f_state", index=None, placeholder="Select state")
        c3.text_input("PIN code *", key="f_pincode", max_chars=6)
        c1, c2, c3 = st.columns(3)
        c1.text_input("Contact person *", key="f_contact_name")
        c2.text_input("Contact email *", key="f_contact_email")
        c3.text_input("Contact phone *", key="f_contact_phone")
    with tax_bank:
        c1, c2 = st.columns(2)
        c1.text_input("GSTIN *", key="f_gstin", max_chars=15, placeholder="15 characters")
        c2.text_input("PAN *", key="f_pan", max_chars=10, placeholder="10 characters")
        c1, c2, c3 = st.columns([2, 1.4, 1])
        c1.text_input("Bank account holder name *", key="f_bank_account_holder_name")
        c2.text_input("Bank account number *", key="f_bank_account_number")
        c3.text_input("IFSC *", key="f_ifsc", max_chars=11)
    with documents:
        st.caption("Assumption: documents are represented as metadata (uploaded? valid until?). "
                   "File contents are not parsed in this prototype.")
        header = st.columns([2.2, 1, 1.2, 1.6])
        for col, text in zip(header, ["**Document**", "**Uploaded**", "**Has validity date**", "**Valid until**"]):
            col.markdown(text)
        for doc in DOC_TYPES:
            up, has_exp, exp = doc_keys(doc)
            row = st.columns([2.2, 1, 1.2, 1.6], vertical_alignment="center")
            row[0].markdown(DOC_LABELS[doc])
            row[1].checkbox("Uploaded", key=up, label_visibility="collapsed")
            row[2].checkbox("Has validity date", key=has_exp, label_visibility="collapsed")
            row[3].date_input("Valid until", key=exp, label_visibility="collapsed", format="DD/MM/YYYY",
                              min_value=date(2000, 1, 1), max_value=date(2100, 12, 31),
                              disabled=not st.session_state.get(has_exp, False))
