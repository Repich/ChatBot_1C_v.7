from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "tests/fixtures"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_property_names(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in node.values():
            names.update(_all_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.update(_all_property_names(value))
    return names


def _unwrap_mcp(message: dict) -> dict:
    if "structuredContent" in message:
        envelope = message["structuredContent"]
        if not isinstance(envelope, dict):
            raise ValueError("MCP_ENVELOPE_INVALID")
    elif "content" in message:
        text_blocks = [block for block in message["content"] if block.get("type") == "text"]
        if len(text_blocks) != 1:
            raise ValueError("MCP_ENVELOPE_INVALID")
        try:
            envelope = json.loads(text_blocks[0]["text"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("MCP_ENVELOPE_INVALID") from error
    else:
        raise ValueError("MCP_ENVELOPE_INVALID")
    if not isinstance(envelope.get("success"), bool):
        raise ValueError("MCP_ENVELOPE_INVALID")
    return envelope


def _classify_envelope(envelope: dict, aggregate_field: str | None = None) -> str:
    if envelope["success"] is False:
        return "query_error"
    rows = envelope.get("data")
    if not isinstance(rows, list):
        raise ValueError("MCP_ENVELOPE_INVALID")
    if not rows:
        return "success_empty"
    if aggregate_field and len(rows) == 1 and rows[0].get(aggregate_field) == 0:
        return "zero_aggregate"
    return "success_with_rows"


def test_planner_schema_has_no_query_code_or_mcp_injection_fields() -> None:
    planner_schema = _json(SCHEMAS / "planner-output.schema.json")
    forbidden = {
        "query", "query_text", "code", "executable_code", "mcp_tool",
        "mcp_arguments", "table", "column", "metadata_name",
    }
    assert _all_property_names(planner_schema).isdisjoint(forbidden)


def test_skill_schema_closes_operation_kinds_and_read_only_tooling() -> None:
    schema = _json(SCHEMAS / "skill.schema.json")
    operation_refs = {
        item["$ref"] for item in schema["properties"]["operation"]["oneOf"]
    }
    data_operation = schema["$defs"]["dataQueryOperation"]
    docs_operation = schema["$defs"]["documentationRetrievalOperation"]

    assert operation_refs == {
        "#/$defs/dataQueryOperation",
        "#/$defs/documentationRetrievalOperation",
    }
    assert data_operation["properties"]["tool"]["const"] == "execute_query"
    assert data_operation["properties"]["read_only"]["const"] is True
    assert data_operation["properties"]["query_template"]["properties"]["include_schema"]["const"] is True
    assert docs_operation["properties"]["filters"]["properties"]["source_kind"]["const"] == "built_in_help"


def test_package_uses_relative_skill_schema_reference() -> None:
    package_schema = _json(SCHEMAS / "skill-package.schema.json")
    assert package_schema["properties"]["skills"]["items"]["$ref"] == "skill.schema.json"


def test_evidence_outcomes_keep_empty_zero_and_errors_distinct() -> None:
    evidence_schema = _json(SCHEMAS / "evidence.schema.json")
    outcomes = evidence_schema["$defs"]["outcome"]["enum"]
    required = {
        "success_with_rows", "success_empty", "zero_aggregate", "partial",
        "query_error", "contract_error", "mcp_unavailable", "llm_unavailable",
    }
    assert required <= set(outcomes)
    assert len(outcomes) == len(set(outcomes))


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("success_rows_with_schema.json", "success_with_rows"),
        ("success_empty.json", "success_empty"),
        ("success_false.json", "query_error"),
    ],
)
def test_documented_mcp_envelopes_have_distinct_outcomes(filename: str, expected: str) -> None:
    envelope = _json(FIXTURES / "mcp" / filename)
    assert _classify_envelope(envelope) == expected


def test_zero_aggregate_is_one_row_and_not_empty() -> None:
    envelope = _json(FIXTURES / "mcp/zero_aggregate.json")
    assert envelope["count"] == 1
    assert _classify_envelope(envelope, "СинтетическийИтог") == "zero_aggregate"


@pytest.mark.parametrize(
    "filename",
    ["structured_content_wrapper.json", "text_content_wrapper.json"],
)
def test_only_documented_mcp_wrappers_are_accepted(filename: str) -> None:
    envelope = _unwrap_mcp(_json(FIXTURES / "mcp" / filename))
    assert envelope["success"] is True
    assert envelope["count"] == 1


@pytest.mark.parametrize(
    "filename",
    [
        "malformed_non_json_text_wrapper.json",
        "ambiguous_multiple_text_blocks.json",
        "unexpected_nested_wrapper.json",
    ],
)
def test_malformed_or_ambiguous_mcp_wrappers_are_rejected(filename: str) -> None:
    with pytest.raises(ValueError, match="MCP_ENVELOPE_INVALID"):
        _unwrap_mcp(_json(FIXTURES / "mcp" / filename))


def test_deepseek_valid_malformed_and_schema_invalid_responses_are_separate() -> None:
    planner_schema = _json(SCHEMAS / "planner-output.schema.json")
    validator = Draft202012Validator(planner_schema, format_checker=FormatChecker())

    valid_content = _json(FIXTURES / "deepseek/valid_planner_response.json")["choices"][0]["message"]["content"]
    valid_plan = json.loads(valid_content)
    assert not list(validator.iter_errors(valid_plan))

    malformed_content = _json(FIXTURES / "deepseek/malformed_json_response.json")["choices"][0]["message"]["content"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed_content)

    invalid_content = _json(FIXTURES / "deepseek/schema_invalid_planner_response.json")["choices"][0]["message"]["content"]
    invalid_plan = json.loads(invalid_content)
    assert list(validator.iter_errors(invalid_plan))


def test_documentation_disagreement_has_two_distinct_grounded_positions() -> None:
    evidence = _json(
        FIXTURES / "contracts/valid/documentation_evidence_disagreement.json"
    )
    disagreement = evidence["documentation_disagreements"][0]
    citation_ids = {citation["citation_id"] for citation in evidence["citations"]}
    position_citations = {
        citation_id
        for position in disagreement["positions"]
        for citation_id in position["citation_ids"]
    }

    assert len(disagreement["positions"]) >= 2
    assert len(position_citations) >= 2
    assert position_citations <= citation_ids
    assert all(citation["source_kind"] == "built_in_help" for citation in evidence["citations"])


def test_fixture_tree_contains_no_absolute_local_path_or_secret() -> None:
    forbidden_patterns = [
        re.compile(r"/Users/"),
        re.compile(r"[A-Za-z]:\\\\"),
        re.compile(r"(?i)authorization\s*:\s*bearer"),
        re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[=:]\s*[^\s]{8,}"),
        re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    ]
    forbidden_demo_values = {"V100123588", "Альтаир", "Торговый дом"}

    for path in FIXTURES.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not any(pattern.search(text) for pattern in forbidden_patterns), path
        assert not any(value in text for value in forbidden_demo_values), path


def test_semantic_invalid_manifest_labels_every_schema_valid_negative() -> None:
    manifest = _json(FIXTURES / "contracts/fixture_manifest.json")
    semantic_cases = {
        item["expected_semantic_error"]
        for item in manifest["invalid"]
        if "expected_semantic_error" in item
    }
    assert semantic_cases == {
        "UNRESOLVED_QUERY_PLACEHOLDER",
        "REQUIRED_OUTPUT_BINDING_MISSING",
        "ENTITY_REF_SEMANTIC_TYPE_MISMATCH",
        "SUFFICIENT_COVERAGE_HAS_MISSING_REQUIREMENTS",
        "ZERO_AGGREGATE_CLASSIFIED_AS_EMPTY",
        "CONCRETE_VALUE_IN_QUERY_TEMPLATE",
    }


def test_semantic_invalid_fixtures_exhibit_the_declared_single_boundary() -> None:
    invalid = FIXTURES / "contracts/invalid"

    placeholder = _json(invalid / "unresolved_query_placeholder.json")
    assert "<verified>" in placeholder["operation"]["query_template"]["text"]

    missing_binding = _json(invalid / "missing_required_output_binding.json")
    required_facts = {
        fact["fact_id"]
        for fact in missing_binding["output_contract"]["facts"]
        if fact["required"]
    }
    bound_facts = {
        binding["fact_id"] for binding in missing_binding["operation"]["column_bindings"]
    }
    assert required_facts - bound_facts == {"stock.balance_quantity"}

    forged_ref = _json(invalid / "forged_entity_ref.json")
    entity_fact = next(fact for fact in forged_ref["facts"] if fact["value_type"] == "entity_ref")
    assert entity_fact["semantic_type"] == "party.customer"
    assert entity_fact["value"]["ТипОбъекта"].startswith("ДокументСсылка.")

    insufficient = _json(invalid / "sufficient_with_missing_facts.json")
    assert insufficient["coverage"]["sufficient"] is True
    assert any(
        requirement["status"] == "missing"
        for requirement in insufficient["coverage"]["requirements"]
    )

    zero_as_empty = _json(invalid / "zero_confused_with_empty.json")
    assert zero_as_empty["outcome"] == "success_empty"
    assert zero_as_empty["steps"][0]["row_count"] == 1
    assert any(fact["value"] == 0 for fact in zero_as_empty["facts"])

    concrete_value = _json(invalid / "prohibited_concrete_value.json")
    assert "SYNTHETIC-CONCRETE-VALUE-001" in concrete_value["operation"]["query_template"]["text"]


def test_acceptance_oracle_manifest_covers_every_data_boundary_scenario() -> None:
    corpus = yaml.safe_load(
        (ROOT / "tests/corpus/user_questions.yaml").read_text(encoding="utf-8")
    )
    manifest = yaml.safe_load(
        (ROOT / "tests/oracles/acceptance_observable_state.yaml").read_text(
            encoding="utf-8"
        )
    )
    expected_ids = {
        scenario["id"]
        for scenario in corpus["scenarios"]
        if scenario["type"] in {"data", "follow_up"}
    } | {"Q102", "Q103"}

    assert set(manifest["scenarios"]) == expected_ids
    assert manifest["status"] == "blocked_external"
    assert manifest["control_query_independence"]["reuse_shipped_skill_query_templates"] == "forbidden"
    assert manifest["control_query_independence"]["copy_or_parameterize_shipped_queries"] == "forbidden"
    assert all(value is None for value in manifest["marker"].values())
    for scenario_id, oracle in manifest["scenarios"].items():
        assert oracle["corpus_ref"].endswith(f"#{scenario_id}")
        assert oracle["expected_values"] is None
        assert oracle["baseline_status"] == "blocked_external"
        assert "semantic" in oracle["oracle_methods"]
