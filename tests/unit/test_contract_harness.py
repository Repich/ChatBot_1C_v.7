from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatbot1c.contracts import ContractHarness, ContractValidationError
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"
MANIFEST = json.loads((FIXTURES / "fixture_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def harness() -> ContractHarness:
    return ContractHarness.discover(ROOT)


@pytest.fixture(scope="module")
def data_skill_catalog(harness: ContractHarness) -> tuple[Skill, ...]:
    document = harness.validate_path(FIXTURES / "valid/data_skill.json")
    assert isinstance(document, Skill)
    return (document,)


@pytest.mark.parametrize("case", MANIFEST["valid"], ids=lambda case: case["file"])
def test_production_harness_accepts_every_valid_fixture(
    harness: ContractHarness,
    data_skill_catalog: tuple[Skill, ...],
    case: dict[str, str],
) -> None:
    available_skills = (
        data_skill_catalog if case["file"] == "valid/planner_execute.json" else ()
    )
    document = harness.validate_path(
        FIXTURES / case["file"], available_skills=available_skills
    )
    assert document.document_type


@pytest.mark.parametrize("case", MANIFEST["invalid"], ids=lambda case: case["file"])
def test_production_harness_rejects_every_invalid_fixture_with_pointer(
    harness: ContractHarness, case: dict[str, str]
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        harness.validate_path(FIXTURES / case["file"])

    issues = caught.value.issues
    assert issues
    assert all(
        issue.json_pointer == "" or issue.json_pointer.startswith("/")
        for issue in issues
    )
    assert all(issue.message_ru for issue in issues)
    if expected := case.get("expected_semantic_error"):
        assert expected in {issue.code for issue in issues}
    else:
        assert case["expected_schema_error"] in {issue.keyword for issue in issues}


def test_injection_error_reports_only_relevant_one_of_branch(
    harness: ContractHarness,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        harness.validate_path(FIXTURES / "invalid/planner_query_injection.json")

    assert [(issue.keyword, issue.json_pointer) for issue in caught.value.issues] == [
        ("additionalProperties", "/result/steps/0/query")
    ]


def test_relative_package_reference_reports_nested_extra_field(
    harness: ContractHarness,
) -> None:
    package = harness.schemas.load_json(FIXTURES / "valid/skill_package.json")
    package["skills"][0]["schema_only_injection"] = True

    issues = harness.schemas.issues(package, "skill-package.schema.json")

    assert any(
        issue.json_pointer == "/skills/0/schema_only_injection"
        and issue.keyword == "additionalProperties"
        for issue in issues
    )


def test_invalid_json_is_reported_at_document_root(
    harness: ContractHarness, tmp_path: Path
) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"document_type":', encoding="utf-8")

    with pytest.raises(ContractValidationError) as caught:
        harness.validate_path(path)

    assert caught.value.issues[0].code == "JSON_PARSE_ERROR"
    assert caught.value.issues[0].json_pointer == ""


def test_execute_plan_requires_a_pinned_catalog(harness: ContractHarness) -> None:
    with pytest.raises(ContractValidationError) as caught:
        harness.validate_path(FIXTURES / "valid/planner_execute.json")

    codes = {issue.code for issue in caught.value.issues}
    assert "PLAN_SKILL_MISSING" in codes
    assert "PLAN_FACT_REQUIREMENT_UNMET" in codes
