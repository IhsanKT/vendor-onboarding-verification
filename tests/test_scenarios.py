"""End-to-end scenario tests: each sample must yield its expected outcome.

The expectations live here, not in the sample files, so the samples carry no
hidden "answer key" that the app could read.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.decision import decide
from core.llm import draft_communications
from core.pipeline import run_checks
from core.samples import load_registry_seed, load_samples
from core.schemas import DocType, FinalStatus, StageStatus

TODAY = date(2026, 11, 4)
SAMPLES = {s.slug: s for s in load_samples()}
REGISTRY = load_registry_seed()

# slug -> (expected status, expected failing stage, expected finding code)
EXPECTED = {
    "01_happy_path": (FinalStatus.APPROVED, None, None),
    "02_gstin_pan_mismatch": (FinalStatus.REJECTED, "Cross-field consistency", "GSTIN_PAN_MISMATCH"),
    "03_bank_name_close_match": (FinalStatus.APPROVED, None, None),
    "04_bank_name_mismatch": (FinalStatus.REJECTED, "Bank account verification", "BANK_NAME_MISMATCH"),
    "05_duplicate_bank_account": (FinalStatus.REJECTED, "Duplicate & fraud check", "SHARED_BANK_ACCOUNT"),
    "06_missing_expired_doc": (FinalStatus.PENDING, "Completeness", "DOCUMENT_EXPIRED"),
}


def _run(slug: str):
    sub = SAMPLES[slug].submission
    stages = run_checks(sub, REGISTRY, TODAY)
    return sub, stages, decide(stages, sub.display_name)


def test_all_samples_covered() -> None:
    assert set(SAMPLES) == set(EXPECTED)


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_scenario_outcome(slug: str) -> None:
    status, failing_stage, code = EXPECTED[slug]
    _, stages, decision = _run(slug)
    assert decision.status == status, [(s.name, s.status, s.message) for s in stages]
    assert decision.failing_stage == failing_stage
    if code:
        codes = {f.code for s in stages for f in s.findings}
        assert code in codes


def test_gstin_pan_mismatch_is_the_only_problem() -> None:
    """Sample 02 must look perfect everywhere except the cross-field stage."""
    _, stages, _ = _run("02_gstin_pan_mismatch")
    non_pass = {s.name for s in stages if s.status != StageStatus.PASS}
    assert non_pass == {"Cross-field consistency"}


def test_close_match_shows_normalisation_effect() -> None:
    _, stages, _ = _run("03_bank_name_close_match")
    bank = next(s for s in stages if s.key == "bank")
    assert bank.status == StageStatus.PASS
    assert bank.evidence["score_after_normalisation"] >= 90
    assert bank.evidence["score_before_normalisation"] < bank.evidence["score_after_normalisation"]


def test_pending_doc_case_flags_both_documents() -> None:
    _, stages, decision = _run("06_missing_expired_doc")
    assert decision.flagged_stages == ["Completeness", "Document validity"]
    fields = {f.field for s in stages for f in s.findings}
    assert {DocType.CANCELLED_CHEQUE.value, DocType.GST_CERTIFICATE.value} <= fields


def test_resubmission_after_fix_is_approved() -> None:
    """Fixing exactly what the email asked for must flip PENDING -> APPROVED."""
    sub = SAMPLES["06_missing_expired_doc"].submission.model_copy(deep=True)
    for doc in sub.documents:
        if doc.doc_type == DocType.CANCELLED_CHEQUE:
            doc.uploaded = True
        if doc.doc_type == DocType.GST_CERTIFICATE:
            doc.expiry_date = None
    decision = decide(run_checks(sub, REGISTRY, TODAY))
    assert decision.status == FinalStatus.APPROVED


def test_rejection_email_does_not_leak_fraud_logic() -> None:
    for slug in ("02_gstin_pan_mismatch", "04_bank_name_mismatch", "05_duplicate_bank_account"):
        sub, stages, decision = _run(slug)
        comms = draft_communications(sub, stages, decision, api_key=None)
        lowered = comms.vendor_email.lower()
        for word in ("fraud", "mismatch", "duplicate", "diversion", "sapphire", "registry"):
            assert word not in lowered, (slug, word)
        assert decision.reasons[0].message in comms.internal_note


def test_pending_email_lists_each_requested_item() -> None:
    sub, stages, decision = _run("06_missing_expired_doc")
    comms = draft_communications(sub, stages, decision, api_key=None)
    assert comms.source == "template"
    assert "cancelled cheque" in comms.vendor_email.lower()
    assert "gst registration certificate" in comms.vendor_email.lower()


def test_missing_fields_and_bad_formats_are_pending_not_rejected() -> None:
    sub = SAMPLES["01_happy_path"].submission.model_copy(update={"pan": "AAHCK48", "contact_email": ""})
    decision = decide(run_checks(sub, REGISTRY, TODAY))
    assert decision.status == FinalStatus.PENDING


def test_same_pan_new_gstin_is_warn() -> None:
    """A new state branch of an existing vendor: needs review, not rejection."""
    existing = REGISTRY[0]
    sub = SAMPLES["01_happy_path"].submission.model_copy(update={"pan": existing.pan})
    stages = run_checks(sub, REGISTRY, TODAY)
    dup = next(s for s in stages if s.key == "duplicate")
    assert "SAME_PAN_OTHER_GSTIN" in {f.code for f in dup.findings}


def test_registry_gstin_resubmitted_is_rejected() -> None:
    existing = REGISTRY[1]
    sub = SAMPLES["01_happy_path"].submission.model_copy(update={"gstin": existing.gstin})
    stages = run_checks(sub, REGISTRY, TODAY)
    assert "DUPLICATE_GSTIN" in {f.code for s in stages for f in s.findings}
    assert decide(stages).status == FinalStatus.REJECTED
