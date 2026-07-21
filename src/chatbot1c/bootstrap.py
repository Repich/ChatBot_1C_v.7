"""Minimal composition root for contract-harness slice 0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chatbot1c.config import Settings, load_settings
from chatbot1c.contracts.harness import ContractHarness


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    contracts: ContractHarness


def build_application(
    settings: Settings | None = None,
    *,
    project_root: Path | str | None = None,
    env_file: Path | str | None = ".env.local",
) -> Application:
    """Compose local services without creating external adapters or reading at import."""

    resolved_settings = settings if settings is not None else load_settings(env_file)
    return Application(
        settings=resolved_settings,
        contracts=ContractHarness.discover(project_root),
    )
