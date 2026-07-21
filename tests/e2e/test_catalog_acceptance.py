from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785
from support import AppClient, CliClient, FixtureDriver, ScenarioController

REPO_ROOT = Path(__file__).resolve().parents[2]
MUTATIONS_PATH = REPO_ROOT / "tests/fixtures/slice1/package_mutations.json"


def _set_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    tokens = [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer.lstrip("/").split("/")
    ]
    parent: Any = document
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    final = tokens[-1]
    if isinstance(parent, list):
        parent[int(final)] = value
    else:
        parent[final] = value


def _package_digest(document: dict[str, Any]) -> str:
    digest_input = copy.deepcopy(document)
    digest_input.pop("integrity", None)
    return hashlib.sha256(rfc8785.dumps(digest_input)).hexdigest()


def _mutated_packages(package_path: Path, target_dir: Path) -> list[tuple[dict[str, Any], Path]]:
    manifest = json.loads(MUTATIONS_PATH.read_text(encoding="utf-8"))
    source = json.loads(package_path.read_text(encoding="utf-8"))
    results: list[tuple[dict[str, Any], Path]] = []
    for case in manifest["cases"]:
        document = copy.deepcopy(source)
        mutation = case["mutation"]
        _set_pointer(document, mutation["json_pointer"], mutation["value"])
        if case["recompute_package_digest"]:
            document["integrity"]["digest"] = _package_digest(document)
        path = target_dir / f"{case['id']}.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        results.append((case, path))
    return results


def _skill_triplets(result: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (skill["skill_id"], skill["version"], skill["digest"])
        for skill in result["skills"]
    }


def _assert_rejected(
    response_status: int,
    result: dict[str, Any],
    case: dict[str, Any],
    unchanged_revision: int,
) -> None:
    assert response_status == case["expected_http_status"], result
    assert result["status"] == "rejected"
    assert result["catalog_revision"] == unchanged_revision
    assert case["expected_error"] in {error["code"] for error in result["errors"]}


def test_portable_import_negative_validation_hot_reload_and_turn_pinning(
    clean_app_client: AppClient,
    cli_client: CliClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    package_path: Path,
    replacement_package_path: Path,
    replace_if_match: str,
    hot_reload_question: str,
    corpus_questions: dict[str, str],
    tmp_path: Path,
) -> None:
    initial_catalog = clean_app_client.list_skills()
    assert initial_catalog["skills"] == [], (
        "SLICE1_CLEAN_BASE_URL must point to a fresh isolated web data directory",
        initial_catalog,
    )
    initial_revision = initial_catalog["catalog_revision"]

    for case, invalid_path in _mutated_packages(package_path, tmp_path):
        response, result = clean_app_client.import_package(invalid_path, mode="create")
        _assert_rejected(response.status, result, case, initial_revision)
        assert clean_app_client.list_skills()["catalog_revision"] == initial_revision

    web_response, web_import = clean_app_client.import_package(
        package_path, mode="create"
    )
    assert web_response.status == 200, web_import
    assert web_import["status"] == "accepted"
    assert web_import["catalog_revision"] > initial_revision

    cli_data_dir = tmp_path / "clean-cli-data"
    cli_data_dir.mkdir()
    assert list(cli_data_dir.iterdir()) == []
    cli_import = cli_client.run(
        ["skills", "import", str(package_path), "--mode", "create"],
        cli_data_dir,
    )
    assert cli_import["status"] == "accepted"
    assert cli_import["catalog_revision"] == web_import["catalog_revision"]
    assert _skill_triplets(cli_import) == _skill_triplets(web_import)

    active_catalog = clean_app_client.list_skills()
    assert active_catalog["catalog_revision"] == web_import["catalog_revision"]
    assert _skill_triplets(active_catalog) == _skill_triplets(web_import)

    scenario_controller.set("q011", delay_ms=1500)
    session = clean_app_client.create_session()
    accepted = clean_app_client.send_message(
        session["session_id"], corpus_questions["Q011"]
    )
    fixture_driver.wait_for_boundary("deepseek")

    replace_response, replaced = clean_app_client.import_package(
        replacement_package_path,
        mode="replace",
        if_match=replace_if_match,
    )
    assert replace_response.status == 200, replaced
    assert replaced["status"] == "accepted"
    assert replaced["catalog_revision"] > web_import["catalog_revision"]

    in_flight_turn = clean_app_client.wait_turn(accepted["turn_id"])
    assert in_flight_turn["pinned"]["catalog_revision"] == web_import[
        "catalog_revision"
    ]

    scenario_controller.set("q011")
    next_turn, _ = clean_app_client.ask(session["session_id"], hot_reload_question)
    assert next_turn["status"] == "completed", next_turn
    assert next_turn["pinned"]["catalog_revision"] == replaced["catalog_revision"]
    reloaded = clean_app_client.get_session(session["session_id"])
    assert {accepted["turn_id"], next_turn["turn_id"]} <= {
        message["turn_id"]
        for message in reloaded["messages"]
        if message["role"] == "assistant"
    }
