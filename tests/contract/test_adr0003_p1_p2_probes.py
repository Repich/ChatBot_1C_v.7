from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "tests/fixtures/contracts"
PROBES = ROOT / "tests/fixtures/adr0003"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


P1_P2 = _json(PROBES / "p1_p2_probes.json")
LIMIT_PROBES = _json(PROBES / "document_limit_probes.json")


def _fact_coverage_error(case: dict) -> str | None:
    requirement = case["requirement"]
    producers = case["candidate_producers"]
    if not producers:
        return "PLAN_FACT_REQUIREMENT_UNMET"

    dimensions = [
        ("semantic_type", "PLAN_FACT_SEMANTIC_TYPE_MISMATCH"),
        ("value_type", "PLAN_FACT_VALUE_TYPE_MISMATCH"),
        ("cardinality", "PLAN_FACT_CARDINALITY_MISMATCH"),
        ("unit_dimension", "PLAN_FACT_UNIT_MISMATCH"),
        ("time_semantics", "PLAN_FACT_TIME_MISMATCH"),
    ]
    matching = producers
    for field, error_code in dimensions:
        narrowed = [item for item in matching if item[field] == requirement[field]]
        if not narrowed:
            return error_code
        matching = narrowed

    known_outputs = {
        (producer["step_id"], producer["fact_id"]) for producer in producers
    }
    if any(
        (output["step_id"], output["fact_id"]) not in known_outputs
        for output in case["final_outputs"]
    ):
        return "PLAN_FINAL_FACT_UNKNOWN"
    return None


def _package_lock_error(package: dict) -> str | None:
    embedded = {
        (skill["skill_id"], skill["version"]): skill["integrity"]["digest"]
        for skill in package["skills"]
    }
    required_keys = set(embedded)
    for skill in package["skills"]:
        required_keys.update(
            (dependency["skill_id"], dependency["version_range"])
            for dependency in skill["dependencies"]["skills"]
        )

    locked = {
        (entry["skill_id"], entry["version"]): entry["digest"]
        for entry in package["dependency_lock"]
    }
    if locked.keys() - required_keys:
        return "PACKAGE_LOCK_ORPHAN"
    if required_keys - locked.keys():
        return "PACKAGE_LOCK_MISSING"
    if any(locked[key] != digest for key, digest in embedded.items()):
        return "SKILL_DIGEST_CONFLICT"
    return None


def _disagreement_error(evidence: dict) -> str | None:
    facts = {fact["fact_instance_id"]: fact for fact in evidence["facts"]}
    citations = {citation["citation_id"] for citation in evidence["citations"]}
    for disagreement in evidence["documentation_disagreements"]:
        used_citations: set[str] = set()
        for position in disagreement["positions"]:
            for fact_id in position["fact_instance_ids"]:
                if fact_id not in facts:
                    return "DISAGREEMENT_FACT_UNKNOWN"
                if facts[fact_id]["fact_id"] != disagreement["subject_fact_id"]:
                    return "DISAGREEMENT_SUBJECT_MISMATCH"
            for citation_id in position["citation_ids"]:
                if citation_id not in citations:
                    return "DISAGREEMENT_CITATION_UNKNOWN"
                used_citations.add(citation_id)
        if len(used_citations) < 2:
            return "DISAGREEMENT_CITATIONS_NOT_DISTINCT"
    return None


def _build_shape(shape: dict) -> object:
    kind = shape["kind"]
    if kind == "empty_object":
        return {}
    if kind == "nested_array":
        value: object = None
        for _ in range(shape["containers"]):
            value = [value]
        return value
    if kind == "branch_arrays":
        return [
            [None] * shape["items_per_branch"]
            for _ in range(shape["branches"])
        ]
    if kind == "flat_array":
        return [None] * shape["items"]
    raise AssertionError(f"unknown synthetic shape: {kind}")


def _tree_metrics(document: object) -> tuple[int, int, int]:
    maximum_depth = 0
    node_count = 0
    maximum_array = 0
    pending = [(document, 1)]
    while pending:
        node, depth = pending.pop()
        node_count += 1
        maximum_depth = max(maximum_depth, depth)
        if isinstance(node, dict):
            pending.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            maximum_array = max(maximum_array, len(node))
            pending.extend((value, depth + 1) for value in node)
    return maximum_depth, node_count, maximum_array


