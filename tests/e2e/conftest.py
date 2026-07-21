from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E_DIR))
support = importlib.import_module("support")
AppClient = support.AppClient
CliClient = support.CliClient
FixtureDriver = support.FixtureDriver
ScenarioController = support.ScenarioController


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/slice1"


def _required_environment(name: str, purpose: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"blocked_implementation: {name} is required for {purpose}")
    return value


@pytest.fixture(scope="session")
def public_contract() -> dict[str, Any]:
    return json.loads(
        (FIXTURE_DIR / "public_contract.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="session")
def acceptance_oracles() -> dict[str, Any]:
    return json.loads(
        (FIXTURE_DIR / "acceptance_oracles.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="session")
def corpus_questions() -> dict[str, str]:
    corpus = yaml.safe_load(
        (REPO_ROOT / "tests/corpus/user_questions.yaml").read_text(encoding="utf-8")
    )
    return {scenario["id"]: scenario["question"] for scenario in corpus["scenarios"]}


@pytest.fixture(scope="session")
def app_client(public_contract: dict[str, Any]) -> AppClient:
    base_url = _required_environment("SLICE1_BASE_URL", "chat acceptance")
    return AppClient(base_url, public_contract)


@pytest.fixture(scope="session")
def clean_app_client(public_contract: dict[str, Any]) -> AppClient:
    base_url = _required_environment(
        "SLICE1_CLEAN_BASE_URL", "clean catalog import acceptance"
    )
    return AppClient(base_url, public_contract)


@pytest.fixture(scope="session")
def fixture_driver() -> FixtureDriver:
    fixture_url = _required_environment(
        "SLICE1_FIXTURE_URL", "DeepSeek/MCP fixture control"
    )
    return FixtureDriver(fixture_url)


@pytest.fixture(scope="session")
def scenario_controller(fixture_driver: FixtureDriver) -> ScenarioController:
    responses: dict[str, dict[str, Any]] = {}
    path_value = os.getenv("SLICE1_PLANNER_RESPONSES_PATH")
    if path_value:
        loaded = json.loads(Path(path_value).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not all(
            isinstance(value, dict) for value in loaded.values()
        ):
            pytest.fail("SLICE1_PLANNER_RESPONSES_PATH must map scenarios to objects")
        responses = loaded
    return ScenarioController(fixture_driver, responses)


@pytest.fixture(scope="session")
def package_path() -> Path:
    path = Path(
        _required_environment("SLICE1_PACKAGE_PATH", "portable package acceptance")
    )
    assert path.is_file(), path
    return path


@pytest.fixture(scope="session")
def replacement_package_path() -> Path:
    path = Path(
        _required_environment(
            "SLICE1_REPLACEMENT_PACKAGE_PATH", "replace and hot reload acceptance"
        )
    )
    assert path.is_file(), path
    return path


@pytest.fixture(scope="session")
def cli_client(fixture_driver: FixtureDriver) -> CliClient:
    command = _required_environment("SLICE1_CLI", "CLI portability acceptance")
    return CliClient(command, fixture_driver.http.base_url)


@pytest.fixture(scope="session")
def replace_if_match() -> str:
    return _required_environment(
        "SLICE1_REPLACE_IF_MATCH", "explicit multi-skill replace precondition"
    )


@pytest.fixture(scope="session")
def hot_reload_question() -> str:
    return _required_environment(
        "SLICE1_HOT_RELOAD_QUESTION", "post-replace hot reload acceptance"
    )
