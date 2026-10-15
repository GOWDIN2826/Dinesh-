"""
main.py - the FastAPI application (final version: only the web endpoints).
The workflow itself lives in pipeline.py.

Run it from the project folder:
    uvicorn app.main:app --reload
Then open  http://127.0.0.1:8000/docs  in your browser.

Endpoints
  POST /api/v1/job-letters        upload and verify a Job Letter
  POST /mock-portal/webhook       PRACTICE ONLY: pretends to be the Sagicor Bank
                                  Portal and stores every webhook it receives
"""
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath
from typing import Optional

from fastapi import Body, FastAPI, File, Header, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

import database as db
from app.config import settings
from app.pipeline import process_job
from models import (
    JobVerificationResponse,
    WebhookCallback,
    WebhookReceiptResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("job_letter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()          # create tables on start-up
    yield


app = FastAPI(
    title="Job Letter - Employment & Income Document Verification",
    version="1.0.0",
    openapi_tags=[
        {"name": "Job Letter", "description": "Upload and inspect verification jobs."},
        {"name": "Mock Sagicor Portal", "description": "Local callback receiver for the demo."},
        {"name": "Health", "description": "Service health information."},
    ],
    lifespan=lifespan,
)


# =============================================================================
# Sagicor Bank Portal  ->  our system
# =============================================================================
@app.post(
    "/api/v1/job-letters",
    response_model=JobVerificationResponse,
    tags=["Job Letter"],
    summary="Upload a job letter",
    description=(
        "Accepts one PDF, JPEG, PNG, or DOCX upload (maximum 10 MB), runs "
        "verification against all five business rules, and returns the result. "
        "Job identifiers are kept internally and are not included in API responses."
    ),
    responses={
        400: {"description": "The uploaded file is empty."},
        413: {"description": "The upload exceeds the configured size limit."},
    },
)
async def upload_job_letter(file: UploadFile = File(...)):
    """Upload a job letter and return its completed verification result."""
    data = await file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"File is larger than {settings.max_upload_mb} MB"
        )

    # Never trust the client's file name: strip any folder part, and build our
    # own safe name on disk.  (File TYPE is checked later, in the pipeline.)
    original_name = PureWindowsPath(file.filename or "upload").name[:255] or "upload"
    ext = Path(original_name).suffix.lower()
    safe_ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".bin"

    job_id = db.new_job_id()
    storage_key = secrets.token_hex(16)
    stored_path = settings.upload_path / f"{storage_key}{safe_ext}"
    stored_path.write_bytes(data)

    db.create_job(job_id, original_name, str(stored_path), len(data))
    db.add_audit(
        job_id, "JOB_CREATED", "INFO", "Job created and queued for processing",
        {"original_filename": original_name, "size_bytes": len(data)},
    )
    await run_in_threadpool(process_job, job_id)
    result = db.get_result(job_id)
    if result is None:
        raise HTTPException(
            status_code=500,
            detail="Verification finished without a persisted result",
        )
    return result


# =============================================================================
# PRACTICE ONLY - a fake "Sagicor Bank Portal" that receives our webhooks
# (in real life you would set PORTAL_WEBHOOK_URL to Sagicor's real endpoint)
# =============================================================================
@app.post(
    "/mock-portal/webhook",
    response_model=WebhookReceiptResponse,
    tags=["Mock Sagicor Portal"],
    summary="Receive a demo webhook",
    description=(
        "Local callback receiver used by the demo. Requests must include the "
        "configured `X-Webhook-Secret`; production clients should provide their own receiver."
    ),
)
def mock_portal_webhook(
    payload: WebhookCallback = Body(...),
    x_webhook_secret: Optional[str] = Header(default=None),
    x_job_id: Optional[str] = Header(default=None),
):
    if not x_webhook_secret or not secrets.compare_digest(
        x_webhook_secret, settings.portal_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    if x_job_id is not None and not db.job_exists(x_job_id):
        raise HTTPException(status_code=422, detail="Unknown job ID")
    event = payload.model_dump(mode="json", exclude_none=True)
    db.save_webhook_event(x_job_id, event["status"], event)
    return {"received": True}


@app.get("/health", tags=["Health"], summary="Health")
def root():
    return {"service": "job-letter-verification", "status": "running", "docs": "/docs"}