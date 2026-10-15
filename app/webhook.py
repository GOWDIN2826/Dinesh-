"""
webhook.py - talks to the Sagicor Bank Portal.

Diagram boxes covered:
  * "Webhook to Sagicor Bank Portal - QUEUED (Status, Details)"
  * "Exception Handling: every failed check sends a FAILED webhook (
     failure status, error details) to the portal ... then the flow ends."
  * "Webhook to Sagicor Bank Portal - COMPLETED (Completion Status,
     Extracted & Verification Details, Validation Summary)"  -> Reviewer Dashboard

Rule: a webhook problem must NEVER crash the pipeline, so send_webhook()
never raises - it retries, logs to the audit table and returns True/False.
"""
import logging
import time
from datetime import date
from typing import Optional
from urllib.parse import urlsplit

import httpx

import database as db
from app.config import settings
from schemas import (
    ExtractedFields,
    FraudFlag,
    JobStatus,
    PipelineError,
    build_error_payload,
    build_queued_payload,
    build_success_payload,
)

logger = logging.getLogger("job_letter.webhook")


def send_webhook(job_id: str, payload: dict) -> bool:
    """POST the payload to the portal. Retries with 1s, 2s, 4s ... waits."""
    status = payload.get("status", "UNKNOWN")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": settings.portal_webhook_secret,  # portal checks this
    }
    target = urlsplit(settings.portal_webhook_url)
    if (
        target.hostname in {"127.0.0.1", "localhost", "::1"}
        and target.path.rstrip("/") == "/mock-portal/webhook"
    ):
        headers["X-Job-ID"] = job_id
    attempts = max(1, settings.webhook_max_retries)
    last_error = ""
    made = 0

    for attempt in range(1, attempts + 1):
        made = attempt
        try:
            resp = httpx.post(
                settings.portal_webhook_url,
                json=payload,
                headers=headers,
                timeout=settings.webhook_timeout_seconds,
            )
            if resp.status_code < 300:
                db.add_audit(
                    job_id, "WEBHOOK", "PASS",
                    f"{status} webhook delivered (HTTP {resp.status_code})",
                    {"attempt": attempt},
                )
                return True
            last_error = f"HTTP {resp.status_code}"
            # A 4xx error (except 408/429) will not fix itself - stop retrying
            if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                break
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))

    logger.warning("Webhook %s failed: %s", status, last_error)
    db.add_audit(
        job_id, "WEBHOOK", "FAIL",
        f"{status} webhook NOT delivered after {made} attempt(s): {last_error}",
    )
    return False


def report_queued(job_id: str) -> bool:
    """Webhook right after the job is created:  status = QUEUED."""
    return send_webhook(job_id, build_queued_payload())


def report_failure(
    job_id: str,
    error: PipelineError,
    step: str = "PIPELINE",
    document_date: Optional[date] = None,
    fraud_flags: Optional[list[FraudFlag]] = None,
    fields: Optional[ExtractedFields] = None,
    validation_summary: Optional[list[dict]] = None,
) -> dict:
    """
    The ONE place where every failed check ends  ("Exception Handling").
      1. write the failure to the audit log
      2. mark the job FAILED
      3. store the result (Verification Result & Audit Log Store)
      4. send the FAILED webhook  ->  Reviewer Dashboard
    Then the flow ends.
    """
    payload = build_error_payload(
        error, document_date, fraud_flags, fields, validation_summary
    )
    db.add_audit(job_id, step, "FAIL", f"{error.code.value}: {error.details}")
    db.update_job(
        job_id,
        status=JobStatus.FAILED.value,
        error_code=error.code.value,
        details=error.details,
    )
    db.save_result(job_id, JobStatus.FAILED.value, payload)
    send_webhook(job_id, payload)
    return payload


def report_success(
    job_id: str,
    fields: ExtractedFields,
    fraud_flags: list[FraudFlag],
    validation_summary: list[dict],
) -> dict:
    """
    "Job Letter Verified - All business rules passed"  ->  COMPLETED webhook  ->  End.
      1. mark the job COMPLETED and write the audit log
      2. send the COMPLETED webhook (extracted data + validation summary + fraud flags)
      3. store the result  (Verification Result & Audit Log Store)
    `job_letter_address_sent_to_sagicor` is only True if the portal really received it.
    """
    payload = build_success_payload(fields, fraud_flags, validation_summary)
    db.add_audit(
        job_id, "JOB_LETTER_VERIFIED", "PASS", "All business rules passed",
        {"risk_indicators": len(fraud_flags)},
    )
    db.update_job(
        job_id,
        status=JobStatus.COMPLETED.value,
        error_code=None,
        details=payload["details"],
    )

    delivered = send_webhook(job_id, payload)
    payload["job_letter_address_sent_to_sagicor"] = bool(delivered and fields.employer_address)
    db.save_result(job_id, JobStatus.COMPLETED.value, payload)
    return payload