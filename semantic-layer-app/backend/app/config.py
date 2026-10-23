"""Process-level settings (from env / .env) and runtime settings (stored in the DB).

Process settings need a restart to change. Runtime settings (row caps, sample sizes,
timeouts, ...) are editable from the Settings page (NFR-07).
"""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_prefix="SLA_", extra="ignore")

    data_dir: Path = ROOT_DIR / "data"
    database_url: Optional[str] = None
    # Fernet key for encrypting API keys at rest. Generated into data/.secret_key if unset.
    fernet_key: Optional[str] = None
    max_upload_mb: int = 500
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    log_json: bool = True

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "sources"


settings = Settings()


class RuntimeSettings(BaseModel):
    """User-editable limits and policies. Persisted as JSON in the app_settings table."""

    # P3-01 / P3-02: what generation is allowed to see
    sample_values_per_column: int = Field(5, ge=0, le=20)
    strict_privacy: bool = False
    # P2-12: profile on a sample when the file is larger than this
    profile_sample_rows: int = Field(200_000, ge=1_000)
    # P6-04: query limits
    query_timeout_s: float = Field(10.0, gt=0, le=300)
    row_cap: int = Field(200, ge=1, le=10_000)
    get_rows_max: int = Field(50, ge=1, le=50)
    # P6-05 / P6-16: tool policies
    raw_sql_enabled: bool = False
    aggregates_only: bool = False
    # P7-09 / P7-14: agent loop
    max_agent_iterations: int = Field(6, ge=1, le=20)
    agent_timeout_s: float = Field(180.0, gt=0, le=1800)
    context_budget_tokens: int = Field(24_000, ge=2_000)
    # P8-04: store exact LLM payloads in the call log (secrets are never part of payloads)
    debug_payloads: bool = False
    # P1-07: defaults per purpose. None -> the provider marked is_default and its default model.
    generation_provider_id: Optional[str] = None
    generation_model: Optional[str] = None
    chat_provider_id: Optional[str] = None
    chat_model: Optional[str] = None
