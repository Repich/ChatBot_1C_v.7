from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "tests/fixtures/contracts"
MANIFEST_PATH = FIXTURE_DIR / "fixture_manifest.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS = {path.name: _load_json(path) for path in SCHEMA_DIR.glob("*.json")}
MANIFEST = _load_json(MANIFEST_PATH)


def _registry() -> Registry:
    registry = Registry()
    for schema in SCHEMAS.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


REGISTRY = _registry()


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        SCHEMAS[schema_name],
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )


def _flatten_errors(errors: list[ValidationError]) -> list[ValidationError]:
    flattened: list[ValidationError] = []
    pending = list(errors)
    while pending:
        error = pending.pop()
        flattened.append(error)
        pending.extend(error.context)
    return flattened


def _case_id(case: dict) -> str:
    return case["file"]


@pytest.mark.parametrize("case", MANIFEST["valid"], ids=_case_id)
def test_every_valid_contract_fixture_matches_its_schema(case: dict) -> None:
    instance = _load_json(FIXTURE_DIR / case["file"])
    errors = list(_validator(case["schema"]).iter_errors(instance))
    assert not errors, "\n".join(error.message for error in errors)


@pytest.mark.parametrize(
    "case",
    [item for item in MANIFEST["invalid"] if "expected_schema_error" in item],
    ids=_case_id,
)
def test_every_schema_invalid_fixture_is_rejected_for_declared_reason(case: dict) -> None:
    instance = _load_json(FIXTURE_DIR / case["file"])
    errors = list(_validator(case["schema"]).iter_errors(instance))
    flattened = _flatten_errors(errors)

    assert errors, f"{case['file']} unexpectedly passed JSON Schema"
    assert case["expected_schema_error"] in {error.validator for error in flattened}


@pytest.mark.parametrize(
    "case",
    [item for item in MANIFEST["invalid"] if "expected_semantic_error" in item],
    ids=_case_id,
)
def test_semantic_invalid_fixtures_are_schema_valid_and_explicitly_labeled(case: dict) -> None:
    instance = _load_json(FIXTURE_DIR / case["file"])
    errors = list(_validator(case["schema"]).iter_errors(instance))

    assert not errors, "\n".join(error.message for error in errors)
    assert case["expected_semantic_error"].strip()


def test_manifest_accounts_for_every_contract_fixture() -> None:
    declared = {item["file"] for group in MANIFEST.values() for item in group}
    actual = {
        str(path.relative_to(FIXTURE_DIR))
        for directory in (FIXTURE_DIR / "valid", FIXTURE_DIR / "invalid")
        for path in directory.glob("*.json")
    }

    assert declared == actual
    for case in MANIFEST["invalid"]:
        expectations = {
            key for key in ("expected_schema_error", "expected_semantic_error") if key in case
        }
        assert len(expectations) == 1, case["file"]


def test_all_contract_schemas_are_valid_draft_2020_12() -> None:
    for schema in SCHEMAS.values():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_skill_package_relative_ref_validates_embedded_skill() -> None:
    package = _load_json(FIXTURE_DIR / "valid/skill_package.json")
    broken_package = copy.deepcopy(package)
    broken_package["skills"][0]["schema_only_injection"] = True

    errors = list(_validator("skill-package.schema.json").iter_errors(broken_package))
    flattened = _flatten_errors(errors)

    assert errors
    assert "additionalProperties" in {error.validator for error in flattened}
    assert any(list(error.absolute_path)[:2] == ["skills", 0] for error in flattened)


@pytest.mark.parametrize(
    "relative_path",
    [
        "valid/data_skill.json",
        "valid/documentation_skill.json",
        "valid/skill_package.json",
        "invalid/unresolved_query_placeholder.json",
        "invalid/missing_required_output_binding.json",
        "invalid/prohibited_concrete_value.json",
    ],
)
def test_schema_valid_skill_documents_have_correct_rfc8785_digest(
    relative_path: str,
) -> None:
    document = _load_json(FIXTURE_DIR / relative_path)
    expected = document.pop("integrity")["digest"]
    actual = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
    assert actual == expected
