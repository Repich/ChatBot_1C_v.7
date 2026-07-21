"""Environment-backed production settings, loaded only on explicit invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from platformdirs import user_data_path
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    return user_data_path("chatbot1c", "ChatBot1C", ensure_exists=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file_encoding="utf-8",
        env_prefix="",
        extra="forbid",
        frozen=True,
    )

    app_host: str = "127.0.0.1"
    app_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    app_data_dir: Path = Field(default_factory=_default_data_dir)
    database_url: str | None = None

    deepseek_base_url: Annotated[str, Field(pattern=r"^https?://")] = (
        "https://api.deepseek.com"
    )
    deepseek_model: Annotated[str, Field(min_length=1)] = "deepseek-chat"
    deepseek_api_key: SecretStr | None = None

    mcp_url: Annotated[str, Field(pattern=r"^https?://")] = "http://127.0.0.1:6003/mcp"
    mcp_channel: Annotated[str, Field(min_length=1, max_length=160)] = "default"
    ut_config_dir: Path | None = None
    database_profile_path: Path | None = None
    starter_package_path: Path | None = None
    auto_import_builtin_skills: bool = False
    build_help_index_on_start: bool = False

    target_configuration_id: Literal["УправлениеТорговлейБазовая"] = (
        "УправлениеТорговлейБазовая"
    )
    target_release: Literal["11.5.27.56"] = "11.5.27.56"
    target_compatibility_mode: Literal["8.3.27"] = "8.3.27"

    default_list_limit: Annotated[int, Field(ge=1, le=1000)] = 20
    max_mcp_rows: Annotated[int, Field(ge=1, le=1000)] = 1000
    request_deadline_seconds: Annotated[int, Field(ge=1, le=300)] = 90
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        database_path = (self.app_data_dir / "chatbot1c.sqlite3").resolve()
        return f"sqlite:///{database_path.as_posix()}"


def load_settings(
    env_file: Path | str | None = ".env.local",
) -> Settings:
    """Read environment and optional dotenv file; imports alone never call this."""

    settings_sources: dict[str, Any] = {"_env_file": env_file}
    return Settings(**settings_sources)
