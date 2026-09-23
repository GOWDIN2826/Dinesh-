"""
database.py - all data storage (SQLite, a single file: storage/job_letter.db).

Tables and the diagram box they belong to:

  jobs                  -> "Job Created & Queued" (job_id + current status)
  audit_logs            -> "File Metadata Extraction (logging / audit)" and every
                           step outcome  ("Verification Result & Audit Log Store")
  raw_content           -> "Store Raw Content Extraction (text)"
  verification_results  -> "Verification Result & Audit Log Store"
  webhook_events        -> what the (mock) Sagicor Bank Portal received; shown on
                           the "Reviewer Dashboard"
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    status            TEXT NOT NULL,
    original_filename TEXT,
    stored_path       TEXT,
    file_size         INTEGER,
    file_hash         TEXT,
    error_code        TEXT,
    details           TEXT,
    submitted_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    step       TEXT NOT NULL,
    outcome    TEXT NOT NULL,          -- PASS / FAIL / INFO
    message    TEXT,
    data       TEXT,                   -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_logs(job_id);

CREATE TABLE IF NOT EXISTS raw_content (
    job_id     TEXT PRIMARY KEY,
    method     TEXT,                   -- direct / ocr
    text       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_results (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_job ON webhook_events(job_id);
"""

# Columns of the jobs table that update_job() is allowed to change
_JOB_UPDATABLE = {
    "status", "original_filename", "stored_path", "file_size",
    "file_hash", "error_code", "details",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(settings.db_file, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables (safe to call every time the app starts)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------
def job_exists(job_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row is not None


def new_job_id() -> str:
    """Make an id like '9f3c-7ad2' (same style as the diagram's sample)."""
    while True:
        h = uuid.uuid4().hex
        job_id = f"{h[:4]}-{h[4:8]}"
        if not job_exists(job_id):
            return job_id


def create_job(
    job_id: str,
    original_filename: str,
    stored_path: str,
    file_size: int,
) -> None:
    """Workflow: 'Job Created & Queued for Processing - Status: QUEUED'."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, status, original_filename, stored_path, file_size,
                submitted_at, updated_at)
               VALUES (?, 'QUEUED', ?, ?, ?, ?, ?)""",
            (job_id, original_filename, stored_path, file_size, now, now),
        )


def update_job(job_id: str, **fields: Any) -> None:
    """update_job('9f3c-7ad2', status='PROCESSING')"""
    bad = set(fields) - _JOB_UPDATABLE
    if bad:
        raise ValueError(f"Cannot update column(s): {', '.join(sorted(bad))}")
    if not fields:
        return
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{col} = ?" for col in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )


def get_job(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY submitted_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------------------
def add_audit(
    job_id: str,
    step: str,
    outcome: str,
    message: str = "",
    data: Optional[dict] = None,
) -> None:
    """add_audit(job_id, 'SECURITY_SCAN', 'PASS', 'No virus found')"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO audit_logs (job_id, step, outcome, message, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job_id, step, outcome, message,
                json.dumps(data, default=str) if data is not None else None,
                _now(),
            ),
        )


def get_audit(job_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["data"] = json.loads(item["data"]) if item["data"] else None
        result.append(item)
    return result


# ----------------------------------------------------------------------------
# Raw extracted text
# ----------------------------------------------------------------------------
def save_raw_content(job_id: str, text: str, method: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO raw_content (job_id, method, text, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   method = excluded.method,
                   text = excluded.text,
                   created_at = excluded.created_at""",
            (job_id, method, text, _now()),
        )


def get_raw_content(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM raw_content WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


# ----------------------------------------------------------------------------
# Final verification result
# ----------------------------------------------------------------------------
def save_result(job_id: str, status: str, payload: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO verification_results (job_id, status, result_json, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   status = excluded.status,
                   result_json = excluded.result_json,
                   created_at = excluded.created_at""",
            (job_id, status, json.dumps(payload, default=str), _now()),
        )


def get_result(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM verification_results WHERE job_id = ?", (job_id,)
        ).fetchone()
    return json.loads(row["result_json"]) if row else None


# ----------------------------------------------------------------------------
# Mock Sagicor Bank Portal - received webhooks (Reviewer Dashboard data)
# ----------------------------------------------------------------------------
def save_webhook_event(job_id: str, status: str, payload: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO webhook_events (job_id, status, payload_json, received_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, status, json.dumps(payload, default=str), _now()),
        )


def list_webhook_events(job_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM webhook_events WHERE job_id = ? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["payload"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result