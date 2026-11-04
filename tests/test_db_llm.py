"""Persistence round-trip and LLM fallback behaviour."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core import db, llm
from core.orchestrator import process_submission
from core.samples import load_samples
from core.schemas import FinalStatus

TODAY = date(2026, 11, 4)


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VENDOR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return tmp_path


def test_seed_save_replay_reset(temp_db: Path) -> None:
    registry = db.get_registry()
    assert len(registry) == 8
    sample = next(s for s in load_samples() if s.slug == "05_duplicate_bank_account")
    result = process_submission(sample.submission, registry, TODAY, api_key=None, source_label=sample.scenario)
    run_id = db.save_run(result)

    replay = db.get_run(run_id)
    assert replay is not None and replay.run_id == run_id
    assert replay.decision.status == FinalStatus.REJECTED
    assert [s.status for s in replay.stages] == [s.status for s in result.stages]
    assert db.list_runs()[0]["failing_stage"] == "Duplicate & fraud check"

    db.reset_demo_data()
    assert db.list_runs() == []
    assert len(db.get_registry()) == 8


def test_llm_failure_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(llm, "_call_gemini", boom)
    sample = next(s for s in load_samples() if s.slug == "06_missing_expired_doc")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "template"
    assert result.communications.llm_error == "Gemini unavailable - template used"
    assert "cheque" in result.communications.vendor_email.lower()


def test_busy_model_falls_through_to_next_model(monkeypatch: pytest.MonkeyPatch) -> None:
    good = ("Subject: Two items needed\n\nDear Rajesh Sharma,\n\n1. Please upload a cancelled cheque for the "
            "account ending 7561.\n2. Your GST registration certificate expired on 30 Jun 2025; please upload "
            "a current copy.\n\nKind regards,\nVendor Onboarding Team")
    calls: list[str] = []

    def flaky(_key: str, _prompt: str, model: str) -> str:
        calls.append(model)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return good

    monkeypatch.setattr(llm, "_call_gemini", flaky)
    sample = next(s for s in load_samples() if s.slug == "06_missing_expired_doc")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "gemini"
    assert result.communications.model == llm.DEFAULT_MODELS[1] == calls[1]


def test_all_models_busy_gives_short_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(*_a, **_k):
        raise RuntimeError("503 UNAVAILABLE {'error': {'code': 503}}")

    monkeypatch.setattr(llm, "_call_gemini", busy)
    sample = next(s for s in load_samples() if s.slug == "01_happy_path")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "template"
    assert result.communications.llm_error == "Gemini busy - template used"


def test_llm_output_that_leaks_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    leaky = "Subject: Hi\n\nWe detected possible fraud because your bank account is a duplicate. " * 2
    monkeypatch.setattr(llm, "_call_gemini", lambda *a, **k: leaky)
    sample = next(s for s in load_samples() if s.slug == "04_bank_name_mismatch")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "template"
    assert "fraud" not in result.communications.vendor_email.lower()


def test_llm_output_that_omits_items_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    vague = "Subject: Docs\n\nDear Rajesh, please send us the remaining paperwork at your convenience. Thanks."
    monkeypatch.setattr(llm, "_call_gemini", lambda *a, **k: vague)
    sample = next(s for s in load_samples() if s.slug == "06_missing_expired_doc")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "template"


def test_llm_output_with_placeholder_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    templated = ("Subject: Update from [Your Company Name]\n\nDear Karthik Subramanian, we were unable to verify "
                 "some details. Please contact procurement@buyer.example. Regards, [Your Name]")
    monkeypatch.setattr(llm, "_call_gemini", lambda *a, **k: templated)
    sample = next(s for s in load_samples() if s.slug == "04_bank_name_mismatch")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "template"


def test_good_llm_output_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    good = ("Subject: Two items needed\n\nDear Rajesh Sharma,\n\n1. Please upload a cancelled cheque for the "
            "account ending 7561.\n2. Your GST registration certificate expired on 30 Jun 2025; please upload "
            "a current copy.\n\nKind regards,\nVendor Onboarding Team")
    monkeypatch.setattr(llm, "_call_gemini", lambda *a, **k: good)
    sample = next(s for s in load_samples() if s.slug == "06_missing_expired_doc")
    result = process_submission(sample.submission, [], TODAY, api_key="fake-key")
    assert result.communications.source == "gemini"
    assert result.communications.vendor_subject == "Two items needed"
