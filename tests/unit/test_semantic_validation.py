from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from chatbot1c.contracts.semantic import SemanticValidator
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "tests/fixtures/contracts/valid"


def _json(name: str) -> dict:
    return json.loads((VALID / name).read_text(encoding="utf-8"))


def _skill_codes(raw: dict) -> set[str]:
    return {
        issue.code for issue in SemanticValidator().issues(Skill.model_validate(raw))
    }


def _package_with_two_skills() -> dict:
    package = _json("skill_package.json")
    data_skill = _json("data_skill.json")
    documentation_skill = _json("documentation_skill.json")
    package["skills"] = [data_skill, documentation_skill]
    package["dependency_lock"] = [
        {
            "skill_id": skill["skill_id"],
            "version": skill["version"],
            "digest": skill["integrity"]["digest"],
        }
        for skill in package["skills"]
    ]
    return package


def test_skill_requires_both_fixture_classes() -> None:
    raw = _json("data_skill.json")
    raw["tests"][1]["case_kind"] = "positive"

    assert "POSITIVE_NEGATIVE_FIXTURES_REQUIRED" in _skill_codes(raw)


def test_query_aliases_and_column_bindings_must_match_exactly() -> None:
    raw = _json("data_skill.json")
    raw["operation"]["column_bindings"][1]["column"] = "НесуществующийAlias"

    codes = _skill_codes(raw)

    assert "QUERY_BINDING_ALIAS_MISSING" in codes
    assert "QUERY_ALIAS_BINDING_MISSING" in codes


@pytest.mark.parametrize(
    ("query", "expected_code"),
    [
        (
            "УДАЛИТЬ ИЗ РегистрНакопления.СинтетическиеОстатки",
            "QUERY_DML_FORBIDDEN",
        ),
        (
            "ВЫБРАТЬ 1 КАК Значение; УДАЛИТЬ ИЗ Справочник.Номенклатура",
            "QUERY_DML_FORBIDDEN",
        ),
        (
            "ВЫБРАТЬ Данные.Поле КАК Поле ПОМЕСТИТЬ ВременныеДанные "
            "ИЗ Справочник.Данные КАК Данные",
            "QUERY_EXECUTION_MISMATCH",
        ),
    ],
)
def test_query_lint_rejects_invalid_single_select_shape(
    query: str, expected_code: str
) -> None:
    raw = _json("data_skill.json")
    raw["operation"]["query_template"]["text"] = query

    assert expected_code in _skill_codes(raw)


def test_operation_requires_matching_source_runtime_contract() -> None:
    raw = _json("data_skill.json")
    raw["dependencies"]["runtime_contracts"][1]["contract"] = "help-index"

    assert "RUNTIME_CONTRACT_OPERATION_MISMATCH" in _skill_codes(raw)


def test_numeric_business_value_must_use_query_parameter_binding() -> None:
    raw = _json("data_skill.json")
    raw["operation"]["query_template"]["text"] += " И Данные.СинтетическийКод = 42"

    assert "CONCRETE_VALUE_IN_QUERY_TEMPLATE" in _skill_codes(raw)


def test_entity_ref_uses_exact_mcp_type_when_catalog_is_available() -> None:
    raw = _json("data_evidence_rows.json")
    raw["facts"][0]["value"]["ТипОбъекта"] = "СправочникСсылка.Склады"
    evidence = EvidenceBundle.model_validate(raw)
    skill = Skill.model_validate(_json("data_skill.json"))

    issues = SemanticValidator().issues(evidence, available_skills=(skill,))

    assert "ENTITY_REF_SEMANTIC_TYPE_MISMATCH" in {issue.code for issue in issues}


def test_package_rejects_missing_dependency() -> None:
    raw = _package_with_two_skills()
    raw["skills"][0]["dependencies"]["skills"] = [
        {
            "skill_id": "ut.synthetic.missing-skill",
            "version_range": "^1.0.0",
            "required_fact_types": [],
        }
    ]

    issues = SemanticValidator().issues(SkillPackage.model_validate(raw))

    assert "DEPENDENCY_MISSING" in {issue.code for issue in issues}
    assert any(issue.json_pointer.endswith("/skill_id") for issue in issues)


def test_package_rejects_incompatible_dependency_version() -> None:
    raw = _package_with_two_skills()
    raw["skills"][0]["dependencies"]["skills"] = [
        {
            "skill_id": raw["skills"][1]["skill_id"],
            "version_range": "^2.0.0",
            "required_fact_types": ["documentation.fragment"],
        }
    ]

    codes = {
        issue.code
        for issue in SemanticValidator().issues(SkillPackage.model_validate(raw))
    }

    assert "DEPENDENCY_VERSION_INCOMPATIBLE" in codes


def test_package_rejects_dependency_fact_contract_mismatch() -> None:
    raw = _package_with_two_skills()
    raw["skills"][0]["dependencies"]["skills"] = [
        {
            "skill_id": raw["skills"][1]["skill_id"],
            "version_range": "^1.0.0",
            "required_fact_types": ["measure.missing_synthetic_fact"],
        }
    ]

    codes = {
        issue.code
        for issue in SemanticValidator().issues(SkillPackage.model_validate(raw))
    }

    assert "DEPENDENCY_FACT_TYPE_INCOMPATIBLE" in codes


def test_package_dependency_graph_rejects_cycle() -> None:
    raw = _package_with_two_skills()
    data_skill, documentation_skill = raw["skills"]
    data_skill["dependencies"]["skills"] = [
        {
            "skill_id": documentation_skill["skill_id"],
            "version_range": "^1.0.0",
            "required_fact_types": ["documentation.fragment"],
        }
    ]
    documentation_skill["dependencies"]["skills"] = [
        {
            "skill_id": data_skill["skill_id"],
            "version_range": "^1.0.0",
            "required_fact_types": ["catalog.item"],
        }
    ]

    issues = SemanticValidator().issues(SkillPackage.model_validate(raw))

    assert "DEPENDENCY_CYCLE" in {issue.code for issue in issues}


def test_package_target_must_fit_every_embedded_skill() -> None:
    raw = _package_with_two_skills()
    raw["target"]["release"] = "11.5.28.1"

    assert "TARGET_COMPATIBILITY_MISMATCH" in {
        issue.code
        for issue in SemanticValidator().issues(SkillPackage.model_validate(raw))
    }


def test_plan_rejects_missing_step_reference() -> None:
    raw = _json("planner_execute.json")
    raw["result"]["steps"][0]["arguments"][0]["binding"] = {
        "source": "step",
        "step_id": "s99",
        "fact_id": "item.ref",
        "cardinality": "one",
    }

    issues = SemanticValidator().issues(PlannerOutput.model_validate(raw))

    assert "PLAN_STEP_REFERENCE_MISSING" in {issue.code for issue in issues}


def test_plan_dependency_graph_rejects_cycle() -> None:
    raw = _json("planner_execute.json")
    first = raw["result"]["steps"][0]
    second = copy.deepcopy(first)
    second["step_id"] = "s2"
    first["arguments"][0]["binding"] = {
        "source": "step",
        "step_id": "s2",
        "fact_id": "item.ref",
        "cardinality": "one",
    }
    second["arguments"][0]["binding"] = {
        "source": "step",
        "step_id": "s1",
        "fact_id": "item.ref",
        "cardinality": "one",
    }
    raw["result"]["steps"] = [first, second]

    issues = SemanticValidator().issues(PlannerOutput.model_validate(raw))

    assert "PLAN_DEPENDENCY_CYCLE" in {issue.code for issue in issues}
