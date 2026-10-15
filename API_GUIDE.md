# Job Letter API Demo

## Swagger walkthrough

1. Start the service from the repository root: `uvicorn app.main:app --reload`.
2. Open `http://127.0.0.1:8000/docs`.
3. Expand `POST /api/v1/job-letters`, choose a PDF, JPEG, PNG, or DOCX, and use **Try it out**. The request runs verification and returns the final result, including the `job_id` and each of the five business-rule outcomes. The upload page also displays the ID after verification.
4. The backend does not expose job-list, per-job status, or callback-event listing endpoints. The local mock callback receiver is `POST /mock-portal/webhook` and requires the configured `X-Webhook-Secret` header.

The configured client URL is `PORTAL_WEBHOOK_URL`; the mock URL is only for local demonstration. Callbacks contain status and details, but not the internal job ID. Final callbacks also contain extracted fields and validation results. Delivery retries use exponential waits (1, 2, ... seconds); callback failure is audited and does not interrupt job processing. The upload response includes the saved verification result even if callback delivery fails.

## Validation and processing

- **Security scan:** screens files for the EICAR test signature, executable files disguised as documents, active PDF content, unsafe DOCX archive contents, and (when enabled) ClamAV findings. It reduces malware and parser risk; the built-in scan is not a substitute for production antivirus.
- **File format validation:** identifies known binary types from file signatures and rejects a known filename extension that disagrees with the content. This prevents trusting a client-supplied extension alone. The existing reader also accepts readable text input, although the portal currently limits its picker to PDF/JPEG/PNG/DOCX.
- **Metadata extraction:** records a SHA-256 digest and document properties (such as PDF creator/dates, DOCX author/revision, or image software metadata). This supports traceability and tampering-risk review; metadata alone does not prove authenticity.
- **Text extraction:** reads a PDF/DOCX text layer where possible, otherwise applies OCR. Source text is truncated at 30,000 characters to bound downstream processing and is written to a randomly named file under `storage/extracted_text/`. It is not returned to the browser or written to SQLite. The audit trail stores processing metadata, not the raw text.

## LLM token budget

`LLM_MAX_TOKENS=1500` is passed to the provider as the maximum generated output for the structured extraction call. It caps completion size, latency, and output-token cost while leaving room for extracted fields and their short evidence quotations. It is **not** a total-token limit and does not cap input/context size.

The separate source-text guard is 30,000 characters. As a rough English-text estimate, that can be several thousand input tokens; actual usage varies with OCR quality, language, symbols, the system prompt, and the tool schema. The provider's reported input/output token counts are recorded on the extraction audit event. There is no measured benchmark in this repository proving that 1,500 is optimal: it is a configurable operational ceiling. Review observed output-token counts and incomplete/failed structured responses before changing it; too low a cap can truncate a tool response, while an unnecessarily high cap permits avoidable generation cost.

## 90-day document validity

The 90-day period is calculated from the **document issue date** extracted from the letter to the job's **submission/upload date** stored as `submitted_at`. The upload date is the reference point; a separately supplied frontend `effectiveDate` is not part of the current API contract. An issue date older than 90 days fails. A document date more than one day in the future also fails under the same expiry error code; the one-day allowance handles date/time-zone differences.

## Database and local files

`models.py` defines and documents the SQLite tables: `jobs`, `audit_logs`, `verification_results`, and `webhook_events`. Every uploaded file has a `jobs` row keyed by `job_id`; its filename, saved path, size, hash, status, and timestamps are stored there. Each processing-step audit row and final verification result references that same ID. Local mock webhook receipts are linked to the job as well. To inspect a file and all of its processing details in SQLite, run:

```sql
SELECT j.job_id, j.original_filename, j.status, j.file_size, j.file_hash,
       j.submitted_at, a.step, a.outcome, a.message, a.data, a.created_at
FROM jobs AS j
LEFT JOIN audit_logs AS a ON a.job_id = j.job_id
ORDER BY j.submitted_at DESC, a.id;
```

The upload binary remains under `storage/uploads/`. Extracted text is separate local filesystem data named with the job ID and is intentionally absent from SQLite to avoid retaining raw document contents there. On startup, text in the legacy `raw_content` table is copied into its matching local file before the table is removed.

## Deferred topics

- **Auto-delete:** a retention job removes uploaded binaries and extracted-text files after an approved period, while retaining only records legally and operationally required. Define retention windows, terminal-state handling, audit retention, and recovery rules before implementing deletion.
- **Celery workers:** a broker-backed queue (commonly Redis or RabbitMQ) would move CPU/OCR/LLM work out of FastAPI's in-process `BackgroundTasks`. Production design needs durable delivery, idempotent jobs, retry/backoff and dead-letter policy, worker scaling, and monitoring. The current background task is simple but is not durable across process restarts.
- **Okta / IDP:** an identity provider can authenticate users or calling services using OAuth 2.0/OIDC tokens. A production integration must validate issuer, audience, signature (JWKS), expiry, and scopes/roles; a token field in a browser is not a substitute for server-side verification.

These topics are intentionally not implemented. The screenshot's Bearer token, `X-LLM-Token`, `organizationCode`, and `effectiveDate` fields are also not part of this Job Letter API contract; their meanings and validation rules need confirmation before adding them.