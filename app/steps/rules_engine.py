"""
rules_engine.py - diagram boxes:

  "Job Letter Validation Rules Repository"  -> the JSON rules file
  "Job Letter Rules Engine"                 -> the RulesEngine class below
  "Document Category Valid? (Job Letter)"   -> check_document_category()
  "Mandatory Fields Extracted?"             -> check_mandatory_fields()

The rules file is re-read automatically whenever you save changes to it, so you
can edit a rule and test again without restarting the server.
Batch 4 (business rules 1-5, fraud checks) reads its settings from the same engine.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import settings
from schemas import ErrorCode, ExtractedFields, PipelineError

REQUIRED_SECTIONS = (
    "document_category", "mandatory_fields", "sagicor_bank",
    "letter_validity", "income",
)


@dataclass
class CategoryResult:
    valid: bool
    category: Optional[str]
    title_matches: list[str] = field(default_factory=list)
    body_matches: list[str] = field(default_factory=list)
    reject_matches: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "category": self.category,
            "title_matches": self.title_matches,
            "body_matches": self.body_matches,
            "reject_matches": self.reject_matches,
        }


def _normalise(text: str) -> str:
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", text)


def _find_phrases(text: str, phrases: list[str]) -> list[str]:
    found = []
    for phrase in phrases:
        pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
        if re.search(pattern, text):
            found.append(phrase)
    return found


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


class RulesEngine:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else settings.rules_file
        self._rules: Optional[dict] = None
        self._mtime: Optional[float] = None

    # ---------------- load (with auto-reload) ----------------
    @property
    def rules(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            raise PipelineError(
                ErrorCode.INTERNAL_PROCESSING_ERROR,
                details=f"Validation rules file not found: {self.path.name}",
            ) from exc

        if self._rules is None or mtime != self._mtime:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise PipelineError(
                    ErrorCode.INTERNAL_PROCESSING_ERROR,
                    details=f"Validation rules file is not valid JSON (line {exc.lineno})",
                ) from exc
            missing = [s for s in REQUIRED_SECTIONS if s not in loaded]
            if missing:
                raise PipelineError(
                    ErrorCode.INTERNAL_PROCESSING_ERROR,
                    details="Validation rules file is missing section(s): " + ", ".join(missing),
                )
            self._rules, self._mtime = loaded, mtime
        return self._rules

    @property
    def info(self) -> dict:
        r = self.rules
        return {"rule_set": r.get("rule_set"), "version": r.get("version")}

    # ---------------- Document Category Valid? ----------------
    def check_document_category(self, text: str) -> CategoryResult:
        cfg = self.rules["document_category"]
        norm = _normalise(text)

        titles = _find_phrases(norm, cfg["title_phrases"])
        bodies = _find_phrases(norm, cfg["body_phrases"])
        rejects = _find_phrases(norm, cfg["reject_phrases"])

        valid = bool(titles) or (
            len(bodies) >= cfg["min_body_matches"] and not rejects
        )

        category = None
        if valid:
            confirmation_words = ("confirmation", "confirm")
            is_confirmation = any(w in " ".join(titles + bodies).lower() for w in confirmation_words)
            accepted = cfg["accepted_categories"]
            category = accepted[-1] if is_confirmation else accepted[0]

        return CategoryResult(valid, category, titles, bodies, rejects)

    # ---------------- Mandatory Fields Extracted? ----------------
    def check_mandatory_fields(self, fields: ExtractedFields) -> list[str]:
        """Returns the labels of mandatory fields that are missing (empty list = all present)."""
        missing = []
        for item in self.rules["mandatory_fields"]:
            if not any(_has_value(getattr(fields, name, None)) for name in item["fields"]):
                missing.append(item["label"])
        return missing


_engine: Optional[RulesEngine] = None


def get_rules_engine() -> RulesEngine:
    global _engine
    if _engine is None:
        _engine = RulesEngine()
    return _engine