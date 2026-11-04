"""Pydantic data models shared by the pipeline, the database layer and the UI.

Design note: the input model (`VendorSubmission`) is deliberately *permissive*.
Empty strings and malformed IDs are allowed through so that the pipeline can
explain, field by field, what is wrong. If pydantic rejected bad input up front,
the vendor would get a stack trace instead of a helpful follow-up email.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class EntityType(str, Enum):
    """Legal constitution of the vendor; drives required docs and PAN checks."""

    PRIVATE_LIMITED = "Private Limited"
    PUBLIC_LIMITED = "Public Limited"
    LLP = "LLP"
    PARTNERSHIP = "Partnership"
    PROPRIETORSHIP = "Proprietorship"


class DocType(str, Enum):
    """Supporting documents a vendor can submit (represented as metadata only)."""

    GST_CERTIFICATE = "GST_CERTIFICATE"
    PAN_CARD = "PAN_CARD"
    CANCELLED_CHEQUE = "CANCELLED_CHEQUE"
    INCORPORATION_CERT = "INCORPORATION_CERT"


DOC_LABELS: dict[DocType, str] = {
    DocType.GST_CERTIFICATE: "GST registration certificate",
    DocType.PAN_CARD: "PAN card",
    DocType.CANCELLED_CHEQUE: "Cancelled cheque",
    DocType.INCORPORATION_CERT: "Certificate of incorporation",
}


class StageStatus(str, Enum):
    """Outcome of a single verification stage."""

    PASS = "PASS"
    WARN = "WARN"  # fixable by the vendor or needs a human look -> PENDING
    FAIL = "FAIL"  # integrity / fraud / compliance blocker      -> REJECTED


class FinalStatus(str, Enum):
    """Overall onboarding decision."""

    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
class Document(BaseModel):
    """Metadata about one supporting document (we do not parse the file)."""

    doc_type: DocType
    uploaded: bool = False
    expiry_date: Optional[date] = None


class VendorSubmission(BaseModel):
    """A vendor's onboarding form exactly as submitted."""

    legal_name: str = ""
    trade_name: Optional[str] = None
    entity_type: EntityType = EntityType.PRIVATE_LIMITED

    address: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""

    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""

    gstin: str = ""
    pan: str = ""

    bank_account_holder_name: str = ""
    bank_account_number: str = ""
    ifsc: str = ""

    documents: list[Document] = Field(default_factory=list)

    @field_validator(
        "legal_name", "address", "city", "state", "pincode", "contact_name",
        "contact_email", "contact_phone", "bank_account_holder_name", mode="before",
    )
    @classmethod
    def _strip(cls, value: Any) -> str:
        """Trim stray whitespace, which is noise rather than a vendor error."""
        return "" if value is None else str(value).strip()

    @field_validator("trade_name", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Optional[str]:
        """Treat a blank trade name as 'not provided'."""
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("gstin", "pan", "ifsc", mode="before")
    @classmethod
    def _normalise_id(cls, value: Any) -> str:
        """Upper-case tax/bank IDs and drop spaces.

        Lower-case or space-separated IDs are a formatting habit, not a data
        error. Normalising them here keeps the vendor from being chased for
        something that doesn't matter.
        """
        return re.sub(r"\s+", "", "" if value is None else str(value)).upper()

    @field_validator("bank_account_number", mode="before")
    @classmethod
    def _normalise_account(cls, value: Any) -> str:
        """Drop spaces and hyphens people type when copying from a passbook."""
        return re.sub(r"[\s\-]", "", "" if value is None else str(value))

    @property
    def display_name(self) -> str:
        """Best human-readable name for tables and headings."""
        return self.legal_name or self.trade_name or "(unnamed vendor)"

    def document(self, doc_type: DocType) -> Optional[Document]:
        """Return the submitted metadata for one document type, if any."""
        return next((d for d in self.documents if d.doc_type == doc_type), None)


class ExistingVendor(BaseModel):
    """A vendor already in the vendor master (used for duplicate detection)."""

    vendor_id: str
    vendor_name: str
    gstin: str
    pan: str
    bank_account_number: str
    ifsc: str
    bank_account_holder_name: str
    onboarded_on: date


# --------------------------------------------------------------------------- #
# Pipeline outputs
# --------------------------------------------------------------------------- #
class Finding(BaseModel):
    """One specific problem a stage found.

    `message` is internal and may describe fraud logic. `vendor_action` is the
    safe, vendor-facing request ("please re-upload X"). It is None when we must
    not tell the vendor why (fraud signals).
    """

    code: str
    severity: StageStatus
    message: str
    field: Optional[str] = None
    vendor_action: Optional[str] = None


class StageResult(BaseModel):
    """Result of one pipeline stage, shown live and stored for replay."""

    key: str
    name: str
    status: StageStatus
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    duration_ms: float = 0.0


class Reason(BaseModel):
    """A decision reason, tied to the stage that produced it."""

    stage: str
    severity: StageStatus
    message: str


class Decision(BaseModel):
    """Aggregated, explainable outcome of all stages."""

    status: FinalStatus
    summary: str
    reasons: list[Reason] = Field(default_factory=list)
    failing_stage: Optional[str] = None
    flagged_stages: list[str] = Field(default_factory=list)


class Communications(BaseModel):
    """Drafted outbound vendor email plus the internal procurement note."""

    vendor_subject: str
    vendor_email: str
    internal_note: str
    source: str = "template"  # "gemini" or "template"
    model: Optional[str] = None  # which Gemini model drafted it, if any
    llm_error: Optional[str] = None


class RunResult(BaseModel):
    """Everything about one verification run; persisted as a row in SQLite."""

    run_id: Optional[int] = None
    created_at: datetime
    submission: VendorSubmission
    stages: list[StageResult]
    decision: Decision
    communications: Communications
    total_duration_ms: float
    source_label: str = "Manual entry"
    resubmitted_from: Optional[int] = None
