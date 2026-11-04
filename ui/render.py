"""Rendering of runs: live stage-by-stage execution and static replay share one look."""

from __future__ import annotations

import html
import time
from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

from core import db
from core.decision import decide
from core.llm import draft_communications, get_api_key
from core.pipeline import STAGE_DESCRIPTIONS, STAGES, iter_stages, now
from core.schemas import (Communications, Decision, FinalStatus, RunResult, StageResult, StageStatus,
                          VendorSubmission)

STAGE_ICON = {StageStatus.PASS: "✅", StageStatus.WARN: "⚠️", StageStatus.FAIL: "❌"}
FINAL_STYLE = {
    FinalStatus.APPROVED: {"icon": "✅", "color": "#0ca30c", "bg": "#eaf7ea", "ink": "#0b4d0b"},
    FinalStatus.PENDING: {"icon": "⏳", "color": "#fab219", "bg": "#fff6e0", "ink": "#6b4a00"},
    FinalStatus.REJECTED: {"icon": "⛔", "color": "#d03b3b", "bg": "#fcecec", "ink": "#7a1f1f"},
}
STATUS_BADGE = {"APPROVED": "✅ APPROVED", "PENDING": "⏳ PENDING", "REJECTED": "⛔ REJECTED"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1200px;}
.decision-banner {border-radius: 10px; padding: 1.1rem 1.4rem; margin: 0.6rem 0 1rem 0;
                  border-left: 8px solid var(--c);}
.decision-banner .title {font-size: 1.9rem; font-weight: 700; letter-spacing: .02em; margin: 0;}
.decision-banner .summary {font-size: 1.0rem; margin-top: .35rem; line-height: 1.5;}
.email-box {border: 1px solid #e3e6ea; border-radius: 8px; padding: 1rem 1.2rem; background: #fff;
            font-size: .95rem; line-height: 1.55;}
