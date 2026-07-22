from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
BASE_SKILL = (
    ROOT
    / "skills/ut-11.5.27.56/ut115.ref.warehouse.resolve.skill.json"
)


def _legacy_v10_skill() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (ROOT / "tests/fixtures/contracts/valid/data_skill.json").read_text(
                encoding="utf-8"
            )
        ),
    )


def _enum_fact() -> dict[str, Any]:
    return {
        "fact_id": "stock.availability_state",
        "semantic_type": "attribute.stock_availability_state",
        "value_type": "enum",
        "role": "attribute",
        "required": False,
        "nullable": True,
        "title_ru": "Состояние доступности",
        "unit_contract": {"mode": "not_applicable"},
        "allowed_values": ["available", "reserved"],
    }


def _resolver_v11() -> dict[str, Any]:
    document = cast(
        dict[str, Any], json.loads(BASE_SKILL.read_text(encoding="utf-8"))
    )
    document["schema_version"] = "1.1.0"
    document["version"] = "1.1.0"
    for parameter in document["parameters"]:
        parameter["context_slot_keys"] = (
            ["selection.department"]
            if "session_context" in parameter["allowed_sources"]
            else []
        )
    output = document["output_contract"]
    output["contract_version"] = "1.1.0"
    output["resolution"] = {
        "protocol": "typed_entity_resolver_v1",
        "identity_fact_id": "warehouse.ref",
        "candidate_label_fact_ids": ["warehouse.name"],
        "role_proof_fact_ids": ["warehouse.type"],
        "default_slot_key": "selection.warehouse",
    }
    output["context_export_policy"] = [
        {
            "fact_id": "warehouse.ref",
            "slot_key": "selection.warehouse",
            "mode": "selected_only",
            "lifetime": {"mode": "session"},
            "max_members": 100,
        }
    ]
    role_fact = next(
        item for item in output["facts"] if item["fact_id"] == "warehouse.type"
    )
    role_fact["semantic_type"] = "catalog.warehouse.role_allowed"
    role_fact["value_type"] = "boolean"
    output_types = document["provides"]["fact_types"]
    output_types[output_types.index("catalog.warehouse.type")] = (
        "catalog.warehouse.role_allowed"
    )
    role_binding = next(
        item
        for item in document["operation"]["column_bindings"]
        if item["fact_id"] == "warehouse.type"
    )
    role_binding["accepted_mcp_types"] = ["Булево"]
    role_binding["converter"] = "boolean"
    return generate_integrity(document)


def _codes(document: dict[str, Any]) -> set[str]:
    with pytest.raises(ContractValidationError) as rejected:
        ContractHarness.discover(ROOT).validate_document(document)
    return {issue.code for issue in rejected.value.issues}


def test_skill_v11_resolver_is_a_strict_portable_contract() -> None:
    validated = ContractHarness.discover(ROOT).validate_document(_resolver_v11())
    assert isinstance(validated, Skill)
    assert validated.schema_version == "1.1.0"
    assert validated.output_contract.resolution is not None
    assert validated.output_contract.resolution.default_slot_key == (
        "selection.warehouse"
    )


def test_skill_v10_cannot_smuggle_slice3_fields() -> None:
    document = _resolver_v11()
    document["schema_version"] = "1.0.0"
    document["version"] = "1.0.1"
    document = generate_integrity(document)
    assert "JSON_SCHEMA_VALIDATION_ERROR" in _codes(document)


def test_skill_v10_cannot_smuggle_v11_enum_fact() -> None:
    document = _legacy_v10_skill()
    enum_fact = _enum_fact()
    document["output_contract"]["facts"].append(enum_fact)
    document["provides"]["fact_types"].append(enum_fact["semantic_type"])

    assert "JSON_SCHEMA_VALIDATION_ERROR" in _codes(generate_integrity(document))


def test_skill_v11_requires_explicit_parameter_slot_keys() -> None:
    document = _resolver_v11()
    del document["parameters"][0]["context_slot_keys"]
    document = generate_integrity(document)
    assert "JSON_SCHEMA_VALIDATION_ERROR" in _codes(document)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda value: value["output_contract"]["resolution"].update(
                {"identity_fact_id": "warehouse.name"}
            ),
            "RESOLVER_IDENTITY_FACT_INVALID",
        ),
        (
            lambda value: value["output_contract"]["context_export_policy"][0].update(
                {"fact_id": "warehouse.name"}
            ),
            "CONTEXT_EXPORT_MODE_INVALID",
        ),
    ],
)
def test_skill_v11_import_rejects_unproved_resolution(
    mutation: Callable[[dict[str, Any]], None], expected: str
) -> None:
    document = copy.deepcopy(_resolver_v11())
    mutation(document)
    assert expected in _codes(generate_integrity(document))


