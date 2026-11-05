"""SQLite persistence: every run (the audit trail) plus the existing-vendor master.

Why SQLite: zero setup, ships with Python, and works on Streamlit Community
Cloud. The schema is deliberately flat. JSON columns hold the full stage
results, so any run can be replayed exactly as it happened.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from core.samples import load_registry_seed
from core.schemas import ExistingVendor, RunResult

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,
    vendor_name       TEXT NOT NULL,
    gstin             TEXT,
    entity_type       TEXT,
    status            TEXT NOT NULL,
    failing_stage     TEXT,
    duration_ms       REAL NOT NULL,
    source_label      TEXT,
    resubmitted_from  INTEGER REFERENCES runs(id),
    submission_json   TEXT NOT NULL,
    stages_json       TEXT NOT NULL,
    decision_json     TEXT NOT NULL,
    reasons_json      TEXT NOT NULL,
    vendor_subject    TEXT,
    vendor_email      TEXT,
    internal_note     TEXT,
    email_source      TEXT,
    result_json       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS existing_vendors (
    vendor_id                 TEXT PRIMARY KEY,
    vendor_name               TEXT NOT NULL,
    gstin                     TEXT NOT NULL,
    pan                       TEXT NOT NULL,
    bank_account_number       TEXT NOT NULL,
    ifsc                      TEXT NOT NULL,
    bank_account_holder_name  TEXT NOT NULL,
    onboarded_on              TEXT NOT NULL
);
"""


def db_path() -> Path:
    """DB location; overridable with VENDOR_DB_PATH (used by tests)."""
    return Path(os.environ.get("VENDOR_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Short-lived connection per operation, committed on success."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and seed the vendor master on first run (idempotent)."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM existing_vendors").fetchone()[0] == 0:
            _seed_registry(conn)


def _seed_registry(conn: sqlite3.Connection) -> None:
    """Load data/existing_vendors.json into the vendor master table.

    OR IGNORE makes seeding race-safe: on a cold start several Streamlit sessions can
    all see an empty table, and the ones that lose the race must not crash the app.
    """
    rows = [(v.vendor_id, v.vendor_name, v.gstin, v.pan, v.bank_account_number, v.ifsc,
             v.bank_account_holder_name, v.onboarded_on.isoformat()) for v in load_registry_seed()]
    conn.executemany("INSERT OR IGNORE INTO existing_vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def reset_demo_data() -> None:
    """Clear all runs and reseed the vendor master. Used by the sidebar reset button."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM existing_vendors")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'runs'")
        _seed_registry(conn)


def get_registry() -> list[ExistingVendor]:
    """All vendors currently in the vendor master."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM existing_vendors ORDER BY vendor_id").fetchall()
    return [ExistingVendor.model_validate(dict(r)) for r in rows]


def save_run(result: RunResult) -> int:
    """Persist one run and return its id."""
    d = result.decision
    c = result.communications
    payload = (
        result.created_at.isoformat(), result.submission.display_name, result.submission.gstin,
        result.submission.entity_type.value, d.status.value, d.failing_stage, result.total_duration_ms,
        result.source_label, result.resubmitted_from,
        result.submission.model_dump_json(),
        json.dumps([s.model_dump(mode="json") for s in result.stages]),
        d.model_dump_json(), json.dumps([r.model_dump(mode="json") for r in d.reasons]),
        c.vendor_subject, c.vendor_email, c.internal_note, c.source,
        result.model_dump_json(exclude={"run_id"}),
    )
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO runs (created_at, vendor_name, gstin, entity_type, status, failing_stage,
               duration_ms, source_label, resubmitted_from, submission_json, stages_json, decision_json,
               reasons_json, vendor_subject, vendor_email, internal_note, email_source, result_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", payload)
        return int(cur.lastrowid)


def list_runs() -> list[dict[str, Any]]:
    """Lightweight run summaries for the dashboard, newest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, created_at, vendor_name, gstin, entity_type, status, failing_stage,
                      duration_ms, source_label, resubmitted_from, email_source
               FROM runs ORDER BY id DESC""").fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> Optional[RunResult]:
    """Full run, rehydrated for replay. Returns None if it doesn't exist."""
    with connect() as conn:
        row = conn.execute("SELECT id, result_json FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    result = RunResult.model_validate_json(row["result_json"])
    result.run_id = int(row["id"])
    return result
