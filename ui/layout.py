"""Sidebar controls shared by every page (rendered once from app.py)."""

from __future__ import annotations

import streamlit as st

from core import db
from core.llm import DEFAULT_MODEL, get_api_key


def _reset() -> None:
    """Clear runs, reseed the vendor master and forget the on-screen run."""
    db.reset_demo_data()
    for key in ("show_run_id", "selected_run_id", "replay_run_id"):
        st.session_state.pop(key, None)
    st.session_state["reset_done"] = True


def demo_delay() -> float:
    """Seconds to pause per stage, or 0 when demo pace is off."""
    if not st.session_state.get("demo_pace", True):
        return 0.0
    return float(st.session_state.get("demo_delay", 0.5))


def sidebar() -> None:
    """Demo pace, LLM status and the reset button."""
    ss = st.session_state
    ss.setdefault("demo_pace", True)
    ss.setdefault("demo_delay", 0.5)
    with st.sidebar:
        st.markdown("### Demo controls")
        st.toggle("Demo pace", key="demo_pace",
                  help="Pause between stages so each one is visibly sequential during a live demo.")
        if ss["demo_pace"]:
            st.slider("Seconds per stage", 0.1, 2.0, step=0.1, key="demo_delay")
        st.divider()
        if get_api_key():
            st.markdown(f"✨ **Emails:** Gemini (`{DEFAULT_MODEL}` + backups), template fallback")
        else:
            st.markdown("📝 **Emails:** deterministic template  \n"
                        "<small>Set `GEMINI_API_KEY` to enable Gemini drafting.</small>",
                        unsafe_allow_html=True)
        st.markdown("⚖️ **Decisions:** rule-based only")
        st.divider()
        st.button("🔄 Reset demo data", on_click=_reset, width="stretch",
                  help="Delete all runs and reseed the existing-vendor master.")
        if ss.pop("reset_done", False):
            st.toast("Demo data reset: runs cleared, vendor master reseeded.", icon="🔄")
