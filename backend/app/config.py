from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_name: str = "z-procure"
    app_title: str = "Z-Procure"
    demo_mode: bool = True
    database_url: str = "postgresql+psycopg://procure:procure@localhost:5432/procure"
    test_database_url: str = "postgresql+psycopg://procure:procure@localhost:5432/procure_test"
    jwt_secret: str = "dev-only-secret-change-me"  # noqa: S105  dev default, .env overrides
    llm_provider: str = "mock"
    gemini_api_key: str = ""
    gemini_parse_model: str = ""
    gemini_write_model: str = ""
    storage_dir: Path = ROOT / "storage"
    frontend_origin: str = "http://localhost:3000"
    cookie_secure: bool = False  # True behind HTTPS
    log_level: str = "INFO"

    @field_validator("storage_dir")
    @classmethod
    def _anchor(cls, v: Path) -> Path:
        """A relative STORAGE_DIR means relative to the repo, whatever the working directory."""
        return v if v.is_absolute() else ROOT / v


@lru_cache
def get_settings() -> Settings:
    return Settings()
