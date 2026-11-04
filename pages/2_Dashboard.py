"""Dashboard: KPIs, status breakdown and a filterable history of every run."""

from __future__ import annotations

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from core import db
from core.pipeline import STAGES
from ui.render import STATUS_BADGE, step_label

STATUS_ORDER = ["APPROVED", "PENDING", "REJECTED"]
STATUS_COLORS = ["#0ca30c", "#fab219", "#d03b3b"]  # reserved status palette, always shown with labels

st.title("Dashboard")
st.caption("Every verification run is stored, so this doubles as the onboarding audit trail.")

runs = db.list_runs()
if not runs:
    st.info("No runs yet. Go to **New Submission**, load a sample and run verification.", icon="📭")
    if st.button("▶ New submission", type="primary"):
        st.switch_page("pages/1_New_Submission.py")
    st.stop()

df = pd.DataFrame(runs)
counts = df["status"].value_counts().reindex(STATUS_ORDER, fill_value=0)
total = len(df)

# ---- KPI tiles ------------------------------------------------------------- #
k = st.columns(5)
k[0].metric("Total runs", total)
k[1].metric("✅ Approved", int(counts["APPROVED"]))
k[2].metric("⏳ Pending", int(counts["PENDING"]))
k[3].metric("⛔ Rejected", int(counts["REJECTED"]))
k[4].metric("Approval rate", f"{counts['APPROVED'] / total:.0%}")

# ---- Charts ---------------------------------------------------------------- #
c1, c2 = st.columns(2)
with c1:
    step_label("Status breakdown")
    status_df = pd.DataFrame({"Status": STATUS_ORDER, "Runs": counts.values})
    base = alt.Chart(status_df).encode(
        y=alt.Y("Status:N", sort=STATUS_ORDER, title=None, axis=alt.Axis(labelFontSize=12, ticks=False, domain=False)),
        x=alt.X("Runs:Q", title=None, axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.35, domain=False)),
        tooltip=["Status", "Runs"],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, size=26).encode(
        color=alt.Color("Status:N", scale=alt.Scale(domain=STATUS_ORDER, range=STATUS_COLORS), legend=None))
    labels = base.mark_text(align="left", dx=6, color="#374151", fontSize=12).encode(text="Runs:Q")
    st.altair_chart((bars + labels).properties(height=170), width="stretch")
with c2:
    step_label("Where submissions are stopped (first flagged stage)")
    stage_names = [name for _, name, _ in STAGES]
    stopped = df["failing_stage"].dropna().value_counts().reindex(stage_names, fill_value=0)
    stage_df = pd.DataFrame({"Stage": stage_names, "Runs": stopped.values})
    base = alt.Chart(stage_df).encode(
        y=alt.Y("Stage:N", sort=stage_names, title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=220)),
        x=alt.X("Runs:Q", title=None, axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.35, domain=False)),
        tooltip=["Stage", "Runs"],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, size=16, color="#4a6fd1")
    labels = base.mark_text(align="left", dx=6, color="#374151").encode(text="Runs:Q")
    st.altair_chart((bars + labels).properties(height=170), width="stretch")

# ---- Filters + table ------------------------------------------------------- #
step_label("Run history")
f1, f2 = st.columns([2, 3])
status_filter = f1.multiselect("Status", STATUS_ORDER, default=STATUS_ORDER)
search = f2.text_input("Search vendor or GSTIN", placeholder="e.g. Kaveri or 29AAHCK")

view = df[df["status"].isin(status_filter)]
if search:
    needle = search.strip().lower()
    view = view[view["vendor_name"].str.lower().str.contains(needle, regex=False)
                | view["gstin"].fillna("").str.lower().str.contains(needle, regex=False)]

table = pd.DataFrame({
    "Run": view["id"].map(lambda i: f"#{i}"),
    "Time": view["created_at"].map(lambda t: datetime.fromisoformat(t).strftime("%d %b, %H:%M:%S")),
    "Vendor": view["vendor_name"],
    "Entity": view["entity_type"],
    "Status": view["status"].map(STATUS_BADGE),
    "Failing stage": view["failing_stage"].fillna("—"),
    "Duration (ms)": view["duration_ms"].round(1),
    "Input": view["source_label"],
    "Email": view["email_source"].map({"gemini": "✨ Gemini", "template": "📝 Template"}),
})
event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                     selection_mode="single-row", key="runs_table",
                     column_config={"Duration (ms)": st.column_config.NumberColumn(format="%.1f")})

selected = event.selection.rows if event and event.selection else []
chosen_id = int(view.iloc[selected[0]]["id"]) if selected else None
b1, b2 = st.columns([3, 1], vertical_alignment="center")
b1.caption("Select a row to open its full stage-by-stage detail." if not chosen_id
           else f"Selected run #{chosen_id}: {view.iloc[selected[0]]['vendor_name']}")
if b2.button("🔎 Open run detail", type="primary", disabled=chosen_id is None, width="stretch"):
    st.session_state["selected_run_id"] = chosen_id
    st.switch_page("pages/3_Run_Detail.py")
