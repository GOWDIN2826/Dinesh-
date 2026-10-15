"""
business_rules.py - diagram box "Business Rule Validation (Job Letter Rule Set)"

Rules run in the diagram's order.  The FIRST rule that fails ends the flow
(red box -> error -> FAILED webhook):

  Rule 1  Addressed to Sagicor Bank?        NOT_ADDRESSED_TO_SAGICOR_BANK
  Rule 2  Document Date within 90 Days?     JOB_LETTER_EXPIRED
  Rule 3  Income Stated?                    INCOME_NOT_STATED
  Rule 4  Employment Start Date Present?    EMPLOYMENT_START_DATE_MISSING
  Rule 5  Job Title Present?                JOB_TITLE_MISSING

All limits (90 days, Sagicor name variants, allowed income frequencies) come from
rules/job_letter_rules_v1.json - change them there, not here.
"""
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Callable, Optional

from schemas import ErrorCode, ExtractedFields, PipelineError


# ----------------------------------------------------------------------------
# Name matching helpers (also used by fraud.py)
# ----------------------------------------------------------------------------
def normalise_name(text: Optional[str]) -> str:
    """'Sagicor Bank Jamaica Limited.' -> 'sagicor bank jamaica ltd'"""
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\blimited\b", "ltd", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_recognised_name(text: Optional[str], names: list[str], threshold: float) -> Optional[str]:
    """
    Looks for any recognised name INSIDE the text.
      1. exact match (after tidying case, punctuation, Limited/Ltd)
      2. tolerant match: same number of words, EVERY word must be very similar,
         so a typo such as 'Sagicar' or an OCR slip still matches, but a different
         company such as 'Sagicor Life Jamaica' does not.
    Returns the matched name, or None.
    """
    haystack = normalise_name(text)
    if not haystack:
        return None
    padded = f" {haystack} "
    for name in names:
        target = normalise_name(name)
        if target and f" {target} " in padded:
            return name

    words = haystack.split()
    for name in names:
        target_words = normalise_name(name).split()
        size = len(target_words)
        if size == 0:
            continue
        for start in range(len(words) - size + 1):
            window = words[start : start + size]
            if all(similarity(w, t) >= threshold for w, t in zip(window, target_words)):
                return name
    return None


# ----------------------------------------------------------------------------
# Result object
# ----------------------------------------------------------------------------
@dataclass
class RuleResult:
    step: str                       # audit-log step name
    rule: str                       # "Rule 1"
    description: str
    passed: bool
    detail: str
    error: Optional[PipelineError] = None

    def as_summary(self) -> dict:
        """One line of the 'Validation Summary' sent in the COMPLETED webhook."""
        return {
            "rule": self.rule,
            "description": self.description,
            "passed": self.passed,
            "detail": self.detail,
        }


def _pass(step: str, rule: str, description: str, detail: str) -> RuleResult:
    return RuleResult(step, rule, description, True, detail)


def _fail(step, rule, description, code: ErrorCode, detail: str) -> RuleResult:
    return RuleResult(step, rule, description, False, detail, PipelineError(code, details=detail))


def _short(text: Optional[str], limit: int = 100) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ----------------------------------------------------------------------------
# The five rules
# ----------------------------------------------------------------------------
def _rule1_addressed_to_sagicor(f: ExtractedFields, submitted: date, rules: dict) -> RuleResult:
    step, rule = "RULE_1_ADDRESSED_TO_SAGICOR", "Rule 1"
    desc = "The letter must be addressed to Sagicor Bank"
    cfg = rules["sagicor_bank"]
    match = find_recognised_name(f.addressed_to, cfg["recognised_names"], cfg["match_threshold"])
    if match:
        return _pass(step, rule, desc, f"Addressed to Sagicor Bank (matched '{match}')")
    return _fail(
        step, rule, desc, ErrorCode.NOT_ADDRESSED_TO_SAGICOR_BANK,
        f"Job Letter is addressed to '{_short(f.addressed_to)}', not Sagicor Bank",
    )


def _rule2_within_validity(f: ExtractedFields, submitted: date, rules: dict) -> RuleResult:
    step, rule = "RULE_2_DOCUMENT_DATE", "Rule 2"
    max_age = int(rules["letter_validity"]["max_age_days"])
    desc = f"Letter date must be within {max_age} days of the submission date"
    if f.letter_date is None:
        return _fail(step, rule, desc, ErrorCode.JOB_LETTER_EXPIRED, "Letter date could not be read")

    age = (submitted - f.letter_date).days
    # A letter dated in the FUTURE cannot be genuine (1 day allowed for time zones).
    # The diagram has no separate error for this, so it uses JOB_LETTER_EXPIRED.
    if age < -1:
        return _fail(
            step, rule, desc, ErrorCode.JOB_LETTER_EXPIRED,
            f"Letter date {f.letter_date} is after the submission date {submitted}",
        )
    if age > max_age:
        return _fail(
            step, rule, desc, ErrorCode.JOB_LETTER_EXPIRED,
            f"Job Letter is {age} days old (limit {max_age} days)",
        )
    return _pass(step, rule, desc, f"Letter is {max(age, 0)} day(s) old (limit {max_age})")


def _rule3_income_stated(f: ExtractedFields, submitted: date, rules: dict) -> RuleResult:
    step, rule = "RULE_3_INCOME_STATED", "Rule 3"
    desc = "Income must be stated with amount, currency and frequency"
    allowed = rules["income"]["allowed_frequencies"]

    missing = []
    if f.income_amount is None or f.income_amount <= 0:
        missing.append("amount")
    if not f.income_currency:
        missing.append("currency")
    if not f.income_frequency or f.income_frequency not in allowed:
        missing.append("frequency")

    if missing:
        return _fail(
            step, rule, desc, ErrorCode.INCOME_NOT_STATED,
            "Income is missing or incomplete: " + ", ".join(missing),
        )
    return _pass(
        step, rule, desc,
        f"Income stated: {f.income_currency} {f.income_amount:,.2f} {f.income_frequency}",
    )


def _rule4_start_date(f: ExtractedFields, submitted: date, rules: dict) -> RuleResult:
    step, rule = "RULE_4_EMPLOYMENT_START_DATE", "Rule 4"
    desc = "The letter must confirm the date employment commenced"
    if f.employment_start_date is None:
        return _fail(
            step, rule, desc, ErrorCode.EMPLOYMENT_START_DATE_MISSING,
            "Employment start date is missing",
        )
    return _pass(step, rule, desc, f"Employment started {f.employment_start_date}")


def _rule5_job_title(f: ExtractedFields, submitted: date, rules: dict) -> RuleResult:
    step, rule = "RULE_5_JOB_TITLE", "Rule 5"
    desc = "The letter must state the applicant's job title / position held"
    if not f.job_title or not f.job_title.strip():
        return _fail(step, rule, desc, ErrorCode.JOB_TITLE_MISSING, "Job title is missing")
    return _pass(step, rule, desc, f"Job title: {f.job_title.strip()}")


RULES: list[Callable[[ExtractedFields, date, dict], RuleResult]] = [
    _rule1_addressed_to_sagicor,
    _rule2_within_validity,
    _rule3_income_stated,
    _rule4_start_date,
    _rule5_job_title,
]


def evaluate_business_rules(
    fields: ExtractedFields, submission_date: date, rules: dict
) -> list[RuleResult]:
    """Evaluate and return all five rules in order so every outcome is reportable."""
    results: list[RuleResult] = []
    for rule_function in RULES:
        results.append(rule_function(fields, submission_date, rules))
    return results