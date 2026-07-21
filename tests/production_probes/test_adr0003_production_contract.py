from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from chatbot1c.contracts import ContractHarness, ContractValidationError
from chatbot1c.contracts.json_limits import loads_bounded_json, validate_json_structure
from chatbot1c.contracts.semantic import SemanticValidator
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "tests/fixtures/contracts/valid"
PROBES = ROOT / "tests/fixtures/adr0003"
P1_P2 = json.loads((PROBES / "p1_p2_probes.json").read_text(encoding="utf-8"))
LIMITS = json.loads(
    (PROBES / "document_limit_probes.json").read_text(encoding="utf-8")
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _issue_codes(error: ContractValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def _plan_probe_models(case: dict) -> tuple[PlannerOutput, Skill]:
    planner = _json(VALID / "planner_execute.json")
    skill = _json(VALID / "data_skill.json")
    requirement = planner["interpretation"]["required_facts"][0]
    case_id = case["id"]

    if case_id == "unmet_fact_requirement":
        requirement["semantic_type"] = "measure.synthetic_unavailable"
    elif case_id == "unknown_final_fact":
        planner["result"]["final_outputs"][0]["fact_id"] = "stock.unknown_final"
    elif case_id == "semantic_type_mismatch":
        quantity = next(
            fact
            for fact in skill["output_contract"]["facts"]
            if fact["fact_id"] == "stock.balance_quantity"
        )
        quantity["semantic_type"] = "measure.stock_available"
        skill["provides"]["fact_types"] = [
            fact["semantic_type"] for fact in skill["output_contract"]["facts"]
        ]
    elif case_id == "cardinality_mismatch":
        skill["output_contract"]["cardinality"] = "exactly_one"
        skill["output_contract"].pop("row_identity_fact_ids", None)
    elif case_id == "unit_dimension_mismatch":
        requirement["unit_dimension"] = "currency"
    elif case_id == "time_semantics_mismatch":
        requirement["time_semantics"] = "period"
    else:  # pragma: no cover - fixture manifest is closed by independent tests
        raise AssertionError(case_id)

    return PlannerOutput.model_validate(planner), Skill.model_validate(skill)


@pytest.mark.parametrize(
    "case", P1_P2["fact_requirement_cases"], ids=lambda case: case["id"]
)
def test_production_plan_validator_reports_typed_coverage_code(case: dict) -> None:
    plan, skill = _plan_probe_models(case)
    available_skills = (
        (Skill.model_validate(_json(VALID / "documentation_skill.json")),)
        if case["id"] == "unmet_fact_requirement"
        else (skill,)
    )
    codes = {
        issue.code
        for issue in SemanticValidator().issues(
            plan, available_skills=available_skills
        )
    }
    assert case["expected_error"] in codes


def test_production_package_validator_detects_available_catalog_digest_conflict() -> None:
    package_raw = _json(VALID / "skill_package.json")
    available_raw = copy.deepcopy(package_raw["skills"][0])
    available_raw["integrity"]["digest"] = "2" * 64
    package = SkillPackage.model_validate(package_raw)
    available = Skill.model_validate(available_raw)

    codes = {
        issue.code
        for issue in SemanticValidator().issues(
            package, available_skills=(available,)
        )
    }
    assert "SKILL_DIGEST_CONFLICT" in codes


@pytest.mark.parametrize(
    ("case_id", "expected_code"),
    [
        ("skill_bytes_over_limit", "JSON_BYTES_LIMIT"),
        ("depth_over_limit", "JSON_DEPTH_LIMIT"),
        ("node_count_over_limit_without_wide_array", "JSON_NODE_LIMIT"),
        ("array_items_over_limit", "JSON_ARRAY_LIMIT"),
    ],
)
def test_production_json_ingress_uses_architecture_limit_codes(
    case_id: str, expected_code: str
) -> None:
    case = next(item for item in LIMITS["cases"] if item["id"] == case_id)

    with pytest.raises(ContractValidationError) as caught:
        if case_id == "skill_bytes_over_limit":
            payload = json.dumps(
                {
                    "document_type": "skill",
                    "synthetic_padding": "x" * case["raw_bytes"],
                }
            ).encode("utf-8")
            loads_bounded_json(payload)
        elif case_id == "depth_over_limit":
            value: object = None
            for _ in range(case["shape"]["containers"]):
                value = [value]
            validate_json_structure(value)
        elif case_id == "node_count_over_limit_without_wide_array":
            value = [
                [None] * case["shape"]["items_per_branch"]
                for _ in range(case["shape"]["branches"])
            ]
            validate_json_structure(value)
        else:
            validate_json_structure([None] * case["shape"]["items"])

    assert expected_code in _issue_codes(caught.value)


def test_production_checks_embedded_skill_size_before_schema() -> None:
    package = _json(VALID / "skill_package.json")
    package["skills"][0]["synthetic_padding"] = "x" * (
        LIMITS["limits"]["embedded_skill_canonical_bytes"] + 1
    )
    harness = ContractHarness.discover(ROOT)

    with pytest.raises(ContractValidationError) as caught:
        harness.validate_document(package, verify_integrity=False)

    assert "JSON_BYTES_LIMIT" in _issue_codes(caught.value)
