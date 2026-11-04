"""Glue code: submission -> stages -> decision -> communications -> RunResult.

The UI calls the same pieces step by step, so it can animate them. This module
gives a one-call version for tests, scripts and future API use.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional, Union

from core.decision import decide
from core.llm import _AUTO, draft_communications
from core.pipeline import now, run_checks
from core.schemas import ExistingVendor, RunResult, StageResult, VendorSubmission


def assemble_result(
    submission: VendorSubmission,
    stages: list[StageResult],
    started: float,
    api_key: Union[Optional[str], object] = _AUTO,
    source_label: str = "Manual entry",
    resubmitted_from: Optional[int] = None,
) -> RunResult:
    """Decide, draft communications and package everything as a RunResult."""
    decision = decide(stages, submission.display_name)
    comms = draft_communications(submission, stages, decision, api_key=api_key)
    return RunResult(
        created_at=now(), submission=submission, stages=stages, decision=decision,
        communications=comms, total_duration_ms=round((time.perf_counter() - started) * 1000, 1),
        source_label=source_label, resubmitted_from=resubmitted_from,
    )


def process_submission(
    submission: VendorSubmission,
    registry: list[ExistingVendor],
    today: Optional[date] = None,
    api_key: Union[Optional[str], object] = _AUTO,
    source_label: str = "Manual entry",
) -> RunResult:
    """Run the full process in one call (no persistence)."""
    started = time.perf_counter()
    stages = run_checks(submission, registry, today)
    return assemble_result(submission, stages, started, api_key, source_label)
