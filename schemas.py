"""
schemas.py - the "vocabulary" of the whole system, taken straight from the
workflow diagram:

  * JobStatus      -> QUEUED / PROCESSING / COMPLETED / FAILED
  * ErrorCode      -> every red "Raise Exception / Return Error" box
  * PipelineError  -> how any step stops the flow and returns an error
  * ExtractedFields-> the "Extracted Data Points" list (LLM output)
  * build_*_payload-> the JSON sent to the Sagicor Bank Portal webhook
                      (matches "Sample success result" / "Sample error result")
"""
from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, field_validator


# ----------------------------------------------------------------------------
# Job status
# ----------------------------------------------------------------------------
class JobStatus(str, Enum):
    QUEUED = "QUEUED"          # "Job Created & Queued for Processing"
    PROCESSING = "PROCESSING"  # pipeline is running
    COMPLETED = "COMPLETED"    # "Job Letter Verified"
    FAILED = "FAILED"          # any exception box


# ----------------------------------------------------------------------------
# Error codes (one per red box in the diagram)
# ----------------------------------------------------------------------------
class ErrorCode(str, Enum):
    INFECTED_FILE_DETECTED = "INFECTED_FILE_DETECTED"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    OCR_FAILED = "OCR_FAILED"
    INCORRECT_DOCUMENT_TYPE_UPLOADED = "INCORRECT_DOCUMENT_TYPE_UPLOADED"
    MANDATORY_FIELDS_MISSING = "MANDATORY_FIELDS_MISSING"
    NOT_ADDRESSED_TO_SAGICOR_BANK = "NOT_ADDRESSED_TO_SAGICOR_BANK"
    JOB_LETTER_EXPIRED = "JOB_LETTER_EXPIRED"
    INCOME_NOT_STATED = "INCOME_NOT_STATED"
    EMPLOYMENT_START_DATE_MISSING = "EMPLOYMENT_START_DATE_MISSING"
    JOB_TITLE_MISSING = "JOB_TITLE_MISSING"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"  # from "Sample error result"
    # Safety net (not in the diagram): any unexpected crash still sends a
    # FAILED webhook, so the portal is never left waiting.
    INTERNAL_PROCESSING_ERROR = "INTERNAL_PROCESSING_ERROR"


class ErrorInfo(BaseModel):
    http_status: int
    details: str   # short text -> "details" field of the webhook
    message: str   # long text  -> "error.message" field of the webhook


ERROR_CATALOG: dict[ErrorCode, ErrorInfo] = {
    ErrorCode.INFECTED_FILE_DETECTED: ErrorInfo(
        http_status=422,
        details="Virus or malicious content detected",
        message="The uploaded file failed the security scan and was rejected.",
    ),
    ErrorCode.INVALID_FILE_TYPE: ErrorInfo(
        http_status=415,
        details="Invalid file type",
        message="Unsupported file type. Accepted formats: PDF, JPEG, PNG, DOCX.",
    ),
    ErrorCode.OCR_FAILED: ErrorInfo(
        http_status=422,
        details="OCR failed",
        message="Text could not be read from the document using OCR.",
    ),
    ErrorCode.INCORRECT_DOCUMENT_TYPE_UPLOADED: ErrorInfo(
        http_status=422,
        details="Incorrect document type uploaded",
        message="The document is not a Job Letter / Employment Confirmation Letter.",
    ),
    ErrorCode.MANDATORY_FIELDS_MISSING: ErrorInfo(
        http_status=422,
        details="Mandatory fields missing",
        message="One or more mandatory fields could not be extracted from the Job Letter.",
    ),
    ErrorCode.NOT_ADDRESSED_TO_SAGICOR_BANK: ErrorInfo(
        http_status=422,
        details="Job Letter not addressed to Sagicor Bank",
        message="The Job Letter must be addressed to Sagicor Bank.",
    ),
    ErrorCode.JOB_LETTER_EXPIRED: ErrorInfo(
        http_status=422,
        details="Job Letter expired",
        message="The Job Letter is older than the allowed validity period.",
    ),
    ErrorCode.INCOME_NOT_STATED: ErrorInfo(
        http_status=422,
        details="Income not stated",
        message="Income must be stated with amount, currency and frequency.",
    ),
    ErrorCode.EMPLOYMENT_START_DATE_MISSING: ErrorInfo(
        http_status=422,
        details="Employment start date missing",
        message="The Job Letter must confirm the date employment commenced.",
    ),
    ErrorCode.JOB_TITLE_MISSING: ErrorInfo(
        http_status=422,
        details="Job title missing",
        message="The Job Letter must state the applicant's job title / position held.",
    ),
    ErrorCode.DOCUMENT_NOT_FOUND: ErrorInfo(
        http_status=404,
        details="Job Letter document not found",
        message="The requested Job Letter document could not be found",
    ),
    ErrorCode.INTERNAL_PROCESSING_ERROR: ErrorInfo(
        http_status=500,
        details="Unexpected processing error",
        message="An unexpected error occurred while processing the Job Letter.",
    ),
}


