"""New Submission: load a sample or type one in, run verification live, act on the result."""

from __future__ import annotations

import streamlit as st

from core import db
from core.llm import vendor_actions
from core.samples import Sample, load_samples
from core.schemas import FinalStatus, RunResult, VendorSubmission
from ui.form import (
    all_form_keys,
    apply_pending_load,
    render_form,
    request_load,
    snapshot_form,
    state_to_submission,
)
from ui.layout import demo_delay
from ui.render import render_run, run_live, step_label

ss = st.session_state


def load_sample(sample: Sample) -> None:
    """Button callback: put a sample into the form."""
    request_load(sample.submission, label=sample.scenario, note=None)


def clear_form() -> None:
    """Button callback: start from an empty form."""
    request_load(VendorSubmission(), label="Manual entry")


def resubmit(result: RunResult) -> None:
    """Button callback: reload a PENDING run's data so the user can fix it and run again."""
    actions = vendor_actions(result.stages)
    note = "**Requested from the vendor:**\n" + "\n".join(f"- {a}" for a in actions) if actions else None
    request_load(result.submission, label=f"Resubmission of run #{result.run_id}",
                 resubmitted_from=result.run_id, note=note)


def current_source_label() -> str:
    """Input label stored with the run; marks a loaded sample as edited if it was changed."""
    label = ss.get("form_source_label", "Manual entry")
    loaded = ss.get("form_loaded_state")
    if loaded and label != "Manual entry":
        current = {k: ss.get(k) for k in all_form_keys()}
        if any(current.get(k) != v for k, v in loaded.items()):
            return f"{label} (edited)"
    return label


def post_run_actions(result: RunResult) -> None:
    """Follow-up buttons under a finished run."""
    c1, c2 = st.columns(2)
    if result.decision.status == FinalStatus.PENDING:
        c1.button("✏️ Fix & resubmit this vendor", type="primary", width="stretch",
                  on_click=resubmit, args=(result,), key=f"resubmit_{result.run_id}")
    if c2.button("🔎 Open in Run Detail", width="stretch", key=f"open_{result.run_id}"):
        ss["selected_run_id"] = result.run_id
        st.switch_page("pages/3_Run_Detail.py")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
had_pending = "pending_load" in ss
apply_pending_load()
if had_pending:
    ss["form_loaded_state"] = {k: ss.get(k) for k in all_form_keys()}

st.title("New vendor submission")
st.caption("Load one of the prepared scenarios (or type your own), then run verification and watch each "
           "stage execute.")

step_label("1 · Load a sample scenario")
samples = load_samples()
cols = st.columns(3)
for i, sample in enumerate(samples):
    cols[i % 3].button(f"{i + 1} · {sample.scenario}", key=f"sample_{sample.slug}", help=sample.description,
                       on_click=load_sample, args=(sample,), width="stretch")

label = ss.get("form_source_label")
if label and label != "Manual entry":
    match = next((s for s in samples if s.scenario == label), None)
    st.info(f"**Loaded:** {label}" + (f" — {match.description}" if match else ""), icon="📂")
if ss.get("form_note"):
    st.warning(ss["form_note"] + "\n\nUpdate the form (for example, the **📎 Documents** tab), then run "
               "verification again.", icon="✏️")

head = st.columns([4, 1], vertical_alignment="bottom")
with head[0]:
    step_label("2 · Review or edit the submission")
head[1].button("Clear form", on_click=clear_form, width="stretch")
with st.container(border=True):
    render_form()
snapshot_form()

step_label("3 · Run")
run_clicked = st.button("▶ Run verification", type="primary", width="stretch")

if run_clicked:
    try:
        submission = state_to_submission()
    except Exception as exc:  # defensive: the schema is permissive, so this should not happen
        st.error(f"Could not read the form: {exc}")
        st.stop()
    result = run_live(submission, demo_delay(), current_source_label(), ss.get("form_resubmitted_from"))
    ss["show_run_id"] = result.run_id
    ss["form_note"] = None
    st.success(f"Saved as run #{result.run_id}. It now appears on the dashboard.", icon="💾")
    post_run_actions(result)
elif ss.get("show_run_id"):
    stored = db.get_run(ss["show_run_id"])
    if stored:
        render_run(stored)
        post_run_actions(stored)
