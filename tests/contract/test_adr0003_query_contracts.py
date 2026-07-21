from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"
MANIFEST = json.loads(
    (FIXTURES / "fixture_manifest.json").read_text(encoding="utf-8")
)

ADR0003_ERROR_CODES = {
    "QUERY_INDEPENDENT_SELECT",
    "QUERY_TEMP_ORPHAN",
    "QUERY_TEMP_FORWARD_REFERENCE",
    "QUERY_TEMP_SELF_REFERENCE",
    "QUERY_TEMP_DUPLICATE_PRODUCER",
    "QUERY_FINAL_PUT",
    "QUERY_DML_FORBIDDEN",
    "QUERY_DDL_FORBIDDEN",
    "QUERY_BAD_SEMICOLON",
    "INVARIANT_BUSINESS_TEXT_FORBIDDEN",
    "INVARIANT_LITERAL_UNDECLARED",
    "INVARIANT_BUSINESS_ARTICLE_FORBIDDEN",
    "INVARIANT_BUSINESS_GUID_FORBIDDEN",
    "INVARIANT_BUSINESS_DOCUMENT_NUMBER_FORBIDDEN",
    "INVARIANT_BUSINESS_DATE_FORBIDDEN",
    "INVARIANT_NUMERIC_BYPASS_IN",
    "INVARIANT_NUMERIC_BYPASS_BETWEEN",
    "INVARIANT_NUMERIC_BYPASS_CASE",
    "INVARIANT_DECLARATION_MISMATCH",
    "QUERY_PARAMETER_UNDECLARED",
    "QUERY_FINAL_PROJECTION_BINDING_MISMATCH",
}


def _load(relative_path: str) -> dict:
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))


