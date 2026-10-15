"""
config.py - central settings for the Job Letter Verification system.

Every value comes from the .env file, so you can change behaviour without
touching code.  Every other module simply does:

    from app.config import settings
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root folder  ->  .../job-letter/
BASE_DIR = Path(__file__).resolve().parent.parent

# Workflow step "File Format Validation": PDF, JPEG, PNG, DOCX
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".docx"}


class Settings(BaseSettings):
    """Field names match the names in .env (upper/lower case does not matter)."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- General ----
    app_env: str = "development"
    max_upload_mb: int = 10

    # ---- Storage ----
    upload_dir: str = "storage/uploads"
    extracted_text_dir: str = "storage/extracted_text"
    db_path: str = "storage/job_letter.db"
    rules_path: str = "rules/job_letter_rules_v1.json"

    # ---- Sagicor Bank Portal webhook ----
    portal_webhook_url: str = "http://127.0.0.1:8000/mock-portal/webhook"
    portal_webhook_secret: str = "change-me"
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 3

    # ---- Security scan ----
    clamav_enabled: bool = False
    clamav_host: str = "127.0.0.1"
    clamav_port: int = 3310

    # ---- OCR ----
    tesseract_cmd: str = ""
    ocr_lang: str = "eng"
    ocr_min_confidence: int = 50

    # ---- LLM / OpenRouter ----
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"
    llm_fallback_models: str = "anthropic/claude-3.5-haiku,google/gemini-2.0-flash-001"
    llm_max_tokens: int = 1500

    # ---- Helpers: turn relative paths into full paths ----
    @staticmethod
    def _resolve(relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def upload_path(self) -> Path:
        return self._resolve(self.upload_dir)

    @property
    def extracted_text_path(self) -> Path:
        return self._resolve(self.extracted_text_dir)

    @property
    def db_file(self) -> Path:
        return self._resolve(self.db_path)

    @property
    def rules_file(self) -> Path:
        return self._resolve(self.rules_path)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # make sure the folders exist
    s.upload_path.mkdir(parents=True, exist_ok=True)
    s.extracted_text_path.mkdir(parents=True, exist_ok=True)
    s.db_file.parent.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()