.email-subject {font-weight: 600; margin-bottom: .6rem; padding-bottom: .5rem; border-bottom: 1px solid #eef0f2;}
.step-label {font-size: .78rem; font-weight: 600; color: #6b7280; text-transform: uppercase;
             letter-spacing: .06em; margin: 1.2rem 0 .3rem 0;}
div[data-testid="stMetric"] {background: #f7f8fa; border: 1px solid #eceef1; border-radius: 10px;
                             padding: .7rem 1rem;}
</style>
"""


def inject_css() -> None:
    """Global styles (called once per run from app.py)."""
    st.markdown(CSS, unsafe_allow_html=True)


def md_escape(text: str) -> str:
    """Escape characters that Streamlit markdown would interpret (incl. $ for LaTeX)."""
    for ch in ("\\", "$", "*", "_", "`", "[", "]", "<", ">"):
        text = text.replace(ch, "\\" + ch)
    return text


def step_label(text: str) -> None:
    """Small uppercase section label."""
    st.markdown(f"<div class='step-label'>{html.escape(text)}</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Stage rendering
# --------------------------------------------------------------------------- #
def stage_label(index: int, stage: StageResult) -> str:
    """Collapsed expander label: icon, stage number and name, one-line message."""
    return f"{STAGE_ICON[stage.status]} **{index}. {stage.name}** — {md_escape(stage.message)}"


def render_stage(index: int, stage: StageResult, container=None) -> None:
    """One stage as an expander: findings, a readable table and the raw evidence."""
    target = container or st
    with target.expander(stage_label(index, stage), expanded=False):
        st.caption(f"{STAGE_DESCRIPTIONS[stage.key]}  ·  {stage.duration_ms:.2f} ms")
        for f in stage.findings:
            st.markdown(f"{STAGE_ICON[f.severity]} {md_escape(f.message)}")
        _stage_detail(stage)
        st.markdown("**Evidence**")
        st.json(stage.evidence, expanded=False)


def _stage_detail(stage: StageResult) -> None:
    """Stage-specific human-readable view of the evidence."""
    ev = stage.evidence
    if stage.key == "completeness":
        c1, c2 = st.columns(2)
        c1.markdown("**Missing fields:** " + (md_escape(", ".join(ev["missing_fields"])) or "none"))
        c2.markdown("**Missing documents:** " + (md_escape(", ".join(ev["missing_documents"])) or "none"))
        st.caption(f"Required for {ev['entity_type']}: {', '.join(ev['required_documents'])}")
    elif stage.key == "format":
        rows = [{"Field": k, "Value": v["value"], "Valid": "✅" if v["valid"] else "⚠️",
                 "Expected": v.get("expected", "15-char GSTN structure + mod-36 checksum")}
                for k, v in ev.items()]
        _table(rows)
    elif stage.key == "cross_field":
        icons = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIPPED": "⏭️"}
        _table([{"Check": c["check"], "Result": f"{icons[c['result']]} {c['result']}", "Detail": c["detail"]}
                for c in ev.get("checks", [])])
    elif stage.key == "bank" and ev:
        _table([
            {"": f"Declared ({ev['matched_against'].replace('_', ' ')})", "As written": ev["declared_name_raw"],
             "Normalised": ev["declared_name_normalised"]},
            {"": "Bank account holder", "As written": ev["holder_name_raw"],
             "Normalised": ev["holder_name_normalised"]},
        ])
        c1, c2 = st.columns(2)
        c1.metric("Similarity as written", f"{ev['score_before_normalisation']:.0f} / 100")
        c2.metric("Similarity after normalisation", f"{ev['score_after_normalisation']:.0f} / 100")
        t = ev["thresholds"]
        st.caption(f"Decision bands (token-sort ratio after normalisation): ✅ PASS {t['pass']}  ·  "
                   f"⚠️ WARN {t['warn']} (ask for bank letter)  ·  ❌ FAIL {t['fail']} (possible payment diversion)")
    elif stage.key == "duplicate":
        if ev.get("matches"):
            _table([{"Matched on": m["matched_on"], "Existing vendor": f"{m['vendor_id']} {m['vendor_name']}",
                     "GSTIN": m["gstin"], "Onboarded": m["onboarded_on"]} for m in ev["matches"]])
        else:
            st.markdown(f"No matches on {', '.join(ev['keys_compared'])} across "
                        f"{ev['vendors_in_master']} existing vendors.")
    elif stage.key == "documents":
        _table([{"Document": d["document"], "Status": d["status"], "Valid until": d["valid_until"]}
                for d in ev.get("documents", [])])


def _table(rows: list[dict]) -> None:
    """Compact read-only table."""
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# --------------------------------------------------------------------------- #
# Decision and communications
# --------------------------------------------------------------------------- #
def render_decision(decision: Decision) -> None:
    """Large coloured banner, then reasons tied to their stages."""
    s = FINAL_STYLE[decision.status]
    st.markdown(
        f"<div class='decision-banner' style='--c:{s['color']}; background:{s['bg']}; color:{s['ink']}'>"
        f"<p class='title'>{s['icon']} {decision.status.value}</p>"
        f"<div class='summary'>{html.escape(decision.summary)}</div></div>",
        unsafe_allow_html=True,
    )
    if decision.reasons:
        st.markdown("**Reasons**")
        for r in decision.reasons:
            st.markdown(f"{STAGE_ICON[r.severity]} **{md_escape(r.stage)}** · {md_escape(r.message)}")


def _email_html(subject: str, body: str) -> str:
    """Render an email safely (vendor-supplied text is HTML-escaped)."""
    return (f"<div class='email-box'><div class='email-subject'>Subject: {html.escape(subject)}</div>"
            f"{html.escape(body).replace(chr(10), '<br>')}</div>")


def render_communications(comms: Communications, status: FinalStatus) -> None:
    """Vendor email and internal note in tabs, with the drafting source made explicit."""
    tab_vendor, tab_internal = st.tabs(["📧 Vendor email", "🔒 Internal note (procurement)"])
    with tab_vendor:
        if comms.source == "gemini":
            st.caption("✨ Drafted by Gemini from vendor-safe action items only, then checked by guardrails.")
        else:
            reason = f" ({comms.llm_error})" if comms.llm_error else ""
            st.caption(f"📝 Deterministic template{md_escape(reason)}.")
        if status == FinalStatus.REJECTED:
            st.caption("Deliberately neutral: the vendor isn't told which control failed.")
        st.markdown(_email_html(comms.vendor_subject, comms.vendor_email), unsafe_allow_html=True)
    with tab_internal:
        st.caption("Exact reasons and recommended next steps. Never sent to the vendor.")
        st.code(comms.internal_note, language=None, wrap_lines=True)


def render_meta(result: RunResult) -> None:
    """One-line run metadata."""
    bits = [f"Run #{result.run_id}" if result.run_id else "Unsaved run",
            result.created_at.strftime("%d %b %Y, %H:%M:%S"),
            f"{result.total_duration_ms:,.0f} ms total",
            f"Input: {result.source_label}"]
    if result.resubmitted_from:
        bits.append(f"Resubmission of run #{result.resubmitted_from}")
    st.caption("  ·  ".join(bits))


# --------------------------------------------------------------------------- #
# Whole runs
# --------------------------------------------------------------------------- #
def render_run(result: RunResult) -> None:
    """Static replay of a stored run, with the same look as the live view."""
    render_meta(result)
    step_label("Verification stages")
    for i, stage in enumerate(result.stages, 1):
        render_stage(i, stage)
    step_label("Decision")
    render_decision(result.decision)
    step_label("Vendor communication")
    render_communications(result.communications, result.decision.status)


def replay_animated(result: RunResult, delay: float) -> None:
    """Re-play a stored run stage by stage (no re-computation) at demo pace."""
    render_meta(result)
    step_label("Verification stages (replay)")
    progress = st.progress(0.0)
    for i, stage in enumerate(result.stages, 1):
        slot = st.empty()
        slot.status(f"**{i}. {stage.name}** — {STAGE_DESCRIPTIONS[stage.key]}", state="running")
        time.sleep(delay)
        render_stage(i, stage, container=slot.container())
        progress.progress(i / len(result.stages), text=f"Stage {i} of {len(result.stages)}")
    progress.empty()
    step_label("Decision")
    render_decision(result.decision)
    step_label("Vendor communication")
    render_communications(result.communications, result.decision.status)


def run_live(sub: VendorSubmission, delay: float, source_label: str,
             resubmitted_from: Optional[int] = None) -> RunResult:
    """Execute the pipeline live, rendering each stage the moment it completes, then persist.

    The recorded duration excludes the demo-pace pauses, so it reflects real processing time.
    """
    started = time.perf_counter()
    paused = 0.0
    registry = db.get_registry()
    st.caption(f"Started {now():%d %b %Y, %H:%M:%S}  ·  Input: {source_label}  ·  "
               f"Vendor master: {len(registry)} existing vendors")
    step_label("Verification stages")
    progress = st.progress(0.0, text="Starting verification…")
    stage_iter = iter_stages(sub, registry, date.today())
    stages: list[StageResult] = []
    for i, (key, name, _) in enumerate(STAGES, 1):
        slot = st.empty()
        slot.status(f"**{i}. {name}** — {STAGE_DESCRIPTIONS[key]}", state="running")
        time.sleep(delay)
        paused += delay
        stage = next(stage_iter)
        stages.append(stage)
        render_stage(i, stage, container=slot.container())
        progress.progress(i / len(STAGES), text=f"Stage {i} of {len(STAGES)} complete")
    progress.empty()

    step_label("Decision")
    with st.spinner("Applying decision rules…"):
        time.sleep(delay)
        paused += delay
        decision = decide(stages, sub.display_name)
    render_decision(decision)

    step_label("Vendor communication")
    key = get_api_key()
    with st.spinner("Drafting vendor email with Gemini…" if key else "Drafting vendor email from template…"):
        comms = draft_communications(sub, stages, decision, api_key=key)
    render_communications(comms, decision.status)

    result = RunResult(created_at=now(), submission=sub, stages=stages, decision=decision,
                       communications=comms, total_duration_ms=round((time.perf_counter() - started - paused) * 1000, 1),
                       source_label=source_label, resubmitted_from=resubmitted_from)
    result.run_id = db.save_run(result)
    return result
