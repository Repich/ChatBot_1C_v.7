from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatbot1c.contracts.query import (
    QueryParseError,
    inspect_query_contract,
    parse_query,
)
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "tests/fixtures/contracts/valid"


def _template():
    raw = json.loads((VALID / "data_skill.json").read_text(encoding="utf-8"))
    return Skill.model_validate(raw).operation.query_template


def test_tokenizer_does_not_split_or_execute_tokens_in_strings_and_comments() -> None:
    parsed = parse_query(
        'ВЫБРАТЬ "УДАЛИТЬ; // не комментарий" КАК Текст '
        "/* ; УНИЧТОЖИТЬ */ ИЗ Справочник.СинтетическиеДанные;"
    )

    assert len(parsed.statements) == 1
    assert parsed.trailing_semicolon is True
    assert parsed.final_projection_aliases == ("Текст",)


def test_parser_rejects_more_than_sixteen_top_level_statements() -> None:
    text = ";".join("ВЫБРАТЬ Поле КАК Поле" for _ in range(17))

    with pytest.raises(QueryParseError) as caught:
        parse_query(text)

    assert caught.value.code == "QUERY_STATEMENT_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("УДАЛИТЬ ИЗ Справочник.СинтетическиеДанные", "QUERY_DML_FORBIDDEN"),
        ("СОЗДАТЬ ТАБЛИЦУ ВТ_Синтетическая", "QUERY_DDL_FORBIDDEN"),
        ("СООБЩИТЬ(\"Синтетика\")", "QUERY_BSL_FORBIDDEN"),
    ],
)
def test_parser_classifies_forbidden_statement_families(
    text: str, expected_code: str
) -> None:
    template = _template().model_copy(update={"text": text})

    result = inspect_query_contract(template)

    assert expected_code in {problem.code for problem in result.problems}
