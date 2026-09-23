"""
fraud.py - diagram box "Flag Fraud / Risk Indicators"
"Indicators are reported to the Sagicor Bank Portal (no employer contact)".

The four triggers from the diagram:
  1. Low extraction confidence
  2. Signs of tampering / edited document
  3. Employer not recognised
  4. Income above configured threshold

IMPORTANT: a flag does NOT fail the job.  The letter still becomes "Job Letter
Verified", and the flags travel in the COMPLETED webhook (`fraud_detection`) so a
human reviewer can look closer.  Nothing here ever contacts the employer.
All settings live in the "fraud" section of rules/job_letter_rules_v1.json.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from schemas import ExtractedFields, FraudFlag, FraudIndicator
from app.steps.business_rules import normalise_name, similarity

# Metadata fields (from metadata.py) that name the software that made the file
SOFTWARE_FIELDS = {
    "pdf_creator": "PDF creator",
    "pdf_producer": "PDF producer",
    "exif_software": "Image software",
}
MODIFIED_AFTER_DAYS = 1      # PDF changed more than 1 day after it was created


def parse_pdf_date(value: Optional[str]) -> Optional[datetime]:
    """PDF dates look like  D:20260915103000+05'30'  (time and zone are optional)."""
    if not value:
        return None
    match = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", str(value).strip())
    if not match:
        return None
    parts = [int(g) if g else default for g, default in zip(match.groups(), (0, 1, 1, 0, 0, 0))]
    try:
        return datetime(*parts)
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Trigger 1: low extraction confidence
# ----------------------------------------------------------------------------
def _low_confidence(
    fields: ExtractedFields,
    ocr_confidence: Optional[float],
    unverified_fields: list[str],
    cfg: dict,
) -> Optional[FraudFlag]:
    minimum = float(cfg["min_confidence"])
    reasons = []
    if fields.confidence < minimum:
        reasons.append(f"AI extraction confidence {fields.confidence:.2f} is below {minimum:.2f}")
    if ocr_confidence is not None and ocr_confidence < minimum * 100:
        reasons.append(f"OCR confidence {ocr_confidence:.0f}% is below {minimum * 100:.0f}%")
    if unverified_fields:
        reasons.append(
            "could not confirm these values in the letter text: " + ", ".join(unverified_fields)
        )
    if not reasons:
        return None
    return FraudFlag(
        indicator=FraudIndicator.LOW_EXTRACTION_CONFIDENCE,
        description="; ".join(reasons),
    )


# ----------------------------------------------------------------------------
# Trigger 2: signs of tampering / edited document
# ----------------------------------------------------------------------------
def _tampering(metadata: dict, cfg: dict) -> Optional[FraudFlag]:
    keywords = [k.lower() for k in cfg.get("suspicious_software", [])]
    reasons = []

    for key, label in SOFTWARE_FIELDS.items():
        value = metadata.get(key)
        if value and any(k in str(value).lower() for k in keywords):
            reasons.append(f"{label} '{value}' is an editing tool")

    created = parse_pdf_date(metadata.get("pdf_created"))
    modified = parse_pdf_date(metadata.get("pdf_modified"))
    if created and modified and modified - created > timedelta(days=MODIFIED_AFTER_DAYS):
        days = (modified - created).days
        reasons.append(f"PDF was modified {days} day(s) after it was created")

    if not reasons:
        return None
    return FraudFlag(
        indicator=FraudIndicator.DOCUMENT_TAMPERING_SUSPECTED,
        description="; ".join(reasons),
    )


# ----------------------------------------------------------------------------
# Trigger 3: employer not recognised
# ----------------------------------------------------------------------------
def _employer_not_recognised(fields: ExtractedFields, cfg: dict) -> Optional[FraudFlag]:
    known = cfg.get("known_employers") or []
    if not known or not fields.employer_name:      # empty list = check switched off
        return None
    threshold = float(cfg.get("employer_match_threshold", 0.85))
    name = normalise_name(fields.employer_name)
    if any(similarity(name, normalise_name(k)) >= threshold for k in known):
        return None
    return FraudFlag(
        indicator=FraudIndicator.EMPLOYER_NOT_RECOGNISED,
        description=f"Employer '{fields.employer_name}' is not in the recognised employers list",
    )


# ----------------------------------------------------------------------------
# Trigger 4: income above configured threshold
# ----------------------------------------------------------------------------
def _income_above_threshold(
    fields: ExtractedFields, rules: dict, cfg: dict
) -> Optional[FraudFlag]:
    thresholds = cfg.get("income_threshold_annual", {})
    multipliers = rules["income"]["annualise_multiplier"]
    currency, frequency, amount = fields.income_currency, fields.income_frequency, fields.income_amount

    if amount is None or currency not in thresholds or frequency not in multipliers:
        return None                                # nothing configured to compare with
    annual = amount * multipliers[frequency]
    limit = thresholds[currency]
    if annual <= limit:
        return None
    return FraudFlag(
        indicator=FraudIndicator.INCOME_ABOVE_THRESHOLD,
        description=(
            f"Annual income {currency} {annual:,.0f} is above the configured "
            f"threshold {currency} {limit:,.0f}"
        ),
    )


# ----------------------------------------------------------------------------
# Public function
# ----------------------------------------------------------------------------
def detect_risk_indicators(
    fields: ExtractedFields,
    metadata: dict,
    ocr_confidence: Optional[float],
    unverified_fields: list[str],
    rules: dict,
) -> list[FraudFlag]:
    """Returns the list of flags (empty list = nothing suspicious)."""
    cfg = rules["fraud"]
    candidates = [
        _low_confidence(fields, ocr_confidence, unverified_fields, cfg),
        _tampering(metadata, cfg),
        _employer_not_recognised(fields, cfg),
        _income_above_threshold(fields, rules, cfg),
    ]
    return [flag for flag in candidates if flag is not None]