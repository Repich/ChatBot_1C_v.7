from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import chatbot1c.contracts.harness as harness_module
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.contracts.json_limits import (
    DOCUMENT_BYTE_LIMITS,
    MAX_EMBEDDED_SKILL_BYTES,
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    loads_bounded_json,
)

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "tests/fixtures/contracts/valid"


def _codes(error: ContractValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def _valid_package() -> dict:
    return json.loads((VALID / "skill_package.json").read_text(encoding="utf-8"))


def test_limit_constants_match_the_accepted_boundary() -> None:
    assert DOCUMENT_BYTE_LIMITS == {
        "skill": 1_048_576,
        "skill_package": 33_554_432,
        "planner_output": 262_144,
        "evidence_bundle": 67_108_864,
    }
    assert MAX_JSON_DEPTH == 32
    assert MAX_JSON_NODES == 500_000
    assert MAX_JSON_ARRAY_ITEMS == 100_000
    assert MAX_EMBEDDED_SKILL_BYTES == 1_048_576


def test_document_byte_limit_is_rejected_before_schema_validation() -> None:
    payload = json.dumps(
        {
            "document_type": "planner_output",
            "padding": "x" * DOCUMENT_BYTE_LIMITS["planner_output"],
        },
        separators=(",", ":"),
    ).encode()

    with pytest.raises(ContractValidationError) as caught:
        loads_bounded_json(payload)

    assert _codes(caught.value) == {"JSON_BYTES_LIMIT"}


def test_depth_limit_is_rejected_by_preflight_scanner() -> None:
    nested = "null"
    for _ in range(MAX_JSON_DEPTH + 1):
        nested = f"[{nested}]"
    payload = (
        '{"document_type":"planner_output","nested":' + nested + "}"
    ).encode()

    with pytest.raises(ContractValidationError) as caught:
        loads_bounded_json(payload)

    assert _codes(caught.value) == {"JSON_DEPTH_LIMIT"}
    assert caught.value.issues[0].json_pointer.startswith("/nested/")


def test_single_array_limit_is_rejected_by_preflight_scanner() -> None:
    values = ",".join("null" for _ in range(MAX_JSON_ARRAY_ITEMS + 1))
    payload = (
        '{"document_type":"evidence_bundle","values":[' + values + "]}"
    ).encode()

    with pytest.raises(ContractValidationError) as caught:
        loads_bounded_json(payload)

    assert _codes(caught.value) == {"JSON_ARRAY_LIMIT"}
    assert caught.value.issues[0].json_pointer == "/values"


def test_total_nodes_are_bounded_without_treating_all_arrays_as_one() -> None:
    branch = "[" + ",".join("null" for _ in range(83_333)) + "]"
    payload = (
        '{"document_type":"evidence_bundle","branches":['
        + ",".join(branch for _ in range(6))
        + "]}"
    ).encode()

    with pytest.raises(ContractValidationError) as caught:
        loads_bounded_json(payload)

    assert _codes(caught.value) == {"JSON_NODE_LIMIT"}


def test_embedded_skill_canonical_limit_runs_before_package_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_module, "MAX_EMBEDDED_SKILL_BYTES", 16)
    document = {
        "document_type": "skill_package",
        "skills": [{"synthetic_padding": "x" * 32}],
    }

    with pytest.raises(ContractValidationError) as caught:
        ContractHarness.discover(ROOT).validate_document(document)

    assert _codes(caught.value) == {"JSON_BYTES_LIMIT"}
    assert caught.value.issues[0].json_pointer == "/skills/0"


def test_dependency_lock_is_bounded_to_one_thousand_entries() -> None:
    package = copy.deepcopy(_valid_package())
    package["dependency_lock"] = [
        {
            "skill_id": f"ut.synthetic.lock-{index}",
            "version": "1.0.0",
            "digest": f"{index:064x}",
        }
        for index in range(1001)
    ]

    with pytest.raises(ContractValidationError) as caught:
        ContractHarness.discover(ROOT).validate_document(
            package, verify_integrity=False
        )

    assert any(
        issue.json_pointer == "/dependency_lock" and issue.keyword == "maxItems"
        for issue in caught.value.issues
    )
