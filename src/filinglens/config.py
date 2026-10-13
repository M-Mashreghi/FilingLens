"""Typed application settings loaded from the environment / `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SEC publishes a 10 req/s ceiling; this is a policy value, not a permanent constant.
SEC_PUBLISHED_MAX_RPS = 10.0


def _alias(name: str) -> AliasChoices:
    return AliasChoices(name, name.lower())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias=_alias("FILINGLENS_ENV")
    )
    log_level: str = Field(default="INFO", validation_alias=_alias("FILINGLENS_LOG_LEVEL"))
    log_json: bool = Field(default=False, validation_alias=_alias("FILINGLENS_LOG_JSON"))
    db_path: Path = Field(
        default=Path("./data/filinglens.db"), validation_alias=_alias("FILINGLENS_DB_PATH")
    )
    raw_dir: Path = Field(default=Path("./data/raw"), validation_alias=_alias("FILINGLENS_RAW_DIR"))
    cache_dir: Path = Field(
        default=Path("./cache"), validation_alias=_alias("FILINGLENS_CACHE_DIR")
    )

    sec_user_agent: str = Field(default="", validation_alias=_alias("SEC_USER_AGENT"))
    sec_max_rps: float = Field(default=5.0, gt=0, validation_alias=_alias("SEC_MAX_RPS"))
    sec_max_concurrency: int = Field(
        default=2, ge=1, le=10, validation_alias=_alias("SEC_MAX_CONCURRENCY")
    )
    sec_live_mode: bool = Field(default=False, validation_alias=_alias("SEC_LIVE_MODE"))

    llm_provider: str = Field(default="", validation_alias=_alias("LLM_PROVIDER"))
    llm_model: str = Field(default="", validation_alias=_alias("LLM_MODEL"))
    llm_api_key: SecretStr | None = Field(default=None, validation_alias=_alias("LLM_API_KEY"))

    @field_validator("sec_max_rps")
    @classmethod
    def _below_sec_ceiling(cls, value: float) -> float:
        if value > SEC_PUBLISHED_MAX_RPS:
            raise ValueError(
                f"SEC_MAX_RPS={value} exceeds SEC published ceiling {SEC_PUBLISHED_MAX_RPS}"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        return value.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
