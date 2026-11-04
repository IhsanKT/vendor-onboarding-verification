"""Entry point: page router, shared sidebar, and the landing page.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from core import db
from core.pipeline import NAME_MATCH_PASS, NAME_MATCH_WARN
from ui.layout import sidebar
from ui.render import inject_css

PROCESS_MAP = f"""
digraph G {{
  rankdir=TB; bgcolor="transparent"; nodesep=0.35; ranksep=0.45; newrank=true;
  node [shape=box, style="rounded,filled", fillcolor="#f4f6f9", color="#c9d1dc",
        fontname="Helvetica", fontsize=12, margin="0.2,0.1", width=2.3];
  edge [color="#8a94a3", fontname="Helvetica", fontsize=10, arrowsize=0.7];

  input [label="Vendor submission\n(form / JSON)", shape=note, fillcolor="#e8eefc", color="#7f9be0"];
  s1 [label="1. Completeness\nrequired fields + documents"];
  s2 [label="2. Format validation\nPAN · GSTIN checksum · IFSC"];
  s3 [label="3. Cross-field consistency\nGSTIN↔PAN · state · entity type"];
  s4 [label="4. Bank account\nname match ≥{NAME_MATCH_PASS:.0f} pass · ≥{NAME_MATCH_WARN:.0f} warn"];
  s5 [label="5. Duplicate & fraud\nvs existing vendor master"];
  s6 [label="6. Document validity\nnothing expired"];
  d  [label="7. Decision\nany FAIL?  any WARN?", shape=diamond, style="filled", fillcolor="#ffffff", width=2.6];
  ok  [label="APPROVED\nwelcome email", fillcolor="#eaf7ea", color="#0ca30c", penwidth=1.6];
  pen [label="PENDING\nemail: exactly what to fix", fillcolor="#fff6e0", color="#fab219", penwidth=1.6];
  rej [label="REJECTED\nneutral email + internal note", fillcolor="#fcecec", color="#d03b3b", penwidth=1.6];

  {{ rank=same; input; s1; s2; s3; }}
  {{ rank=same; s6; s5; s4; }}
  {{ rank=same; ok; pen; rej; }}
  input -> s1 -> s2 -> s3;
  s3 -> s4;
  s5 -> s4 [dir=back]; s6 -> s5 [dir=back];
  s6 -> d;
  d -> ok  [label=" no issues"];
  d -> pen [label=" WARN only"];
  d -> rej [label=" any FAIL"];
  pen -> s1 [style=dashed, label=" 8. vendor fixes & resubmits", constraint=false, color="#c9a227"];
}}
"""


def home() -> None:
    """Landing page for a non-technical buyer."""
    st.title("Vendor Onboarding Verification")
    st.markdown(
        "Before a company pays a new supplier, someone has to confirm the supplier is real, that the tax "
        "details belong to them, and that the bank account is theirs. Today this is a manual inbox exercise. "
        "**This process does it in seconds.** It takes an Indian vendor's onboarding submission, runs six "
        "transparent checks (the same ones an experienced procurement analyst would do), and returns "
        "**Approved**, **Pending** or **Rejected** with the reason for every finding. When something is "
        "missing, it drafts the email to the vendor saying exactly what to send. When something looks "
        "wrong, it alerts procurement without tipping off the sender. Every run is stored as an audit trail."
    )
    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.markdown("#### ✅ Approved")
        st.caption("All checks pass. The vendor gets a welcome email and the record can be activated.")
    with c2.container(border=True):
        st.markdown("#### ⏳ Pending")
        st.caption("Fixable gaps: missing or expired documents, typos in IDs. The vendor is asked for "
                   "exactly what's needed.")
    with c3.container(border=True):
        st.markdown("#### ⛔ Rejected")
        st.caption("Integrity or fraud signal: the GSTIN belongs to someone else, or the bank account isn't "
                   "the vendor's or is shared with another vendor.")

    st.subheader("Process map")
    st.graphviz_chart(PROCESS_MAP, width="content")

    st.subheader("Design principles")
    st.markdown(
        "- **Rules decide, AI writes.** Every decision comes from explainable, deterministic rules. "
        "Gemini is used only to word the vendor email, and the process falls back to a template if it's "
        "unavailable.\n"
        "- **Warn vs fail.** Anything the vendor can fix is *pending*; anything that suggests fraud or "
        "a compliance breach is *rejected*.\n"
        "- **One consolidated ask.** All stages always run, so the vendor gets a single, complete list "
        "instead of several emails in a row.\n"
        "- **Assumptions:** India-only vendors. Documents are represented as metadata (uploaded / valid "
        "until), not parsed files. The vendor master is a local registry seeded with 8 existing vendors."
    )
    c1, c2 = st.columns(2)
    if c1.button("▶ Start a new verification", type="primary", width="stretch"):
        st.switch_page("pages/1_New_Submission.py")
    if c2.button("📊 Open the dashboard", width="stretch"):
        st.switch_page("pages/2_Dashboard.py")


def main() -> None:
    """Configure the app, render the shared sidebar and route to the selected page."""
    st.set_page_config(page_title="Vendor Onboarding · Verification", page_icon="🛡️", layout="wide")
    db.init_db()  # idempotent: creates tables and seeds the vendor master on first run
    inject_css()
    nav = st.navigation([
        st.Page(home, title="Home", icon="🏠", default=True),
        st.Page("pages/1_New_Submission.py", title="New Submission", icon="📝"),
        st.Page("pages/2_Dashboard.py", title="Dashboard", icon="📊"),
        st.Page("pages/3_Run_Detail.py", title="Run Detail", icon="🔎"),
    ])
    sidebar()
    nav.run()


main()
