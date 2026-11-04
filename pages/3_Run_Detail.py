"""Run Detail: full drill-down of one stored run, with replay and resubmit."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from core import db
from core.llm import vendor_actions
from core.schemas import DOC_LABELS, FinalStatus, RunResult
from ui.form import request_load
from ui.layout import demo_delay
from ui.render import STATUS_BADGE, render_run, replay_animated, step_label

ss = st.session_state


def resubmit(result: RunResult) -> None:
    """Load the run's submission into the New Submission form, linked to this run."""
    actions = vendor_actions(result.stages)
    note = "**Requested from the vendor:**\n" + "\n".join(f"- {a}" for a in actions) if actions else None
    request_load(result.submission, label=f"Resubmission of run #{result.run_id}",
                 resubmitted_from=result.run_id, note=note)
    ss["go_to_form"] = True


def submission_table(result: RunResult) -> pd.DataFrame:
    """Submitted data as a readable two-column table (no raw dicts on screen)."""
    s = result.submission
    rows = [
        ("Legal name", s.legal_name), ("Trade name", s.trade_name or "—"), ("Entity type", s.entity_type.value),
        ("Address", f"{s.address}, {s.city}, {s.state} {s.pincode}".strip(", ")),
        ("Contact", f"{s.contact_name} · {s.contact_email} · {s.contact_phone}"),
        ("GSTIN", s.gstin), ("PAN", s.pan),
        ("Bank account", f"{s.bank_account_holder_name} · {s.bank_account_number} · {s.ifsc}"),
    ]
    for d in s.documents:
        status = "uploaded" if d.uploaded else "not uploaded"
        if d.uploaded and d.expiry_date:
            status += f", valid until {d.expiry_date:%d %b %Y}"
        rows.append((DOC_LABELS[d.doc_type], status))
    return pd.DataFrame(rows, columns=["Field", "Submitted value"])


if ss.pop("go_to_form", False):
    st.switch_page("pages/1_New_Submission.py")

st.title("Run detail")
runs = db.list_runs()
if not runs:
    st.info("No runs yet. Run a verification first.", icon="📭")
    st.stop()

ids = [r["id"] for r in runs]
labels = {r["id"]: f"#{r['id']} · {r['vendor_name']} · {STATUS_BADGE[r['status']]} · "
                   f"{datetime.fromisoformat(r['created_at']):%d %b %H:%M:%S}" for r in runs}
default = ss.get("selected_run_id") if ss.get("selected_run_id") in ids else ids[0]
run_id = st.selectbox("Run", ids, index=ids.index(default), format_func=labels.get)
ss["selected_run_id"] = run_id
result = db.get_run(run_id)
if result is None:
    st.error("Run not found.")
    st.stop()

st.subheader(result.submission.display_name)
a1, a2, a3 = st.columns([1, 1, 2])
replay = a1.button("▶ Replay live", width="stretch", help="Re-play the stored stages at demo pace.")
if result.decision.status == FinalStatus.PENDING:
    a2.button("✏️ Fix & resubmit", type="primary", width="stretch", on_click=resubmit, args=(result,))
if result.resubmitted_from:
    a3.caption(f"↩️ This run is a resubmission of run #{result.resubmitted_from}.")
later = [r["id"] for r in runs if r["resubmitted_from"] == run_id]
if later:
    a3.caption(f"↪️ Resubmitted later as run {', '.join(f'#{i}' for i in later)}.")

if replay:
    replay_animated(result, max(demo_delay(), 0.3))
else:
    render_run(result)

step_label("Submitted data")
with st.expander("Show the submission as received", expanded=False):
    st.dataframe(submission_table(result), hide_index=True, width="stretch")
