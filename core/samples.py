"""Loaders for the bundled sample submissions and the vendor-master seed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.schemas import ExistingVendor, VendorSubmission

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLES_DIR = DATA_DIR / "samples"
REGISTRY_SEED = DATA_DIR / "existing_vendors.json"


@dataclass(frozen=True)
class Sample:
    """A named demo scenario."""

    slug: str
    scenario: str
    description: str
    submission: VendorSubmission


def load_samples() -> list[Sample]:
    """All sample scenarios, in file-name order (01_, 02_, ...)."""
    samples = []
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        samples.append(Sample(slug=path.stem, scenario=raw["scenario"], description=raw["description"],
                              submission=VendorSubmission.model_validate(raw["submission"])))
    return samples


def load_registry_seed() -> list[ExistingVendor]:
    """Existing vendors used to seed the duplicate-detection registry."""
    raw = json.loads(REGISTRY_SEED.read_text(encoding="utf-8"))
    return [ExistingVendor.model_validate(row) for row in raw]