def test_role_proof_requires_boolean_or_singleton_enum_domain() -> None:
    document = _resolver_v11()
    role_fact = next(
        item
        for item in document["output_contract"]["facts"]
        if item["fact_id"] == "warehouse.type"
    )
    role_fact["value_type"] = "string"
    assert "RESOLVER_ROLE_PROOF_INVALID" in _codes(generate_integrity(document))


def test_skill_v11_accepts_singleton_enum_role_proof() -> None:
    document = _resolver_v11()
    role_fact = next(
        item
        for item in document["output_contract"]["facts"]
        if item["fact_id"] == "warehouse.type"
    )
    role_fact["value_type"] = "enum"
    role_fact["allowed_values"] = ["warehouse"]
    role_binding = next(
        item
        for item in document["operation"]["column_bindings"]
        if item["fact_id"] == "warehouse.type"
    )
    role_binding["accepted_mcp_types"] = ["Строка"]
    role_binding["converter"] = "string"

    validated = ContractHarness.discover(ROOT).validate_document(
        generate_integrity(document)
    )

    assert isinstance(validated, Skill)
    validated_role = next(
        fact
        for fact in validated.output_contract.facts
        if fact.fact_id == "warehouse.type"
    )
    assert validated_role.allowed_values == ("warehouse",)


def test_package_v10_rejects_v11_but_package_v11_can_carry_legacy_dependencies() -> None:
    resolver = _resolver_v11()
    legacy = _legacy_v10_skill()
    package: dict[str, Any] = {
        "schema_version": "1.0.0",
        "document_type": "skill_package",
        "package_id": "test.context-package",
        "version": "1.0.0",
        "display": {
            "name_ru": "Проверка context package",
            "description_ru": "Проверяет строгие ветви версии package contract.",
        },
        "target": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "configuration_name": "1С:Управление торговлей (базовая), редакция 11",
            "release": "11.5.27.56",
            "compatibility_mode": "8.3.27",
        },
        "skills": [resolver],
        "dependency_lock": [],
        "provenance": {
            "author": "contract test",
            "created_at": "2026-07-21T12:00:00Z",
            "release_note_ru": "Проверка ветки package 1.1.",
            "source_references": [
                {
                    "kind": "test_evidence",
                    "uri": "test-evidence://slice3/package-v11",
                }
            ],
        },
    }
    assert "JSON_SCHEMA_VALIDATION_ERROR" in _codes(generate_integrity(package))

    package["schema_version"] = "1.1.0"
    package["version"] = "1.1.0"
    package["skills"] = [resolver, legacy]
    package["dependency_lock"] = [
        {
            "skill_id": item["skill_id"],
            "version": item["version"],
            "digest": item["integrity"]["digest"],
        }
        for item in package["skills"]
    ]
    validated = ContractHarness.discover(ROOT).validate_document(
        generate_integrity(package)
    )
    assert isinstance(validated, SkillPackage)
    assert validated.schema_version == "1.1.0"
    legacy_validated = next(
        skill for skill in validated.skills if skill.schema_version == "1.0.0"
    )
    assert legacy_validated.schema_version == "1.0.0"


def test_package_v10_accepts_legacy_v10_skill() -> None:
    legacy = _legacy_v10_skill()
    package: dict[str, Any] = {
        "schema_version": "1.0.0",
        "document_type": "skill_package",
        "package_id": "test.legacy-package",
        "version": "1.0.0",
        "display": {
            "name_ru": "Проверка legacy package",
            "description_ru": "Проверяет совместимость skill с package версии 1.0.",
        },
        "target": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "configuration_name": "1С:Управление торговлей (базовая), редакция 11",
            "release": "11.5.27.56",
            "compatibility_mode": "8.3.27",
        },
        "skills": [legacy],
        "dependency_lock": [
            {
                "skill_id": legacy["skill_id"],
                "version": legacy["version"],
                "digest": legacy["integrity"]["digest"],
            }
        ],
        "provenance": {
            "author": "contract test",
            "created_at": "2026-07-21T12:00:00Z",
            "release_note_ru": "Проверка обратной совместимости package 1.0.",
            "source_references": [
                {
                    "kind": "test_evidence",
                    "uri": "test-evidence://slice3/package-v10",
                }
            ],
        },
    }

    validated = ContractHarness.discover(ROOT).validate_document(
        generate_integrity(package)
    )

    assert isinstance(validated, SkillPackage)
    assert validated.schema_version == "1.0.0"
    assert validated.skills[0].schema_version == "1.0.0"
