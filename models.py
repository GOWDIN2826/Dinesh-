"""Database schema and typed records for the local SQLite persistence layer.

Raw OCR/direct-extraction text is deliberately not a database model. It is kept
in a randomly named file under ``storage/extracted_text`` so it is neither
returned by the API nor stored in SQLite. The remaining tables contain job
state, audit metadata, the final verification payload, and webhook receipts.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class JobRecord(BaseModel):
    """Persisted job state; ``stored_path`` is internal and never an API field."""

    job_id: str
    status: str
    original_filename: Optional[str] = None
    stored_path: Optional[str] = None
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    error_code: Optional[str] = None
    details: Optional[str] = None
    submitted_at: str
    updated_at: str


class JobVerificationResponse(BaseModel):
    """Completed verification result returned after upload processing."""

    job_id: str
    status: str
    details: str
    http_status_code: Optional[int] = None
    error: Optional[dict[str, str]] = None
    document_date: Optional[str] = None
    extracted_data: Optional[dict[str, Any]] = None
    validation_summary: list[dict[str, Any]] = Field(default_factory=list)
    job_letter_address_sent_to_sagicor: Optional[bool] = None
    fraud_detection: Optional[dict[str, Any]] = None


class VerificationResultRecord(BaseModel):
    """Final COMPLETED or FAILED result retained for internal processing."""

    job_id: str
    status: str
    result_json: str
    created_at: str


class WebhookEventRecord(BaseModel):
    """Callback receipt captured by the local mock client for the demo."""

    id: int
    job_id: str
    status: str
    payload_json: str
    received_at: str


class WebhookReceiptResponse(BaseModel):
    received: bool


class WebhookCallback(BaseModel):
    """Callback contract shared by queued, completed, and failed jobs."""

    status: Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED"]
    details: str
    http_status_code: Optional[int] = None
    error: Optional[dict[str, str]] = None
    document_date: Optional[str] = None
    extracted_data: Optional[dict[str, Any]] = None
    validation_summary: list[dict[str, Any]] = Field(default_factory=list)
    job_letter_address_sent_to_sagicor: Optional[bool] = None
    fraud_detection: Optional[dict[str, Any]] = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    status            TEXT NOT NULL CHECK (status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED')),
    original_filename TEXT,
    stored_path       TEXT,
    file_size         INTEGER CHECK (file_size IS NULL OR file_size >= 0),
    file_hash         TEXT,
    error_code        TEXT,
    details           TEXT,
    submitted_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    step       TEXT NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('PASS', 'FAIL', 'INFO', 'WARN')),
    message    TEXT,
    data       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_logs(job_id);

CREATE TABLE IF NOT EXISTS verification_results (
    job_id       TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN ('COMPLETED', 'FAILED')),
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_job ON webhook_events(job_id);
"""