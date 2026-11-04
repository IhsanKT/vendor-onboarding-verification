"""Headless UI smoke tests using Streamlit's AppTest: every page renders and the demo flow works."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("VENDOR_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.toggle(key="demo_pace").set_value(False).run()
    return at


def _run_sample(at: AppTest, slug: str) -> None:
    at.switch_page("pages/1_New_Submission.py").run()
    at.button(key=f"sample_{slug}").click().run()
    next(b for b in at.button if b.label == "▶ Run verification").click().run()
    assert not at.exception, at.exception


def _banner(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if "decision-banner" in m.value)


def test_home_renders(app: AppTest) -> None:
    assert not app.exception
    assert app.title[0].value == "Vendor Onboarding Verification"


@pytest.mark.parametrize("slug,status", [
    ("01_happy_path", "APPROVED"), ("02_gstin_pan_mismatch", "REJECTED"),
    ("03_bank_name_close_match", "APPROVED"), ("04_bank_name_mismatch", "REJECTED"),
    ("05_duplicate_bank_account", "REJECTED"), ("06_missing_expired_doc", "PENDING"),
])
def test_each_sample_runs_in_ui(app: AppTest, slug: str, status: str) -> None:
    _run_sample(app, slug)
    assert status in _banner(app)
    assert len(app.expander) >= 6  # one per stage


def test_pending_resubmit_flow_becomes_approved(app: AppTest) -> None:
    _run_sample(app, "06_missing_expired_doc")
    next(b for b in app.button if "Fix & resubmit" in b.label).click().run()
    assert any("Requested from the vendor" in w.value for w in app.warning)
    app.checkbox(key="f_doc_CANCELLED_CHEQUE_up").check().run()
    app.checkbox(key="f_doc_GST_CERTIFICATE_has_exp").uncheck().run()
    next(b for b in app.button if b.label == "▶ Run verification").click().run()
    assert "APPROVED" in _banner(app)
    runs = db.list_runs()
    assert runs[0]["resubmitted_from"] == runs[1]["id"]


def test_dashboard_and_detail_render(app: AppTest) -> None:
    _run_sample(app, "01_happy_path")
    _run_sample(app, "04_bank_name_mismatch")
    app.switch_page("pages/2_Dashboard.py").run()
    assert not app.exception
    assert app.metric[0].value == "2"
    app.switch_page("pages/3_Run_Detail.py").run()
    assert not app.exception
    assert "REJECTED" in _banner(app)
