"""
pipeline.py - the COMPLETE workflow diagram, top to bottom, in one function.

  Upload -> Job Created & Queued (webhook QUEUED)
  -> Security Scan ................ Virus Detected?
  -> File Format Validation ....... Valid File Type?
  -> File Metadata Extraction
  -> Text extraction / OCR flow ... Can text be extracted directly? / OCR Success?
  -> Store Raw Content Extraction
  -> Job Letter Rules Engine ...... Document Category Valid?
  -> AI Field Extraction (LLM) .... Mandatory Fields Extracted?
  -> Business Rule Validation ..... Rules 1-5
  -> Flag Fraud / Risk Indicators
  -> Job Letter Verified
  -> Webhook COMPLETED -> Verification Result & Audit Log Store -> End

Any failed check raises PipelineError -> ONE handler at the bottom sends the FAILED
webhook and ends the flow ("Exception Handling" note in the diagram).
main.py starts this function in the background after each upload.
"""
import logging
from datetime import datetime
from pathlib import Path

import database as db
from schemas import ErrorCode, JobStatus, PipelineError
from app.steps.business_rules import evaluate_business_rules
from app.steps.extraction import extract_raw_text
from app.steps.file_validation import validate_file_format
from app.steps.fraud import detect_risk_indicators
from app.steps.llm_extractor import extract_fields
from app.steps.metadata import extract_file_metadata
from app.steps.rules_engine import get_rules_engine
from app.steps.security_scan import run_security_scan
from app.webhook import report_failure, report_queued, report_success

logger = logging.getLogger("job_letter.pipeline")


def process_job(job_id: str) -> None:
    """Runs in the background after the upload has been accepted."""
    step = "START"
    document_date = None      # filled after AI extraction, sent with later FAILED webhooks
    fields = None
    validation_summary = []
    try:
        # ---- Job Created & Queued -> Webhook to Sagicor Bank Portal: QUEUED ----
        report_queued(job_id)
        db.update_job(job_id, status=JobStatus.PROCESSING.value)

        job = db.get_job(job_id)
        file_path = Path(job["stored_path"])
        if not file_path.exists():
            raise PipelineError(ErrorCode.DOCUMENT_NOT_FOUND)

        # ---- Security Scan  ->  Virus Detected? ----
        step = "SECURITY_SCAN"
        scan = run_security_scan(file_path)
        db.add_audit(job_id, step, "PASS", "No virus or malicious content found", scan)

        # ---- File Format Validation  ->  Valid File Type? ----
        step = "FILE_FORMAT_VALIDATION"
        fmt = validate_file_format(file_path, job["original_filename"])
        db.add_audit(job_id, step, "PASS", f"Valid file type: {fmt['detected_type'].upper()}", fmt)

        # ---- File Metadata Extraction (logging / audit) ----
        step = "FILE_METADATA_EXTRACTION"
        meta = extract_file_metadata(file_path, job["original_filename"], fmt["detected_type"])
        db.update_job(job_id, file_hash=meta["sha256"])
        db.add_audit(job_id, step, "PASS", "File metadata recorded", meta)

        # ---- Can text be extracted directly? / OCR flow / Extract Raw Data ----
        step = "TEXT_EXTRACTION"
        extraction = extract_raw_text(file_path, fmt["detected_type"], meta.get("image_count", 0))
        db.add_audit(
            job_id, step, "PASS",
            f"Raw text extracted by {extraction.method} ({len(extraction.text)} characters)",
            {
                "method": extraction.method,
                "decisions": extraction.decisions,
                "ocr_confidence": extraction.ocr_confidence,
                "truncated": extraction.truncated,
            },
        )

        # ---- Store Raw Content Extraction (text) ----
        step = "STORE_RAW_CONTENT"
        db.save_raw_content(job_id, extraction.text, extraction.method)
        db.add_audit(job_id, step, "PASS", "Raw content stored")

        # ---- Job Letter Rules Engine  ->  Document Category Valid? ----
        step = "DOCUMENT_CATEGORY_CHECK"
        engine = get_rules_engine()
        category = engine.check_document_category(extraction.text)
        audit_data = {**category.as_dict(), "rules": engine.info}
        if not category.valid:
            # Keep processing so AI extraction and the mandatory-field checks can
            # make the final decision instead of stopping on phrase matching.
            db.add_audit(
                job_id, step, "WARN",
                "Document category was not identified; continuing processing",
                audit_data,
            )
        else:
            db.add_audit(job_id, step, "PASS", f"Document category: {category.category}", audit_data)

        # ---- AI Document Processing & Field Extraction (LLM) ----
        step = "LLM_FIELD_EXTRACTION"
        llm = extract_fields(extraction.text)
        fields = llm.fields
        document_date = fields.letter_date
        db.add_audit(
            job_id, step, "PASS", "Data points extracted by the AI model",
            {
                "fields": fields.model_dump(mode="json"),
                "unverified_fields": llm.unverified_fields,
                "reported_confidence": llm.reported_confidence,
                "model": llm.model,
                "tokens": {"input": llm.input_tokens, "output": llm.output_tokens},
            },
        )

        # ---- Mandatory Fields Extracted? ----
        step = "MANDATORY_FIELDS_CHECK"
        missing = engine.check_mandatory_fields(fields)
        if missing:
            raise PipelineError(
                ErrorCode.MANDATORY_FIELDS_MISSING,
                details="Mandatory fields missing: " + ", ".join(missing),
            )
        db.add_audit(job_id, step, "PASS", "All mandatory fields extracted")

        # ---- Business Rule Validation (Job Letter Rule Set): Rules 1-5 ----
        step = "BUSINESS_RULE_VALIDATION"
        submission_date = datetime.fromisoformat(job["submitted_at"]).date()
        results = evaluate_business_rules(fields, submission_date, engine.rules)
        validation_summary = [r.as_summary() for r in results]
        for result in results:
            if not result.passed:
                step = result.step              # the failure is logged under this rule's name
                raise result.error
            db.add_audit(job_id, result.step, "PASS", result.detail, {"rule": result.rule})
        # ---- Flag Fraud / Risk Indicators (reported, no employer contact) ----
        step = "FRAUD_RISK_FLAGS"
        flags = detect_risk_indicators(
            fields, meta, extraction.ocr_confidence, llm.unverified_fields, engine.rules
        )
        if flags:
            db.add_audit(
                job_id, step, "INFO", f"{len(flags)} risk indicator(s) flagged for the reviewer",
                {"flags": [f.model_dump(mode="json") for f in flags]},
            )
        else:
            db.add_audit(job_id, step, "PASS", "No fraud / risk indicators found")

        # ---- Job Letter Verified -> Webhook COMPLETED -> Audit Log Store -> End ----
        step = "JOB_LETTER_VERIFIED"
        report_success(job_id, fields, flags, validation_summary)

    except PipelineError as err:
        report_failure(
            job_id,
            err,
            step=step,
            document_date=document_date,
            fields=fields,
            validation_summary=validation_summary,
        )
    except Exception as exc:  # anything unexpected still notifies the portal
        logger.exception("Unexpected error in job %s", job_id)
        report_failure(
            job_id,
            PipelineError(
                ErrorCode.INTERNAL_PROCESSING_ERROR,
                details=f"Unexpected error in step {step}: {type(exc).__name__}",
            ),
            step=step,
            document_date=document_date,
        )