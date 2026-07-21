from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chatbot1c.bootstrap import build_application
from chatbot1c.config import Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_settings_defaults_do_not_create_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-created"

    settings = Settings(app_data_dir=data_dir)

    assert not data_dir.exists()
    assert settings.app_host == "127.0.0.1"
    assert settings.effective_database_url.endswith("/not-created/chatbot1c.sqlite3")


def test_loader_reads_environment_only_when_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_PORT", "8123")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret-value")

    settings = load_settings(env_file=None)

    assert settings.app_port == 8123
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "synthetic-secret-value"
    assert "synthetic-secret-value" not in repr(settings)


def test_settings_reject_unknown_constructor_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Settings(unknown_setting=True)


def test_composition_root_uses_explicit_settings_without_external_clients(
    tmp_path: Path,
) -> None:
    settings = Settings(app_data_dir=tmp_path)

    application = build_application(settings, project_root=ROOT)

    assert application.settings is settings
    assert "skill.schema.json" in application.contracts.schemas.names
    assert not tmp_path.joinpath("chatbot1c.sqlite3").exists()
