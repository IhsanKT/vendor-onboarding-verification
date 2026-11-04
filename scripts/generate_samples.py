"""Generate the demo sample submissions and the vendor-master seed file.

Every GSTIN is built with `core.validators.build_gstin`, so its checksum is
computed, never hand-typed. All company and person names are fictional.

Usage:  python scripts/generate_samples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.validators import build_gstin  # noqa: E402

SAMPLES_DIR = ROOT / "data" / "samples"
REGISTRY_FILE = ROOT / "data" / "existing_vendors.json"

ALL_DOCS = ["GST_CERTIFICATE", "PAN_CARD", "CANCELLED_CHEQUE", "INCORPORATION_CERT"]


def docs(missing: tuple[str, ...] = (), expiry: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """Document metadata list: all uploaded, except the ones listed in `missing`."""
    expiry = expiry or {}
    return [{"doc_type": d, "uploaded": d not in missing, "expiry_date": expiry.get(d)} for d in ALL_DOCS]


def sample(scenario: str, description: str, **submission: Any) -> dict[str, Any]:
    """Wrap a submission with a human-readable scenario title and description."""
    return {"scenario": scenario, "description": description, "submission": submission}


SAMPLES: dict[str, dict[str, Any]] = {
    "01_happy_path": sample(
        "Happy path",
        "A clean Private Limited submission from Karnataka. Every check should pass.",
        legal_name="Kaveri Agro Foods Private Limited", trade_name="Kaveri Foods",
        entity_type="Private Limited",
        address="Plot 42, 2nd Phase, Peenya Industrial Area", city="Bengaluru",
        state="Karnataka", pincode="560058",
        contact_name="Meera Iyer", contact_email="accounts@kaveriagro.example",
        contact_phone="+91 98450 12345",
        gstin=build_gstin("29", "AAHCK4821M"), pan="AAHCK4821M",
        bank_account_holder_name="Kaveri Agro Foods Private Limited",
        bank_account_number="50200012345671", ifsc="HDFC0001234",
        documents=docs(),
    ),
    "02_gstin_pan_mismatch": sample(
        "GSTIN / PAN mismatch",
        "Every field is well-formed and the GSTIN checksum is valid, but the GSTIN was "
        "issued to a different PAN, so it belongs to another legal entity.",
        legal_name="Shreeji Polymers Private Limited", trade_name=None,
        entity_type="Private Limited",
        address="Survey No. 118, Changodar Industrial Estate", city="Ahmedabad",
        state="Gujarat", pincode="382213",
        contact_name="Hardik Patel", contact_email="finance@shreejipolymers.example",
        contact_phone="+91 98250 44781",
        gstin=build_gstin("24", "AAQCS9052K"), pan="AAQCS7314H",
        bank_account_holder_name="Shreeji Polymers Private Limited",
        bank_account_number="31890100004521", ifsc="BARB0CHANGO",
        documents=docs(),
    ),
    "03_bank_name_close_match": sample(
        "Bank name: cosmetic difference",
        "The bank records the holder as 'M/s ACME PRECISION TOOLS PVT. LTD.' against the legal "
        "name 'Acme Precision Tools Private Limited'. Normalisation should make these match.",
        legal_name="Acme Precision Tools Private Limited", trade_name="Acme Tools",
        entity_type="Private Limited",
        address="Gat No. 210, Bhosari MIDC", city="Pune",
        state="Maharashtra", pincode="411026",
        contact_name="Sanjay Kulkarni", contact_email="ap@acmetools.example",
        contact_phone="+91 99220 61834",
        gstin=build_gstin("27", "AABCA6627R"), pan="AABCA6627R",
        bank_account_holder_name="M/s ACME PRECISION TOOLS PVT. LTD.",
        bank_account_number="002105017733", ifsc="ICIC0000021",
        documents=docs(),
    ),
    "04_bank_name_mismatch": sample(
        "Bank account in a stranger's name",
        "An LLP whose bank account is held by an unrelated individual, the classic "
        "payment-diversion pattern.",
        legal_name="Nilgiri Weaves LLP", trade_name=None, entity_type="LLP",
        address="14/2 Avinashi Road, Peelamedu", city="Coimbatore",
        state="Tamil Nadu", pincode="641004",
        contact_name="Karthik Subramanian", contact_email="karthik@nilgiriweaves.example",
        contact_phone="+91 94430 27719",
        gstin=build_gstin("33", "AAKFN3390D"), pan="AAKFN3390D",
        bank_account_holder_name="Rohit Malhotra",
        bank_account_number="38291045567", ifsc="SBIN0007845",
        documents=docs(),
    ),
    "05_duplicate_bank_account": sample(
        "Bank account shared with another vendor",
        "A new vendor whose bank account is already on file for a different existing vendor "
        "(Sapphire Office Supplies), which is a shell-vendor / collusion signal.",
        legal_name="Vardhan Logistics Private Limited", trade_name="Vardhan Express",
        entity_type="Private Limited",
        address="B-27, Okhla Industrial Area Phase II", city="New Delhi",
        state="Delhi", pincode="110020",
        contact_name="Amit Vardhan", contact_email="billing@vardhanlogistics.example",
        contact_phone="+91 98110 53620",
        gstin=build_gstin("07", "AAGCV2718P"), pan="AAGCV2718P",
        bank_account_holder_name="Vardhan Logistics Pvt Ltd",
        bank_account_number="917020045678123", ifsc="UTIB0000045",
        documents=docs(),
    ),
    "06_missing_expired_doc": sample(
        "Missing and expired documents",
        "A proprietorship (bank account in the owner's name) that forgot the cancelled cheque and "
        "uploaded an old provisional GST certificate that has since lapsed.",
        legal_name="Sharma Electricals", trade_name="Sharma Electricals",
        entity_type="Proprietorship",
        address="Shop 9, Aminabad Market", city="Lucknow",
        state="Uttar Pradesh", pincode="226018",
        contact_name="Rajesh Sharma", contact_email="sharmaelectricals@mail.example",
        contact_phone="+91 94150 88213",
        gstin=build_gstin("09", "BQWPS5512L"), pan="BQWPS5512L",
        bank_account_holder_name="RAJESH SHARMA",
        bank_account_number="10293847561", ifsc="PUNB0123400",
        documents=docs(missing=("CANCELLED_CHEQUE", "INCORPORATION_CERT"),
                       expiry={"GST_CERTIFICATE": "2025-06-30"}),
    ),
}


def vendor(vid: str, name: str, state_code: str, pan: str, acct: str, ifsc: str,
           holder: str, onboarded: str) -> dict[str, str]:
    """One existing vendor-master row with a generated, checksum-valid GSTIN."""
    return {"vendor_id": vid, "vendor_name": name, "gstin": build_gstin(state_code, pan), "pan": pan,
            "bank_account_number": acct, "ifsc": ifsc, "bank_account_holder_name": holder,
            "onboarded_on": onboarded}


REGISTRY: list[dict[str, str]] = [
    vendor("V-1001", "Deccan Packaging Private Limited", "36", "AADCD4410B", "62510034418", "SBIN0020071",
           "Deccan Packaging Private Limited", "2023-04-12"),
    vendor("V-1002", "Konkan Freight Carriers LLP", "27", "AAMFK2290Q", "50200067781203", "HDFC0000412",
           "Konkan Freight Carriers LLP", "2023-07-03"),
    vendor("V-1003", "Brahmaputra Tea Traders", "18", "AFTPB8123C", "20019844563", "SBIN0001350",
           "Anil Baruah", "2023-11-21"),
    vendor("V-1004", "Malabar Spice Exports Private Limited", "32", "AAJCM5531E", "16420200008812",
           "FDRL0001642", "Malabar Spice Exports Pvt Ltd", "2024-01-15"),
    vendor("V-1005", "Indus Steel Fabricators Private Limited", "03", "AACCI7745G", "4012004500077",
           "PUNB0401200", "Indus Steel Fabricators Private Limited", "2024-05-30"),
    vendor("V-1006", "Sapphire Office Supplies Private Limited", "07", "AAPCS3307N", "917020045678123",
           "UTIB0000045", "Sapphire Office Supplies Private Limited", "2024-08-19"),
    vendor("V-1007", "Ganga Print House", "10", "AAEFG6619H", "0571102000034", "IBKL0000571",
           "Ganga Print House", "2025-02-07"),
    vendor("V-1008", "Coromandel Chemicals Limited", "33", "AABCC1182J", "6150123456", "KKBK0008612",
           "Coromandel Chemicals Limited", "2025-06-24"),
]


def main() -> None:
    """Write all sample files and the registry seed."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for slug, payload in SAMPLES.items():
        (SAMPLES_DIR / f"{slug}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    REGISTRY_FILE.write_text(json.dumps(REGISTRY, indent=2), encoding="utf-8")
    print(f"Wrote {len(SAMPLES)} samples to {SAMPLES_DIR} and {len(REGISTRY)} vendors to {REGISTRY_FILE}")


if __name__ == "__main__":
    main()
