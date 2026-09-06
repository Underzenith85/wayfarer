"""Validated application configuration."""

from pathlib import Path
from typing import ClassVar, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="WAYFARER_", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)
    db: Path = Path("data/wayfarer.sqlite3")
    database_url: SecretStr | None = None
    log_level: str = "INFO"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    model_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    db_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def complete_model_configuration(self) -> Self:
        if (self.openai_api_key is None) != (self.openai_model is None):
            raise ValueError("OPENAI_API_KEY and OPENAI_MODEL must be configured together")
        return self

    @property
    def llm_enabled(self) -> bool:
        return self.openai_api_key is not None and self.openai_model is not None