def _document_limit_error(case: dict) -> str | None:
    limits = LIMIT_PROBES["limits"]
    if case["raw_bytes"] > limits["document_bytes"][case["document_type"]]:
        return "JSON_BYTES_LIMIT"
    if case.get("embedded_skill_canonical_bytes", 0) > limits[
        "embedded_skill_canonical_bytes"
    ]:
        return "JSON_BYTES_LIMIT"

    depth, nodes, array_items = _tree_metrics(_build_shape(case["shape"]))
    if depth > limits["depth"]:
        return "JSON_DEPTH_LIMIT"
    if nodes > limits["nodes"]:
        return "JSON_NODE_LIMIT"
    if array_items > limits["array_items"]:
        return "JSON_ARRAY_LIMIT"
    return None


@pytest.mark.parametrize(
    "case", P1_P2["fact_requirement_cases"], ids=lambda case: case["id"]
)
def test_fact_requirement_and_final_output_probes(case: dict) -> None:
    assert _fact_coverage_error(case) == case["expected_error"]


def test_provides_fact_types_must_exactly_equal_output_semantic_types() -> None:
    skill = _json(CONTRACTS / "invalid/skill_extra_provides_type.json")
    provided = set(skill["provides"]["fact_types"])
    output = {
        fact["semantic_type"] for fact in skill["output_contract"]["facts"]
    }

    assert provided != output
    assert provided - output == {"measure.synthetic_extra"}


@pytest.mark.parametrize(
    ("filename", "expected_error"),
    [
        ("invalid/package_orphan_lock_entry.json", "PACKAGE_LOCK_ORPHAN"),
        ("invalid/package_missing_lock_entry.json", "PACKAGE_LOCK_MISSING"),
    ],
)
def test_dependency_lock_is_exact_and_closed(
    filename: str, expected_error: str
) -> None:
    assert _package_lock_error(_json(CONTRACTS / filename)) == expected_error


@pytest.mark.parametrize(
    "case", P1_P2["available_catalog_cases"], ids=lambda case: case["id"]
)
def test_same_skill_id_version_with_different_available_digest_conflicts(
    case: dict,
) -> None:
    incoming = {
        (item["skill_id"], item["version"]): item["digest"]
        for item in case["incoming_skills"]
    }
    available = {
        (item["skill_id"], item["version"]): item["digest"]
        for item in case["available_catalog"]
    }
    conflicts = {
        key for key in incoming.keys() & available.keys() if incoming[key] != available[key]
    }

    assert conflicts
    assert case["expected_error"] == "SKILL_DIGEST_CONFLICT"


@pytest.mark.parametrize(
    ("filename", "expected_error"),
    [
        (
            "invalid/disagreement_duplicate_citation.json",
            "DISAGREEMENT_CITATIONS_NOT_DISTINCT",
        ),
        (
            "invalid/disagreement_wrong_fact.json",
            "DISAGREEMENT_FACT_UNKNOWN",
        ),
        (
            "invalid/disagreement_subject_mismatch.json",
            "DISAGREEMENT_SUBJECT_MISMATCH",
        ),
    ],
)
def test_disagreement_references_and_subject_are_grounded(
    filename: str, expected_error: str
) -> None:
    assert _disagreement_error(_json(CONTRACTS / filename)) == expected_error


def test_document_limit_policy_matches_architecture_numbers() -> None:
    assert LIMIT_PROBES["limits"] == {
        "document_bytes": {
            "skill": 1_048_576,
            "skill_package": 33_554_432,
            "planner_output": 262_144,
            "evidence_bundle": 67_108_864,
        },
        "depth": 32,
        "nodes": 500_000,
        "array_items": 100_000,
        "embedded_skill_canonical_bytes": 1_048_576,
    }


@pytest.mark.parametrize(
    "case", LIMIT_PROBES["cases"], ids=lambda case: case["id"]
)
def test_bounded_document_loader_probes(case: dict) -> None:
    assert _document_limit_error(case) == case["expected_error"]
