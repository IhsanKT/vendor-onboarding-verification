"""Aggregates stage results into one explainable onboarding decision.

The rule is intentionally simple, so that anyone in procurement can predict it:
    any FAIL          -> REJECTED  (integrity / fraud / compliance blocker)
    else any WARN     -> PENDING   (vendor must fix or supply something)
    else              -> APPROVED
No LLM is involved. The decision is fully reproducible from the stage results.
"""

from __future__ import annotations

from core.schemas import Decision, FinalStatus, Reason, StageResult, StageStatus


def decide(stages: list[StageResult], vendor_name: str = "The vendor") -> Decision:
    """Turn ordered stage results into a final status, a summary and reasons."""
    status = _final_status(stages)
    reasons = _collect_reasons(stages)
    trigger = {FinalStatus.REJECTED: StageStatus.FAIL, FinalStatus.PENDING: StageStatus.WARN}.get(status)
    failing_stage = next((s.name for s in stages if s.status == trigger), None) if trigger else None
    flagged = [s.name for s in stages if s.status != StageStatus.PASS]
    return Decision(
        status=status,
        summary=_summary(status, vendor_name, reasons, stages),
        reasons=reasons,
        failing_stage=failing_stage,
        flagged_stages=flagged,
    )


def _final_status(stages: list[StageResult]) -> FinalStatus:
    """Worst stage status wins: FAIL beats WARN beats PASS."""
    statuses = {s.status for s in stages}
    if StageStatus.FAIL in statuses:
        return FinalStatus.REJECTED
    if StageStatus.WARN in statuses:
        return FinalStatus.PENDING
    return FinalStatus.APPROVED


def _collect_reasons(stages: list[StageResult]) -> list[Reason]:
    """Every finding becomes a reason tied to its stage. Blockers are listed first."""
    reasons = [Reason(stage=s.name, severity=f.severity, message=f.message)
               for s in stages for f in s.findings]
    return sorted(reasons, key=lambda r: 0 if r.severity == StageStatus.FAIL else 1)


def _summary(status: FinalStatus, vendor: str, reasons: list[Reason], stages: list[StageResult]) -> str:
    """Plain-English one-paragraph summary for a non-technical reader."""
    if status == FinalStatus.APPROVED:
        return (f"{vendor} passed all {len(stages)} verification checks. Tax IDs, bank account "
                "ownership and documents are consistent, and there are no conflicts with existing "
                "vendors. The vendor can be activated for payments.")
    fails = [r for r in reasons if r.severity == StageStatus.FAIL]
    warns = [r for r in reasons if r.severity == StageStatus.WARN]
    if status == FinalStatus.REJECTED:
        extra = f" {_count(len(warns), 'other item')} would also need fixing." if warns else ""
        return (f"{vendor} cannot be onboarded. {_count(len(fails), 'blocking issue')} found, "
                f"starting with: {fails[0].message}.{extra} Do not create a vendor record or "
                "release payments until procurement has investigated.")
    return (f"{vendor} is on hold until {_count(len(warns), 'item')} {'is' if len(warns) == 1 else 'are'} resolved. Nothing suggests "
            "fraud, but the file is incomplete or needs correction. A follow-up request has been "
            "drafted for the vendor.")


def _count(n: int, noun: str) -> str:
    """'1 item' / '2 items'."""
    return f"{n} {noun}{'' if n == 1 else 's'}"