def _remove_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    quote = False
    while index < len(text):
        if quote:
            result.append(text[index])
            if text[index] == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    result.append(text[index + 1])
                    index += 2
                    continue
                quote = False
            index += 1
            continue
        if text[index] == '"':
            quote = True
            result.append(text[index])
            index += 1
            continue
        if text.startswith("//", index) or text.startswith("--", index):
            while index < len(text) and text[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if text.startswith("/*", index):
            result.extend("  ")
            index += 2
            while index < len(text) and not text.startswith("*/", index):
                result.append("\n" if text[index] == "\n" else " ")
                index += 1
            if index < len(text):
                result.extend("  ")
                index += 2
            continue
        result.append(text[index])
        index += 1
    return "".join(result)


def _split_statements(text: str) -> tuple[list[str], bool]:
    uncommented = _remove_comments(text)
    statements: list[str] = []
    current: list[str] = []
    quote = False
    index = 0
    while index < len(uncommented):
        char = uncommented[index]
        if quote:
            current.append(char)
            if char == '"':
                if index + 1 < len(uncommented) and uncommented[index + 1] == '"':
                    current.append(uncommented[index + 1])
                    index += 2
                    continue
                quote = False
            index += 1
            continue
        if char == '"':
            quote = True
            current.append(char)
        elif char == ";":
            statements.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    statements.append("".join(current).strip())

    if statements and not statements[-1]:
        statements.pop()
    bad_separator = not statements or any(not statement for statement in statements)
    return statements, bad_separator


def _mask_strings(text: str) -> str:
    masked = list(text)
    quote = False
    index = 0
    while index < len(text):
        if not quote and text[index] == '"':
            quote = True
            masked[index] = " "
        elif quote:
            masked[index] = " "
            if text[index] == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    masked[index + 1] = " "
                    index += 1
                else:
                    quote = False
        index += 1
    return "".join(masked)


def _string_literals(text: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        index += 1
        value: list[str] = []
        while index < len(text):
            if text[index] != '"':
                value.append(text[index])
                index += 1
                continue
            if index + 1 < len(text) and text[index + 1] == '"':
                value.append('"')
                index += 2
                continue
            index += 1
            break
        values.append("".join(value))
    return values


def _temp_graph_error(
    statements: list[str], code_statements: list[str], execution: dict
) -> str | None:
    kind = execution["kind"]
    if execution["statement_count"] != len(statements):
        return "QUERY_EXECUTION_MANIFEST_MISMATCH"

    producers_by_statement: dict[int, list[str]] = {}
    reads_by_statement: dict[int, set[str]] = {}
    for number, code in enumerate(code_statements, start=1):
        producers_by_statement[number] = [
            name.casefold()
            for name in re.findall(r"\bПОМЕСТИТЬ\s+(ВТ_[A-Za-zА-Яа-яЁё0-9_]+)", code)
        ]
        reads_by_statement[number] = {
            name.casefold()
            for name in re.findall(
                r"(?:\bИЗ|\bСОЕДИНЕНИЕ)\s+(ВТ_[A-Za-zА-Яа-яЁё0-9_]+)",
                code,
            )
        }

    if kind == "single_select":
        if len(statements) != 1 or producers_by_statement[1]:
            return "QUERY_SINGLE_SELECT_SHAPE"
        return None

    final_statement = execution["final_statement"]
    if final_statement != len(statements):
        return "QUERY_EXECUTION_MANIFEST_MISMATCH"
    if any(not producers_by_statement[number] for number in range(1, final_statement)):
        return "QUERY_INDEPENDENT_SELECT"
    if producers_by_statement[final_statement]:
        return "QUERY_FINAL_PUT"
    if any(len(names) != 1 for number, names in producers_by_statement.items() if number < final_statement):
        return "QUERY_EXECUTION_MANIFEST_MISMATCH"

    producer_index: dict[str, int] = {}
    for number in range(1, final_statement):
        name = producers_by_statement[number][0]
        if name in producer_index:
            return "QUERY_TEMP_DUPLICATE_PRODUCER"
        producer_index[name] = number

    for number, reads in reads_by_statement.items():
        produced_here = set(producers_by_statement[number])
        if produced_here & reads:
            return "QUERY_TEMP_SELF_REFERENCE"
        for name in reads:
            producer = producer_index.get(name)
            if producer is not None and producer >= number:
                return "QUERY_TEMP_FORWARD_REFERENCE"

    edges: dict[int, set[int]] = {number: set() for number in range(1, final_statement + 1)}
    actual_consumers: dict[str, set[int]] = {name: set() for name in producer_index}
    for consumer, reads in reads_by_statement.items():
        for name in reads:
            producer = producer_index.get(name)
            if producer is not None and producer < consumer:
                edges[producer].add(consumer)
                actual_consumers[name].add(consumer)

    def reaches_final(start: int) -> bool:
        pending = [start]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            if current == final_statement:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(edges[current])
        return False

    if any(not consumers or not reaches_final(producer_index[name]) for name, consumers in actual_consumers.items()):
        return "QUERY_TEMP_ORPHAN"

    declared = execution["temporary_tables"]
    declared_names = [item["name"].casefold() for item in declared]
    if len(declared_names) != len(set(declared_names)):
        return "QUERY_TEMP_DUPLICATE_PRODUCER"
    if set(declared_names) != set(producer_index):
        return "QUERY_EXECUTION_MANIFEST_MISMATCH"
    for item in declared:
        name = item["name"].casefold()
        if item["producer_statement"] != producer_index[name]:
            return "QUERY_EXECUTION_MANIFEST_MISMATCH"
        if set(item["consumer_statements"]) != actual_consumers[name]:
            return "QUERY_EXECUTION_MANIFEST_MISMATCH"
    return None


def _literal_error(statements: list[str], invariants: list[dict]) -> str | None:
    actual: Counter[tuple[int, str, object]] = Counter()
    undeclared_numeric_context: dict[int, set[str]] = {}

    for statement_number, statement in enumerate(statements, start=1):
        for value in _string_literals(statement):
            if value:
                if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
                    return "INVARIANT_BUSINESS_GUID_FORBIDDEN"
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
                    return "INVARIANT_BUSINESS_DATE_FORBIDDEN"
                if "ARTICLE" in value.upper():
                    return "INVARIANT_BUSINESS_ARTICLE_FORBIDDEN"
                if "DOC" in value.upper():
                    return "INVARIANT_BUSINESS_DOCUMENT_NUMBER_FORBIDDEN"
                return "INVARIANT_BUSINESS_TEXT_FORBIDDEN"
            actual[(statement_number, "empty", "")] += 1

        code = _mask_strings(statement).upper()
        for symbol in re.findall(
            r"\bЗНАЧЕНИЕ\s*\(\s*([A-ZА-ЯЁ_][A-ZА-ЯЁ0-9_]*(?:\.[A-ZА-ЯЁ_][A-ZА-ЯЁ0-9_]*)+)\s*\)",
            code,
        ):
            actual[(statement_number, "metadata", symbol.casefold())] += 1
        for token in re.findall(r"\b(?:ИСТИНА|ЛОЖЬ)\b", code):
            actual[(statement_number, "boolean", token == "ИСТИНА")] += 1
        for token in re.findall(r"\b(?:NULL|НЕОПРЕДЕЛЕНО)\b", code):
            actual[(statement_number, "null", token)] += 1
        for token in re.findall(r"(?<![A-ZА-ЯЁ0-9_&])(\d+(?:[.,]\d+)?)(?![A-ZА-ЯЁ0-9_])", code):
            normalized: int | float
            normalized = float(token.replace(",", ".")) if "." in token or "," in token else int(token)
            actual[(statement_number, "number", normalized)] += 1
            contexts = undeclared_numeric_context.setdefault(statement_number, set())
            if re.search(r"\bВ\s*\(", code):
                contexts.add("INVARIANT_NUMERIC_BYPASS_IN")
            if "МЕЖДУ" in code:
                contexts.add("INVARIANT_NUMERIC_BYPASS_BETWEEN")
            if "ВЫБОР" in code and ("ТОГДА" in code or "ИНАЧЕ" in code):
                contexts.add("INVARIANT_NUMERIC_BYPASS_CASE")

    declared: Counter[tuple[int, str, object]] = Counter()
    for invariant in invariants:
        statement = invariant["statement"]
        kind = invariant["kind"]
        if kind in {"zero_boundary", "structural_integer", "unit_scale"}:
            key = (statement, "number", invariant["value"])
        elif kind == "boolean_literal":
            key = (statement, "boolean", invariant["value"])
        elif kind == "null_literal":
            key = (statement, "null", invariant["value"])
        elif kind == "empty_literal":
            key = (statement, "empty", "")
        else:
            key = (statement, "metadata", invariant["symbol"].casefold())
        declared[key] += invariant["occurrences"]

    undeclared = actual - declared
    extra = declared - actual
    if undeclared:
        for statement_number, kind, _value in undeclared:
            if kind == "number" and undeclared_numeric_context.get(statement_number):
                return sorted(undeclared_numeric_context[statement_number])[0]
        return "INVARIANT_LITERAL_UNDECLARED"
    if extra:
        return "INVARIANT_DECLARATION_MISMATCH"
    return None


def _query_contract_error(skill: dict) -> str | None:
    operation = skill["operation"]
    template = operation["query_template"]
    statements, bad_separator = _split_statements(template["text"])
    if bad_separator:
        return "QUERY_BAD_SEMICOLON"

    code_statements = [_mask_strings(statement).upper() for statement in statements]
    code = "\n".join(code_statements)
    if re.search(r"\b(?:УДАЛИТЬ|ОБНОВИТЬ|ВСТАВИТЬ|DELETE|UPDATE|INSERT|MERGE)\b", code):
        return "QUERY_DML_FORBIDDEN"
    if re.search(r"\b(?:СОЗДАТЬ|УНИЧТОЖИТЬ|CREATE|DROP|ALTER|TRUNCATE)\b", code):
        return "QUERY_DDL_FORBIDDEN"
    if any(not re.match(r"^\s*ВЫБРАТЬ\b", statement) for statement in code_statements):
        return "QUERY_NON_SELECT_STATEMENT"

    graph_error = _temp_graph_error(
        statements, code_statements, template["execution"]
    )
    if graph_error:
        return graph_error

    declared_parameters = {
        binding["query_parameter"].casefold()
        for binding in operation["parameter_bindings"]
    }
    used_parameters = {
        value.casefold()
        for statement in code_statements
        for value in re.findall(r"&([A-ZА-ЯЁ_][A-ZА-ЯЁ0-9_]*)", statement)
    }
    if not used_parameters <= declared_parameters:
        return "QUERY_PARAMETER_UNDECLARED"

    final_code = code_statements[template["execution"]["final_statement"] - 1]
    projection = re.split(r"\bИЗ\b", final_code, maxsplit=1)[0]
    final_aliases = {
        alias.casefold()
        for alias in re.findall(r"\bКАК\s+([A-ZА-ЯЁ_][A-ZА-ЯЁ0-9_]*)", projection)
    }
    bound_columns = {
        binding["column"].casefold() for binding in operation["column_bindings"]
    }
    if not bound_columns <= final_aliases:
        return "QUERY_FINAL_PROJECTION_BINDING_MISMATCH"

    return _literal_error(statements, template["invariant_constants"])


ADR_CASES = [
    case
    for case in MANIFEST["invalid"]
    if case.get("expected_semantic_error") in ADR0003_ERROR_CODES
]


@pytest.mark.parametrize("case", ADR_CASES, ids=lambda case: case["file"])
def test_adr0003_negative_fixture_reaches_declared_independent_oracle(case: dict) -> None:
    skill = _load(case["file"])
    assert _query_contract_error(skill) == case["expected_semantic_error"]


@pytest.mark.parametrize(
    "filename",
    [
        "valid/data_skill.json",
        "valid/data_skill_single_select_typed_constants.json",
        "valid/data_skill_linked_temp_batch_sequential.json",
        "valid/data_skill_linked_temp_batch_branching.json",
        "valid/data_skill_single_select_misleading_comment.json",
    ],
)
def test_adr0003_positive_fixture_passes_independent_oracle(filename: str) -> None:
    assert _query_contract_error(_load(filename)) is None


def test_typed_constant_fixture_covers_every_closed_variant() -> None:
    skill = _load("valid/data_skill_single_select_typed_constants.json")
    kinds = {
        item["kind"]
        for item in skill["operation"]["query_template"]["invariant_constants"]
    }
    assert kinds == {
        "zero_boundary",
        "boolean_literal",
        "null_literal",
        "empty_literal",
        "metadata_constant",
        "structural_integer",
        "unit_scale",
    }


@pytest.mark.parametrize(
    ("filename", "expected_edges"),
    [
        ("valid/data_skill_linked_temp_batch_sequential.json", 2),
        ("valid/data_skill_linked_temp_batch_branching.json", 4),
    ],
)
def test_linked_batch_manifest_has_only_backward_edges_to_final(
    filename: str, expected_edges: int
) -> None:
    execution = _load(filename)["operation"]["query_template"]["execution"]
    edges = [
        (table["producer_statement"], consumer)
        for table in execution["temporary_tables"]
        for consumer in table["consumer_statements"]
    ]
    assert len(edges) == expected_edges
    assert all(producer < consumer <= execution["final_statement"] for producer, consumer in edges)
    assert any(consumer == execution["final_statement"] for _, consumer in edges)


def test_misleading_tokens_in_comments_and_strings_are_not_control_tokens() -> None:
    comment_case = _load("valid/data_skill_single_select_misleading_comment.json")
    string_case = _load("invalid/data_skill_query_misleading_string.json")

    assert _query_contract_error(comment_case) is None
    assert _query_contract_error(string_case) == "INVARIANT_BUSINESS_TEXT_FORBIDDEN"


def test_parameter_bindings_are_package_scoped_and_columns_are_final_only() -> None:
    valid = _load("valid/data_skill_linked_temp_batch_sequential.json")
    bad_parameter = _load(
        "invalid/data_skill_query_undeclared_parameter_later_statement.json"
    )
    bad_projection = _load(
        "invalid/data_skill_query_final_projection_binding_mismatch.json"
    )

    assert _query_contract_error(valid) is None
    assert _query_contract_error(bad_parameter) == "QUERY_PARAMETER_UNDECLARED"
    assert (
        _query_contract_error(bad_projection)
        == "QUERY_FINAL_PROJECTION_BINDING_MISMATCH"
    )
