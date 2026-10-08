"""Application settings, read from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # LLM (NVIDIA API)
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_primary_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    llm_fallback_model: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_temperature_extract: float = 0.1
    llm_temperature_reply: float = 0.5
    llm_max_completion_tokens: int = 1024
    llm_timeout_s: float = 15.0  # per attempt; a slow model hands over to the fallback
    llm_requests_per_minute: int = 36
    llm_max_attempts: int = 3
    llm_reply_writer_enabled: bool = True  # LLM phrases questions/small talk (else templates)

    # Supervisor approval for large returns
    approval_enabled: bool = False
    approval_threshold: float = 1000.0

    # Conversation limits
    auth_max_attempts: int = 3
    max_queue: int = 8
    max_asks_per_slot: int = 4
    max_declines: int = 3
    recursion_limit: int = 100

    # Paths (relative paths are resolved against the project root)
    data_dir: Path = Path("data")
    var_dir: Path = Path("var")

    @field_validator("llm_temperature_extract", "llm_temperature_reply")
    @classmethod
    def _temperature_in_range(cls, v: float) -> float:
        # NVIDIA's hosted endpoint accepts 0 < t <= 1.
        if not 0 < v <= 1:
            raise ValueError("temperature must be in (0, 1]")
        return v

    @field_validator("data_dir", "var_dir")
    @classmethod
    def _resolve(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def db_original(self) -> Path:
        return self.data_dir / "db.json"

    @property
    def db_working(self) -> Path:
        return self.var_dir / "db.working.json"

    @property
    def checkpoint_db(self) -> Path:
        return self.var_dir / "checkpoints.sqlite"

    @property
    def app_db(self) -> Path:
        return self.var_dir / "app.sqlite"

    @property
    def tasks_path(self) -> Path:
        return self.data_dir / "tasks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
