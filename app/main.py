"""
main.py - the FastAPI application (final version: only the web endpoints).
The workflow itself lives in pipeline.py.

Run it from the project folder:
    uvicorn app.main:app --reload
Then open  http://127.0.0.1:8000/docs  in your browser.

Endpoints
  POST /api/v1/job-letters        Sagicor Portal uploads the Job Letter  (-> QUEUED)
  GET  /api/v1/jobs               list jobs
  GET  /api/v1/jobs/{job_id}      status + audit trail + final result
  POST /mock-portal/webhook       PRACTICE ONLY: pretends to be the Sagicor Bank
                                  Portal and stores every webhook it receives
  GET  /mock-portal/events        what the portal received (Reviewer Dashboard data)
"""
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath
from typing import Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

import database as db
from app.config import settings
from app.pipeline import process_job
from schemas import ErrorCode, JobStatus, PipelineError, build_error_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("job_letter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()          # create tables on start-up
    yield


app = FastAPI(
    title="Job Letter - Employment & Income Document Verification",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# Sagicor Bank Portal  ->  our system
# =============================================================================
@app.post("/api/v1/job-letters", status_code=202, tags=["Job Letter"])
async def upload_job_letter(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Workflow: 'Job Letter Document Upload' -> 'Job Created & Queued (QUEUED)'."""
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
    stored_path = settings.upload_path / f"{job_id}{safe_ext}"
    stored_path.write_bytes(data)

    db.create_job(job_id, original_name, str(stored_path), len(data))
    db.add_audit(
        job_id, "JOB_CREATED", "INFO", "Job created and queued for processing",
        {"original_filename": original_name, "size_bytes": len(data)},
    )
    background_tasks.add_task(process_job, job_id)

    return {
        "job_id": job_id,
        "status": JobStatus.QUEUED.value,
        "details": "Job received and queued for processing",
    }


@app.get("/api/v1/jobs", tags=["Job Letter"])
def list_jobs(limit: int = 50):
    return db.list_jobs(limit=min(max(limit, 1), 200))


@app.get("/api/v1/jobs/{job_id}", tags=["Job Letter"])
def get_job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        # Same shape as the diagram's "Sample error result"
        body = build_error_payload(job_id, PipelineError(ErrorCode.DOCUMENT_NOT_FOUND))
        return JSONResponse(status_code=404, content=body)
    return {"job": job, "result": db.get_result(job_id), "audit": db.get_audit(job_id)}


# =============================================================================
# PRACTICE ONLY - a fake "Sagicor Bank Portal" that receives our webhooks
# (in real life you would set PORTAL_WEBHOOK_URL to Sagicor's real endpoint)
# =============================================================================
@app.post("/mock-portal/webhook", tags=["Mock Sagicor Portal"])
def mock_portal_webhook(
    payload: dict = Body(...),
    x_webhook_secret: Optional[str] = Header(default=None),
):
    if not x_webhook_secret or not secrets.compare_digest(
        x_webhook_secret, settings.portal_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    job_id = str(payload.get("job_id", "unknown"))
    status = str(payload.get("status", "UNKNOWN"))
    db.save_webhook_event(job_id, status, payload)
    return {"received": True}


@app.get("/mock-portal/events", tags=["Mock Sagicor Portal"])
def mock_portal_events(job_id: Optional[str] = None, limit: int = 100):
    return db.list_webhook_events(job_id=job_id, limit=min(max(limit, 1), 500))


@app.get("/", tags=["Health"])
def root():
    return {"service": "job-letter-verification", "status": "running", "docs": "/docs"}