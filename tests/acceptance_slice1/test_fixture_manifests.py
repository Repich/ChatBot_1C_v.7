from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/slice1"
CORPUS_PATH = REPO_ROOT / "tests/corpus/user_questions.yaml"


def _json(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _corpus_scenarios() -> dict[str, dict[str, Any]]:
    corpus = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    return {scenario["id"]: scenario for scenario in corpus["scenarios"]}


def test_all_slice1_json_fixtures_are_objects() -> None:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    assert {path.name for path in paths} == {
        "acceptance_oracles.json",
        "package_mutations.json",
        "planner_q037_context_execute.json",
        "planner_q037_missing_context.json",
        "public_contract.json",
        "transport_scenarios.json",
    }
    for path in paths:
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict), path


def test_public_contract_has_unique_black_box_boundaries() -> None:
    contract = _json("public_contract.json")
    boundaries = [
        (contract["session_create"]["method"], contract["session_create"]["path"]),
        (contract["message_create"]["method"], contract["message_create"]["path"]),
        (contract["turn"]["method"], contract["turn"]["path"]),
        (contract["session"]["method"], contract["session"]["path"]),
        (contract["sse"]["method"], contract["sse"]["path"]),
        (
            contract["package_import"]["create"]["method"],
            contract["package_import"]["create"]["path"],
        ),
        (
            contract["package_import"]["replace"]["method"],
            contract["package_import"]["replace"]["path"],
        ),
        (
            contract["diagnostic_zip"]["method"],
            contract["diagnostic_zip"]["path"],
        ),
    ]
    assert len(boundaries) == len(set(boundaries))
    assert contract["message_create"]["status"] == 202
    assert contract["sse"]["content_type"] == "text/event-stream"


def test_mandatory_demo_oracles_trace_exactly_to_corpus() -> None:
    oracle = _json("acceptance_oracles.json")
    corpus = _corpus_scenarios()
    expected_ids = {"Q001", "Q011", "Q036", "Q037", "Q102"}

    assert set(oracle["scenarios"]) == expected_ids
    for scenario_id, acceptance in oracle["scenarios"].items():
        corpus_scenario = corpus[scenario_id]
        assert acceptance["required_capability_ids"] == corpus_scenario["capability_ids"]
        assert acceptance["oracles"]

    assert oracle["scenarios"]["Q037"]["previous_scenario"] == "Q036"
    assert oracle["scenarios"]["Q036"]["required_skill"] == (
        "sales_order_header_status_by_number"
    )
    assert oracle["scenarios"]["Q037"]["number_lookup_calls_after_q036"] == 0
    assert corpus["Q037"]["context"]["previous_scenario"] == "Q036"
    assert oracle["scenarios"]["Q001"]["citation_scheme"] == "ut-help://"
    assert oracle["slo_seconds"] == 30


def test_sales_order_context_negative_oracles_are_closed() -> None:
    cases = _json("acceptance_oracles.json")["sales_order_context_cases"]
    assert set(cases) == {
        "q036_ambiguous",
        "q037_missing_context",
        "q037_ref_mismatch",
    }
    assert cases["q036_ambiguous"] == {
        "expected_outcome": "clarification_required",
        "context_exports": 0,
        "must_not_select_first_row": True,
    }
    assert cases["q037_missing_context"]["mcp_execute_query_calls"] == 0
    assert cases["q037_ref_mismatch"]["expected_outcome"] == "contract_error"


def test_unimplemented_product_acceptance_is_not_reported_as_passed() -> None:
    oracle = _json("acceptance_oracles.json")
    assert oracle["status"] == "blocked_implementation"


def test_transport_values_are_explicitly_synthetic_and_secret_free() -> None:
    transport = _json("transport_scenarios.json")
    serialized = json.dumps(transport, ensure_ascii=False)
    assert transport["synthetic"] is True
    assert "SYNTHETIC" in serialized
    q001_chunk = transport["documentation_chunks"]["q001"]
    assert q001_chunk["source_kind"] == "builtin_help"
    assert q001_chunk["citation"].startswith("ut-help://")
    for prohibited in ("/Users/", "C:\\\\Users\\", "Bearer ", "api_key", "password"):
        assert prohibited not in serialized


def test_package_negative_cases_are_independent_and_typed() -> None:
    manifest = _json("package_mutations.json")
    cases = manifest["cases"]
    assert {case["id"] for case in cases} == {
        "compatibility_mismatch",
        "digest_mismatch",
        "schema_invalid",
    }
    assert len({case["expected_error"] for case in cases}) == len(cases)
    assert all(case["expected_http_status"] == 422 for case in cases)
    assert next(
        case for case in cases if case["id"] == "compatibility_mismatch"
    )["recompute_package_digest"] is True
