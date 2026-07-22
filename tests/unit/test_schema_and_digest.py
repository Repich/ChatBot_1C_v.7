from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from chatbot1c.contracts import (
    ContractValidationError,
    SchemaRepository,
    canonicalize,
    compute_digest,
    generate_integrity,
    verify_digest,
)
from chatbot1c.contracts.schema import json_pointer
from chatbot1c.domain.evidence import EvidenceBundle

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"


def _json(relative_path: str) -> dict:
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))


def test_repository_loads_all_root_draft_2020_12_schemas() -> None:
    repository = SchemaRepository.discover(ROOT)

    assert set(repository.names) == {
        "evidence.schema.json",
        "planner-output.schema.json",
        "skill-package.schema.json",
        "skill.schema.json",
    }
    assert all(
        repository.schema(name)["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
        for name in repository.names
    )


def test_evidence_schema_versions_new_fields_without_rewriting_legacy() -> None:
    repository = SchemaRepository.discover(ROOT)
    legacy = _json("valid/data_evidence_empty.json")
    assert legacy["schema_version"] == "1.0.0"
    assert "collection_scope" not in legacy["steps"][0]
    assert "required" not in legacy["coverage"]["requirements"][0]
    repository.validate(legacy, "evidence.schema.json")

    current = copy.deepcopy(legacy)
    current["schema_version"] = "1.1.0"
    issues = repository.issues(current, "evidence.schema.json")
    assert {issue.json_pointer for issue in issues} == {
        "/coverage/requirements/0/required",
        "/steps/0/collection_scope",
    }

    current["steps"][0]["collection_scope"] = "complete_set"
    current["coverage"]["requirements"][0]["required"] = True
    repository.validate(current, "evidence.schema.json")


def test_evidence_enum_fact_is_available_only_in_v11() -> None:
    repository = SchemaRepository.discover(ROOT)
    legacy = _json("valid/data_evidence_rows.json")
    enum_fact = next(
        fact for fact in legacy["facts"] if fact["fact_id"] == "time.moment"
    )
    enum_fact["value_type"] = "enum"

    assert any(
        issue.json_pointer == "/facts/2"
        for issue in repository.issues(legacy, "evidence.schema.json")
    )
    with pytest.raises(ValueError):
        EvidenceBundle.model_validate(legacy)

    current = copy.deepcopy(legacy)
    current["schema_version"] = "1.1.0"
    for step in current["steps"]:
        step["collection_scope"] = "complete_set"
    for requirement in current["coverage"]["requirements"]:
        requirement["required"] = True
    repository.validate(current, "evidence.schema.json")
    EvidenceBundle.model_validate(current)


def test_json_pointer_escapes_reference_tokens() -> None:
    assert json_pointer(["a/b", "~value", 0]) == "/a~1b/~0value/0"


def test_rfc8785_canonicalization_sorts_keys_and_normalizes_numbers() -> None:
    assert canonicalize({"b": 1.0, "a": "text"}) == b'{"a":"text","b":1}'


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
def test_digest_matches_all_accepted_digest_examples(relative_path: str) -> None:
    document = _json(relative_path)
    assert compute_digest(document) == document["integrity"]["digest"]
    assert verify_digest(document) == document["integrity"]["digest"]


def test_digest_generation_replaces_old_integrity_without_mutating_input() -> None:
    source = _json("valid/data_skill.json")
    original = copy.deepcopy(source)
    source["integrity"]["digest"] = "0" * 64

    generated = generate_integrity(source)

    assert source["integrity"]["digest"] == "0" * 64
    assert original["integrity"]["digest"] == generated["integrity"]["digest"]
    assert verify_digest(generated) == generated["integrity"]["digest"]


def test_digest_verification_rejects_tampering_at_integrity_pointer() -> None:
    document = _json("valid/data_skill.json")
    document["display"]["name_ru"] = "Поврежденное имя навыка"

    with pytest.raises(ContractValidationError) as caught:
        verify_digest(document)

    assert caught.value.issues[0].code == "DIGEST_MISMATCH"
    assert caught.value.issues[0].json_pointer == "/integrity/digest"


def test_package_harness_reports_embedded_skill_digest_pointer() -> None:
    from chatbot1c.contracts import ContractHarness

    document = _json("valid/skill_package.json")
    document["skills"][0]["display"]["name_ru"] = "Поврежденный embedded skill"
    document = generate_integrity(document)

    with pytest.raises(ContractValidationError) as caught:
        ContractHarness.discover(ROOT).validate_document(document)

    assert any(
        issue.code == "DIGEST_MISMATCH"
        and issue.json_pointer == "/skills/0/integrity/digest"
        for issue in caught.value.issues
    )
