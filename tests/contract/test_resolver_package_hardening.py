from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.errors import ContractIssue, ContractValidationError
from chatbot1c.contracts.harness import ContractHarness

ROOT = Path(__file__).resolve().parents[2]
BASE_SKILL = (
    ROOT / "skills/ut-11.5.27.56/ut115.ref.warehouse.resolve.skill.json"
)


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


def _package(*skills: dict[str, Any]) -> dict[str, Any]:
    package: dict[str, Any] = {
        "schema_version": "1.1.0",
        "document_type": "skill_package",
        "package_id": "test.resolver-hardening",
        "version": "1.1.0",
        "display": {
            "name_ru": "Проверка resolver hardening",
            "description_ru": (
                "Проверяет generic ограничения resolver и package contracts."
            ),
        },
        "target": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "configuration_name": "1С:Управление торговлей (базовая), редакция 11",
            "release": "11.5.27.56",
            "compatibility_mode": "8.3.27",
        },
        "skills": list(skills),
        "dependency_lock": [
            {
                "skill_id": skill["skill_id"],
                "version": skill["version"],
                "digest": skill["integrity"]["digest"],
            }
            for skill in skills
        ],
        "provenance": {
            "author": "contract test",
            "created_at": "2026-07-22T00:00:00Z",
            "release_note_ru": "Проверка generic hardening resolver package.",
            "source_references": [
                {
                    "kind": "test_evidence",
                    "uri": "test-evidence://slice3/resolver-package-hardening",
                }
            ],
        },
    }
    return generate_integrity(package)


def _rejected_issues(document: dict[str, Any]) -> tuple[ContractIssue, ...]:
    with pytest.raises(ContractValidationError) as rejected:
        ContractHarness.discover(ROOT).validate_document(document)
    return rejected.value.issues


def test_package_rejects_duplicate_skill_id_across_versions() -> None:
    first = _resolver_v11()
    second = copy.deepcopy(first)
    second["version"] = "1.2.0"
    second = generate_integrity(second)

    issues = _rejected_issues(_package(first, second))

    duplicate = next(
        issue for issue in issues if issue.code == "PACKAGE_SKILL_ID_DUPLICATE"
    )
    assert duplicate.json_pointer == "/skills/1/skill_id"
    assert "одну version каждого skill_id" in duplicate.message_ru


def test_package_preserves_exact_skill_pair_duplicate_diagnostic() -> None:
    skill = _resolver_v11()
    package = _package(skill, skill)
    package["dependency_lock"] = package["dependency_lock"][:1]
    package = generate_integrity(package)

    issues = _rejected_issues(package)

    by_code = {issue.code: issue for issue in issues}
    assert by_code["PACKAGE_SKILL_DUPLICATE"].json_pointer == "/skills/1"
    assert by_code["PACKAGE_SKILL_ID_DUPLICATE"].json_pointer == (
        "/skills/1/skill_id"
    )


def test_resolver_accepts_required_non_null_scalar_label_fact() -> None:
    ContractHarness.discover(ROOT).validate_document(_resolver_v11())


@pytest.mark.parametrize(
    ("fact_id", "value_type"),
    [
        ("warehouse.ref", None),
        ("warehouse.name", "document_fragment"),
        ("warehouse.name", "source_citation"),
        ("warehouse.name", "period"),
    ],
)
def test_resolver_rejects_unsafe_or_structured_label_fact(
    fact_id: str, value_type: str | None
) -> None:
    document = _resolver_v11()
    document["output_contract"]["resolution"]["candidate_label_fact_ids"] = [
        fact_id
    ]
    if value_type is not None:
        fact = next(
            item
            for item in document["output_contract"]["facts"]
            if item["fact_id"] == fact_id
        )
        fact["value_type"] = value_type
    document = generate_integrity(document)

    issues = _rejected_issues(document)

    invalid = next(
        issue for issue in issues if issue.code == "RESOLVER_PROOF_FACT_INVALID"
    )
    assert invalid.json_pointer == (
        "/output_contract/resolution/candidate_label_fact_ids/0"
    )
    assert "safe scalar fact" in invalid.message_ru