class PipelineError(Exception):
    """
    Raise this from ANY step to stop the workflow.

        raise PipelineError(ErrorCode.INVALID_FILE_TYPE)

    The pipeline catches it, saves the failure, sends the FAILED webhook to the
    Sagicor Bank Portal and ends the flow  ("Exception Handling" note).
    """

    def __init__(
        self,
        code: ErrorCode,
        details: Optional[str] = None,
        message: Optional[str] = None,
    ):
        info = ERROR_CATALOG[code]
        self.code = code
        self.http_status_code = info.http_status
        self.details = details or info.details
        self.message = message or info.message
        super().__init__(f"{code.value}: {self.details}")


# ----------------------------------------------------------------------------
# Extracted data points  ("AI Document Processing & Field Extraction (LLM)")
# ----------------------------------------------------------------------------
def _to_date_or_none(value: Any) -> Optional[date]:
    """Accept 'YYYY-MM-DD' strings; anything unreadable becomes None."""
    if value in (None, "", "null", "None"):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


class ExtractedFields(BaseModel):
    # Applicant / employer
    employee_name: Optional[str] = None
    employer_name: Optional[str] = None
    employer_address: Optional[str] = None       # -> "job_letter_address"
    # Letterhead / signatory
    letterhead_present: Optional[bool] = None
    signatory_name: Optional[str] = None
    signatory_title: Optional[str] = None
    # Letter details
    addressed_to: Optional[str] = None
    letter_date: Optional[date] = None           # -> "document_date"
    job_title: Optional[str] = None
    employment_start_date: Optional[date] = None
    # Income (amount, currency, frequency)
    income_amount: Optional[float] = None
    income_currency: Optional[str] = None        # e.g. JMD, USD
    income_frequency: Optional[str] = None       # per annum / per month / ...
    @field_validator("letter_date", "employment_start_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> Optional[date]:
        return _to_date_or_none(v)

    @field_validator("income_amount", mode="before")
    @classmethod
    def _amount(cls, v: Any) -> Optional[float]:
        if v in (None, "", "null"):
            return None
        try:
            return float(str(v).replace(",", "").strip())
        except ValueError:
            return None

# ----------------------------------------------------------------------------
# Webhook payload builders  (match the diagram's sample JSON exactly)
# ----------------------------------------------------------------------------
def format_salary(fields: ExtractedFields) -> Optional[str]:
    """Build 'JMD 250,000' from amount + currency (diagram sample style)."""
    if fields.income_amount is None:
        return None
    amount = fields.income_amount
    text = f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"
    return f"{fields.income_currency} {text}" if fields.income_currency else text


def build_queued_payload() -> dict:
    """Webhook right after upload:  QUEUED (Status, Details)."""
    return {
        "status": JobStatus.QUEUED.value,
        "details": "Job received and queued for processing",
    }


def build_success_payload(
    fields: ExtractedFields,
    validation_summary: list[dict],
) -> dict:
    """Webhook at the end:  COMPLETED  (see 'Sample success result')."""
    return {
        "status": JobStatus.COMPLETED.value,
        "details": "Job Letter processed successfully",
        "extracted_data": {
            "employee_name": fields.employee_name,
            "employer_name": fields.employer_name,
            "job_title": fields.job_title,
            "salary": format_salary(fields),
            "income_frequency": fields.income_frequency,
            "job_letter_address": fields.employer_address,
            "document_date": fields.letter_date.isoformat() if fields.letter_date else None,
            "employment_start_date": (
                fields.employment_start_date.isoformat()
                if fields.employment_start_date
                else None
            ),
            "addressed_to": fields.addressed_to,
        },
        "validation_summary": validation_summary,
    }


def build_error_payload(
    error: PipelineError,
    document_date: Optional[date] = None,
    fields: Optional[ExtractedFields] = None,
    validation_summary: Optional[list[dict]] = None,
) -> dict:
    """Webhook on any failed check:  FAILED  (see 'Sample error result')."""
    extracted_data = None
    if fields is not None:
        extracted_data = {
            "employee_name": fields.employee_name,
            "employer_name": fields.employer_name,
            "job_title": fields.job_title,
            "salary": format_salary(fields),
            "income_amount": fields.income_amount,
            "income_currency": fields.income_currency,
            "income_frequency": fields.income_frequency,
            "job_letter_address": fields.employer_address,
            "document_date": fields.letter_date.isoformat() if fields.letter_date else None,
            "employment_start_date": (
                fields.employment_start_date.isoformat()
                if fields.employment_start_date
                else None
            ),
            "addressed_to": fields.addressed_to,
        }

    return {
        "status": JobStatus.FAILED.value,
        "http_status_code": error.http_status_code,
        "details": error.details,
        "error": {"code": error.code.value, "message": error.message},
        "document_date": document_date.isoformat() if document_date else None,
        "extracted_data": extracted_data,
        "validation_summary": validation_summary or [],
    }