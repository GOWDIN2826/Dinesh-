"""
database.py - all data storage (SQLite, a single file: storage/job_letter.db).

Tables and the diagram box they belong to:

  jobs                  -> "Job Created & Queued" (job_id + current status)
  audit_logs            -> "File Metadata Extraction (logging / audit)" and every
                           step outcome  ("Verification Result & Audit Log Store")
  raw_content           -> "Store Raw Content Extraction (text)"
  verification_results  -> "Verification Result & Audit Log Store"
  webhook_events        -> callback receipts captured by the local mock portal
"""
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.config import settings
from models import SCHEMA_SQL

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
    conn.execute("PRAGMA foreign_keys = ON")
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
    """Create tables and move legacy extracted text to per-job local files."""
    settings.extracted_text_path.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        webhook_job_id = next(
            column
            for column in conn.execute("PRAGMA table_info(webhook_events)")
            if column["name"] == "job_id"
        )
        if webhook_job_id["notnull"]:
            conn.execute("ALTER TABLE webhook_events RENAME TO webhook_events_legacy")
            conn.execute(
                """CREATE TABLE webhook_events (
                       id           INTEGER PRIMARY KEY AUTOINCREMENT,
                       job_id       TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
                       status       TEXT NOT NULL,
                       payload_json TEXT NOT NULL,
                       received_at  TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """INSERT INTO webhook_events
                   (id, job_id, status, payload_json, received_at)
                   SELECT id, job_id, status, payload_json, received_at
                   FROM webhook_events_legacy"""
            )
            conn.execute("DROP TABLE webhook_events_legacy")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhook_job ON webhook_events(job_id)"
            )
        _backfill_webhook_job_ids(conn)
        legacy_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'raw_content'"
        ).fetchone()
        if legacy_table:
            rows = conn.execute("SELECT job_id, text FROM raw_content").fetchall()
            for row in rows:
                (settings.extracted_text_path / f"{row['job_id']}.txt").write_text(
                    row["text"] or "", encoding="utf-8"
                )
            conn.execute("DROP TABLE raw_content")


def _backfill_webhook_job_ids(conn: sqlite3.Connection) -> None:
    """Link legacy local webhook receipts to matching same-time audit records."""
    events = conn.execute(
        """SELECT id, status, received_at
           FROM webhook_events
           WHERE job_id IS NULL
           ORDER BY received_at, id"""
    ).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for event in events:
        key = (event["status"], event["received_at"])
        groups.setdefault(key, []).append(event)

    for (status, received_at), group in groups.items():
        audits = conn.execute(
            """SELECT job_id
               FROM audit_logs
               WHERE step = 'WEBHOOK'
                 AND outcome = 'PASS'
                 AND created_at = ?
                 AND message LIKE ?
               ORDER BY id""",
            (received_at, f"{status} webhook delivered%"),
        ).fetchall()
        if len(group) != len(audits):
            continue
        for event, audit in zip(group, audits):
            conn.execute(
                "UPDATE webhook_events SET job_id = ? WHERE id = ? AND job_id IS NULL",
                (audit["job_id"], event["id"]),
            )


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------
def job_exists(job_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row is not None


def new_job_id() -> str:
    """Generate a unique eight-digit numeric job ID."""
    while True:
        job_id = f"{secrets.randbelow(100_000_000):08d}"
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
    """update_job('00421873', status='PROCESSING')"""
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
# Final verification result
# ----------------------------------------------------------------------------
def save_result(job_id: str, status: str, payload: dict) -> None:
    result = {**payload, "job_id": job_id}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO verification_results (job_id, status, result_json, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   status = excluded.status,
                   result_json = excluded.result_json,
                   created_at = excluded.created_at""",
            (job_id, status, json.dumps(result, default=str), _now()),
        )


def get_result(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM verification_results WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result_json"])
    result["job_id"] = job_id
    return result


# ----------------------------------------------------------------------------
# Mock Sagicor Bank Portal - received webhooks (Reviewer Dashboard data)
# ----------------------------------------------------------------------------
def save_webhook_event(job_id: Optional[str], status: str, payload: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO webhook_events (job_id, status, payload_json, received_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, status, json.dumps(payload, default=str), _now()),
        )
