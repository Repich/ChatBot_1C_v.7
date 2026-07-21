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
BUILTIN = ROOT / "skills/ut-11.5.27.56"


def _json(name: str) -> dict:
    return json.loads((VALID / name).read_text(encoding="utf-8"))


def _skill_codes(raw: dict) -> set[str]:
    return {
        issue.code for issue in SemanticValidator().issues(Skill.model_validate(raw))
    }


def _warehouse_skill() -> dict:
    return json.loads(
        (BUILTIN / "ut115.ref.warehouse.resolve.skill.json").read_text(
            encoding="utf-8"
        )
    )


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


def test_evidence_sufficiency_ignores_only_explicit_optional_missing_requirement() -> None:
    raw = _json("data_evidence_rows.json")
    raw["coverage"]["requirements"].append(
        {
            "requirement_id": "r2",
            "semantic_type": "warehouse.department",
            "required": False,
            "status": "missing",
            "fact_instance_ids": [],
        }
    )

    issues = SemanticValidator().issues(EvidenceBundle.model_validate(raw))

    assert "SUFFICIENT_COVERAGE_HAS_MISSING_REQUIREMENTS" not in {
        issue.code for issue in issues
    }


def test_evidence_sufficiency_rejects_explicit_required_missing_requirement() -> None:
    raw = _json("data_evidence_rows.json")
    requirement = raw["coverage"]["requirements"][0]
    requirement.update(required=True, status="missing", fact_instance_ids=[])

    issues = SemanticValidator().issues(EvidenceBundle.model_validate(raw))

    assert "SUFFICIENT_COVERAGE_HAS_MISSING_REQUIREMENTS" in {
        issue.code for issue in issues
    }


def test_evidence_v11_success_empty_requires_stable_reason_but_v10_does_not() -> None:
    legacy = _json("data_evidence_empty.json")
    legacy_issues = SemanticValidator().issues(EvidenceBundle.model_validate(legacy))
    assert "EMPTY_REASON_REQUIRED" not in {issue.code for issue in legacy_issues}

    current = copy.deepcopy(legacy)
    current["schema_version"] = "1.1.0"
    current["steps"][0]["collection_scope"] = "complete_set"
    current["coverage"]["requirements"][0]["required"] = True
    current_issues = SemanticValidator().issues(
        EvidenceBundle.model_validate(current)
    )
    assert "EMPTY_REASON_REQUIRED" in {issue.code for issue in current_issues}

    current["empty_reason"] = "no_rows"
    resolved_issues = SemanticValidator().issues(
        EvidenceBundle.model_validate(current)
    )
    assert "EMPTY_REASON_REQUIRED" not in {issue.code for issue in resolved_issues}


def test_keyset_sort_fact_ids_must_be_unique() -> None:
    raw = _warehouse_skill()
    raw["operation"]["pagination"]["sort"][1]["fact_id"] = "warehouse.name"

    assert "PAGINATION_SORT_FACT_DUPLICATE" in _skill_codes(raw)


def test_keyset_cursor_fact_ids_must_exactly_follow_sort_order() -> None:
    raw = _warehouse_skill()
    raw["operation"]["pagination"]["cursor_bindings"].reverse()

    assert "PAGINATION_CURSOR_BIJECTION_MISMATCH" in _skill_codes(raw)


def test_keyset_query_parameter_names_are_unique_and_noncolliding() -> None:
    duplicate = _warehouse_skill()
    duplicate["operation"]["pagination"]["cursor_bindings"][1][
        "query_parameter"
    ] = "ИмяКурсора"
    assert "PAGINATION_CURSOR_PARAMETER_DUPLICATE" in _skill_codes(
        duplicate
    )

    collision = _warehouse_skill()
    collision["operation"]["pagination"]["has_cursor_query_parameter"] = "Шаблон"
    assert "PAGINATION_PARAMETER_COLLISION" in _skill_codes(collision)


def test_keyset_cursor_encoding_must_match_fact_value_type() -> None:
    raw = _warehouse_skill()
    raw["operation"]["pagination"]["cursor_bindings"][0]["encoding"] = (
        "integer"
    )

    assert "PAGINATION_CURSOR_ENCODING_MISMATCH" in _skill_codes(raw)


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("required", False, "PAGINATION_SORT_COORDINATE_INVALID"),
        ("nullable", True, "PAGINATION_SORT_COORDINATE_INVALID"),
    ],
)
def test_keyset_sort_facts_are_required_and_nonnullable(
    field: str, value: bool, expected_code: str
) -> None:
    raw = _warehouse_skill()
    fact = next(
        item
        for item in raw["output_contract"]["facts"]
        if item["fact_id"] == "warehouse.name"
    )
    fact[field] = value

    assert expected_code in _skill_codes(raw)


def test_keyset_sort_must_end_with_complete_row_identity() -> None:
    raw = _warehouse_skill()
    raw["operation"]["pagination"]["sort"].reverse()
    raw["operation"]["pagination"]["cursor_bindings"].reverse()

    assert "PAGINATION_IDENTITY_SUFFIX_MISMATCH" in _skill_codes(raw)


def test_keyset_parameters_must_appear_in_query_and_static_top_is_forbidden() -> None:
    missing = _warehouse_skill()
    missing["operation"]["query_template"]["text"] = missing["operation"][
        "query_template"
    ]["text"].replace("&ЕстьКурсор", "&НеобъявленныйКурсор")
    assert "PAGINATION_QUERY_CONTRACT_UNPROVEN" in _skill_codes(missing)

    limited = _warehouse_skill()
    template = limited["operation"]["query_template"]
    template["text"] = template["text"].replace(
        "ВЫБРАТЬ\n", "ВЫБРАТЬ ПЕРВЫЕ 20\n", 1
    )
    template["invariant_constants"].append(
        {
            "kind": "structural_integer",
            "statement": 1,
            "value": 20,
            "role": "top_limit",
            "occurrences": 1,
        }
    )
    assert "PAGINATION_STATIC_TOP_FORBIDDEN" in _skill_codes(limited)


def test_keyset_query_order_and_strict_predicate_are_proved_structurally() -> None:
    wrong_order = _warehouse_skill()
    template = wrong_order["operation"]["query_template"]
    template["text"] = template["text"].replace(
        "УПОРЯДОЧИТЬ ПО Склады.Наименование, Склады.Ссылка",
        "УПОРЯДОЧИТЬ ПО Склады.Ссылка, Склады.Наименование",
    )
    assert "PAGINATION_QUERY_ORDER_MISMATCH" in _skill_codes(wrong_order)

    non_strict = _warehouse_skill()
    template = non_strict["operation"]["query_template"]
    template["text"] = template["text"].replace(
        "Склады.Ссылка > &СсылкаКурсора",
        "Склады.Ссылка >= &СсылкаКурсора",
    )
    assert "PAGINATION_QUERY_PREDICATE_MISMATCH" in _skill_codes(non_strict)


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

    assert "SKILL_DEPENDENCY_MISSING" in {issue.code for issue in issues}
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
