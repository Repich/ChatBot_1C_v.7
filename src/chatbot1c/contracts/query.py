"""Bounded lexer and shallow parser for immutable 1C query packages."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, TypeAlias

from chatbot1c.domain.skill import (
    BooleanInvariant,
    EmptyInvariant,
    InvariantConstant,
    LinkedTempBatchExecution,
    MetadataConstantInvariant,
    NullInvariant,
    QueryTemplate,
    StructuralIntegerInvariant,
    TemporaryTableContract,
    UnitScaleInvariant,
    ZeroBoundaryInvariant,
)

TokenKind: TypeAlias = Literal[
    "word", "number", "string", "parameter", "identifier", "symbol"
]
InvariantValue: TypeAlias = str | int | float | bool

_SELECT_WORDS = frozenset({"SELECT", "ВЫБРАТЬ"})
_INTO_WORDS = frozenset({"INTO", "ПОМЕСТИТЬ"})
_SOURCE_WORDS = frozenset({"FROM", "JOIN", "ИЗ", "СОЕДИНЕНИЕ"})
_DML_WORDS = frozenset(
    {
        "DELETE",
        "INSERT",
        "MERGE",
        "UPDATE",
        "ВСТАВИТЬ",
        "ЗАПИСАТЬ",
        "ОБНОВИТЬ",
        "УДАЛИТЬ",
    }
)
_DDL_WORDS = frozenset(
    {
        "ALTER",
        "CREATE",
        "DROP",
        "TRUNCATE",
        "ИЗМЕНИТЬ",
        "СОЗДАТЬ",
        "УНИЧТОЖИТЬ",
    }
)
_ADMIN_WORDS = frozenset(
    {
        "COMMIT",
        "EXEC",
        "EXECUTE",
        "GRANT",
        "IMPORT",
        "REVOKE",
        "ROLLBACK",
        "SET",
        "USE",
        "ВЫПОЛНИТЬ",
    }
)
_BSL_WORDS = frozenset(
    {
        "BEGIN",
        "EXPORT",
        "IF",
        "PROCEDURE",
        "ВЫЗВАТЬИСКЛЮЧЕНИЕ",
        "ВОЗВРАТ",
        "ИСКЛЮЧЕНИЕ",
        "КОНЕЦЕСЛИ",
        "КОНЕЦПОПЫТКИ",
        "КОНЕЦПРОЦЕДУРЫ",
        "КОНЕЦЦИКЛА",
        "КОНЕЦФУНКЦИИ",
        "НОВЫЙ",
        "ПЕРЕМ",
        "ПОПЫТКА",
        "ПРЕРВАТЬ",
        "ПРОДОЛЖИТЬ",
        "ПРОЦЕДУРА",
        "СООБЩИТЬ",
        "ФУНКЦИЯ",
        "ЦИКЛ",
        "ЕСЛИ",
        "ЭКСПОРТ",
    }
)
_FORBIDDEN_WORDS = _DML_WORDS | _DDL_WORDS | _ADMIN_WORDS | _BSL_WORDS
_CLAUSE_END_WORDS = frozenset(
    {
        "GROUP",
        "HAVING",
        "ORDER",
        "UNION",
        "WHERE",
        "ГДЕ",
        "ИМЕЮЩИЕ",
        "ОБЪЕДИНИТЬ",
        "СГРУППИРОВАТЬ",
        "УПОРЯДОЧИТЬ",
    }
)
_MEASURE_NAME_PARTS = (
    "AMOUNT",
    "BALANCE",
    "COUNT",
    "COST",
    "PRICE",
    "QUANTITY",
    "RATIO",
    "RATE",
    "SHARE",
    "SUM",
    "VALUE",
    "ВЕС",
    "ДОЛЯ",
    "ЗНАЧЕН",
    "КОЛИЧЕСТВ",
    "ОСТАТОК",
    "ПРОЦЕНТ",
    "СТАВК",
    "СТОИМОСТ",
    "СУММ",
    "ЦЕН",
)
_BUSINESS_IDENTITY_PARTS = (
    "ARTICLE",
    "CODE",
    "DATE",
    "DOCUMENT",
    "ID",
    "ITEM",
    "NUMBER",
    "ORGANIZATION",
    "REF",
    "WAREHOUSE",
    "АРТИКУЛ",
    "ДАТ",
    "ДОКУМЕНТ",
    "ИДЕНТИФИКАТОР",
    "КОД",
    "НОМЕНКЛАТУР",
    "НОМЕР",
    "ОРГАНИЗАЦ",
    "ССЫЛК",
    "СКЛАД",
    "ТОВАР",
)
_GUID_LITERAL_RE = re.compile(
    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
)
_DATE_LITERAL_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T.*)?")


class QueryParseError(ValueError):
    """A deterministic lexical or structural query error."""

    def __init__(self, code: str, message: str, offset: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.offset = offset


@dataclass(frozen=True, slots=True)
class QueryToken:
    kind: TokenKind
    text: str
    value: str
    start: int
    end: int

    @property
    def upper(self) -> str:
        return self.value.upper()


@dataclass(frozen=True, slots=True)
class InvariantKey:
    kind: str
    statement: int
    role: str
    value: InvariantValue
    constant_kind: str = ""


@dataclass(frozen=True, slots=True)
class ParsedLiteral:
    key: InvariantKey | None
    offset: int
    violation_code: str | None = None
    undeclared_code: str = "INVARIANT_LITERAL_UNDECLARED"


@dataclass(frozen=True, slots=True)
class QueryStatement:
    number: int
    tokens: tuple[QueryToken, ...]
    producer_names: tuple[str, ...]
    source_names: tuple[str, ...]
    projection_aliases: tuple[str, ...]
    parameters: frozenset[str]
    literals: tuple[ParsedLiteral, ...]


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    statements: tuple[QueryStatement, ...]
    trailing_semicolon: bool

    @property
    def parameters(self) -> frozenset[str]:
        return frozenset(
            parameter
            for statement in self.statements
            for parameter in statement.parameters
        )

    @property
    def final_projection_aliases(self) -> tuple[str, ...]:
        return self.statements[-1].projection_aliases if self.statements else ()


@dataclass(frozen=True, slots=True)
class QueryProblem:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class QueryContractResult:
    parsed: ParsedQuery | None
    problems: tuple[QueryProblem, ...]


@dataclass(frozen=True, slots=True)
class KeysetQueryProof:
    order: Literal["match", "mismatch", "unproven"]
    predicate: Literal["match", "mismatch", "unproven"]


def inspect_query_contract(template: QueryTemplate) -> QueryContractResult:
    """Parse and validate a query package against its closed manifest."""

    try:
        parsed = parse_query(template.text)
    except QueryParseError as error:
        return QueryContractResult(
            parsed=None,
            problems=(
                QueryProblem(
                    code=error.code,
                    path="/text",
                    message=f"Некорректная структура query package: {error.message}",
                ),
            ),
        )

    problems = list(_read_only_problems(parsed))
    problems.extend(_execution_problems(parsed, template.execution))
    problems.extend(_invariant_problems(parsed, template.invariant_constants))
    return QueryContractResult(parsed=parsed, problems=tuple(problems))


def inspect_keyset_query_contract(
    parsed: ParsedQuery,
    *,
    projection_aliases: Sequence[str],
    directions: Sequence[Literal["asc", "desc"]],
    guard_parameter: str,
    cursor_parameters: Sequence[str],
) -> KeysetQueryProof:
    """Prove the restricted keyset ORDER BY and after-predicate token AST."""

    if (
        not parsed.statements
        or not projection_aliases
        or len(projection_aliases) != len(directions)
        or len(projection_aliases) != len(cursor_parameters)
    ):
        return KeysetQueryProof("unproven", "unproven")
    tokens = parsed.statements[-1].tokens
    projections = _keyset_projection_expressions(tokens)
    expected_expressions: list[tuple[str, ...]] = []
    for alias in projection_aliases:
        expression = projections.get(alias.casefold())
        if expression is None:
            return KeysetQueryProof("unproven", "unproven")
        expected_expressions.append(expression)

    order_terms = _keyset_order_terms(tokens, projections)
    if order_terms is None:
        order_status: Literal["match", "mismatch", "unproven"] = "unproven"
    else:
        expected_order = tuple(zip(expected_expressions, directions, strict=True))
        order_status = "match" if order_terms == expected_order else "mismatch"

    predicate_tokens = _keyset_cursor_predicate_tokens(
        tokens,
        {guard_parameter.casefold(), *(name.casefold() for name in cursor_parameters)},
    )
    if predicate_tokens is None:
        predicate_status: Literal["match", "mismatch", "unproven"] = "unproven"
    else:
        predicate_status = (
            "match"
            if _keyset_predicate_matches(
                predicate_tokens,
                expected_expressions,
                directions,
                guard_parameter,
                cursor_parameters,
            )
            else "mismatch"
        )
    return KeysetQueryProof(order_status, predicate_status)


def _keyset_projection_expressions(
    tokens: Sequence[QueryToken],
) -> dict[str, tuple[str, ...]]:
    select_index = _find_top_level_word(tokens, {"SELECT", "ВЫБРАТЬ"})
    source_index = _find_top_level_word(tokens, {"FROM", "ИЗ"})
    if select_index is None or source_index is None or source_index <= select_index:
        return {}
    projection_tokens = tuple(tokens[select_index + 1 : source_index])
    if projection_tokens and projection_tokens[0].upper in {"DISTINCT", "РАЗЛИЧНЫЕ"}:
        projection_tokens = projection_tokens[1:]
    if (
        len(projection_tokens) >= 2
        and projection_tokens[0].upper in {"TOP", "ПЕРВЫЕ"}
        and projection_tokens[1].kind == "number"
    ):
        projection_tokens = projection_tokens[2:]
    result: dict[str, tuple[str, ...]] = {}
    for item in _split_top_level_symbols(projection_tokens, ","):
        depth = 0
        alias_index: int | None = None
        for index, token in enumerate(item):
            depth = _next_depth(depth, token)
            if depth == 0 and token.kind == "word" and token.upper in {"AS", "КАК"}:
                alias_index = index
        if (
            alias_index is None
            or alias_index == 0
            or alias_index + 2 != len(item)
        ):
            return {}
        alias = item[alias_index + 1]
        if alias.kind not in {"word", "identifier"}:
            return {}
        expression = _normalize_expression(item[:alias_index])
        if not expression or alias.value.casefold() in result:
            return {}
        result[alias.value.casefold()] = expression
    return result


def _keyset_order_terms(
    tokens: Sequence[QueryToken],
    projections: dict[str, tuple[str, ...]],
) -> tuple[tuple[tuple[str, ...], Literal["asc", "desc"]], ...] | None:
    order_index = _find_top_level_sequence(
        tokens, (("ORDER", "BY"), ("УПОРЯДОЧИТЬ", "ПО"))
    )
    if order_index is None:
        return None
    start, width = order_index
    terms: list[tuple[tuple[str, ...], Literal["asc", "desc"]]] = []
    for raw_term in _split_top_level_symbols(tokens[start + width :], ","):
        term = list(_strip_outer_parentheses(raw_term))
        if not term:
            return None
        direction: Literal["asc", "desc"] = "asc"
        if term[-1].kind == "word" and term[-1].upper in {
            "ASC",
            "DESC",
            "ВОЗР",
            "УБЫВ",
        }:
            direction = "desc" if term[-1].upper in {"DESC", "УБЫВ"} else "asc"
            term.pop()
        expression = _normalize_expression(term)
        if len(expression) == 1 and expression[0] in projections:
            expression = projections[expression[0]]
        if not expression:
            return None
        terms.append((expression, direction))
    return tuple(terms)


def _keyset_cursor_predicate_tokens(
    tokens: Sequence[QueryToken], pagination_parameters: set[str]
) -> tuple[QueryToken, ...] | None:
    where_index = _find_top_level_word(tokens, {"WHERE", "ГДЕ"})
    if where_index is None:
        return None
    order_index = _find_top_level_sequence(
        tokens, (("ORDER", "BY"), ("УПОРЯДОЧИТЬ", "ПО"))
    )
    end = len(tokens) if order_index is None else order_index[0]
    where_tokens = tuple(tokens[where_index + 1 : end])
    parameter_positions = [
        index
        for index, token in enumerate(where_tokens)
        if token.kind == "parameter" and token.value.casefold() in pagination_parameters
    ]
    present = {
        where_tokens[index].value.casefold() for index in parameter_positions
    }
    if not parameter_positions or present != pagination_parameters:
        return None

    pairs: list[tuple[int, int]] = []
    stack: list[int] = []
    for index, token in enumerate(where_tokens):
        if token.kind == "symbol" and token.value == "(":
            stack.append(index)
        elif token.kind == "symbol" and token.value == ")":
            if not stack:
                return None
            pairs.append((stack.pop(), index))
    if stack:
        return None
    first = min(parameter_positions)
    last = max(parameter_positions)
    containers = [pair for pair in pairs if pair[0] < first and pair[1] > last]
    if containers:
        start, stop = min(containers, key=lambda pair: pair[1] - pair[0])
        candidate = where_tokens[start : stop + 1]
    else:
        candidate = where_tokens
    return _strip_outer_parentheses(candidate)


def _keyset_predicate_matches(
    tokens: Sequence[QueryToken],
    expressions: Sequence[tuple[str, ...]],
    directions: Sequence[Literal["asc", "desc"]],
    guard_parameter: str,
    cursor_parameters: Sequence[str],
) -> bool:
    branches = _split_top_level_words(tokens, {"OR", "ИЛИ"})
    if len(branches) != len(expressions) + 1:
        return False
    guard = _strip_outer_parentheses(branches[0])
    if not (
        len(guard) == 2
        and guard[0].kind == "word"
        and guard[0].upper in {"NOT", "НЕ"}
        and guard[1].kind == "parameter"
        and guard[1].value.casefold() == guard_parameter.casefold()
    ):
        return False

    normalized_cursor_parameters = [name.casefold() for name in cursor_parameters]
    for coordinate, raw_branch in enumerate(branches[1:]):
        comparisons = _split_top_level_words(
            _strip_outer_parentheses(raw_branch), {"AND", "И"}
        )
        if len(comparisons) != coordinate + 1:
            return False
        for prefix_index, comparison in enumerate(comparisons):
            parsed_comparison = _parse_keyset_comparison(comparison)
            if parsed_comparison is None:
                return False
            expression, operator, parameter = parsed_comparison
            expected_operator = (
                "="
                if prefix_index < coordinate
                else ">"
                if directions[coordinate] == "asc"
                else "<"
            )
            if (
                expression != expressions[prefix_index]
                or operator != expected_operator
                or parameter != normalized_cursor_parameters[prefix_index]
            ):
                return False
    return True


def _parse_keyset_comparison(
    tokens: Sequence[QueryToken],
) -> tuple[tuple[str, ...], str, str] | None:
    item = _strip_outer_parentheses(tokens)
    depth = 0
    operators: list[int] = []
    for index, token in enumerate(item):
        if depth == 0 and token.kind == "symbol" and token.value in {
            "=",
            "<",
            ">",
            "<=",
            ">=",
            "<>",
        }:
            operators.append(index)
        depth = _next_depth(depth, token)
    if len(operators) != 1:
        return None
    operator_index = operators[0]
    right = _strip_outer_parentheses(item[operator_index + 1 :])
    if len(right) != 1 or right[0].kind != "parameter":
        return None
    expression = _normalize_expression(item[:operator_index])
    if not expression:
        return None
    return expression, item[operator_index].value, right[0].value.casefold()


def _find_top_level_word(
    tokens: Sequence[QueryToken], words: set[str]
) -> int | None:
    depth = 0
    for index, token in enumerate(tokens):
        if depth == 0 and token.kind == "word" and token.upper in words:
            return index
        depth = _next_depth(depth, token)
    return None


def _find_top_level_sequence(
    tokens: Sequence[QueryToken], sequences: Sequence[tuple[str, str]]
) -> tuple[int, int] | None:
    depth = 0
    for index, token in enumerate(tokens[:-1]):
        if depth == 0 and token.kind == "word":
            for sequence in sequences:
                following = tokens[index + 1]
                if token.upper == sequence[0] and following.upper == sequence[1]:
                    return index, 2
        depth = _next_depth(depth, token)
    return None


def _split_top_level_symbols(
    tokens: Sequence[QueryToken], symbol: str
) -> tuple[tuple[QueryToken, ...], ...]:
    return _split_top_level(tokens, lambda token: token.kind == "symbol" and token.value == symbol)


def _split_top_level_words(
    tokens: Sequence[QueryToken], words: set[str]
) -> tuple[tuple[QueryToken, ...], ...]:
    return _split_top_level(
        tokens, lambda token: token.kind == "word" and token.upper in words
    )


def _split_top_level(
    tokens: Sequence[QueryToken], separator: Callable[[QueryToken], bool]
) -> tuple[tuple[QueryToken, ...], ...]:
    parts: list[tuple[QueryToken, ...]] = []
    start = 0
    depth = 0
    for index, token in enumerate(tokens):
        if depth == 0 and separator(token):
            parts.append(tuple(tokens[start:index]))
            start = index + 1
            continue
        depth = _next_depth(depth, token)
    parts.append(tuple(tokens[start:]))
    return tuple(parts)


def _strip_outer_parentheses(
    tokens: Sequence[QueryToken],
) -> tuple[QueryToken, ...]:
    result = tuple(tokens)
    while len(result) >= 2 and _parentheses_wrap_all(result):
        result = result[1:-1]
    return result


def _parentheses_wrap_all(tokens: Sequence[QueryToken]) -> bool:
    if not (
        tokens[0].kind == "symbol"
        and tokens[0].value == "("
        and tokens[-1].kind == "symbol"
        and tokens[-1].value == ")"
    ):
        return False
    depth = 0
    for index, token in enumerate(tokens):
        depth = _next_depth(depth, token)
        if depth == 0 and index != len(tokens) - 1:
            return False
        if depth < 0:
            return False
    return depth == 0


def _normalize_expression(tokens: Sequence[QueryToken]) -> tuple[str, ...]:
    return tuple(
        (
            "&" + token.value.casefold()
            if token.kind == "parameter"
            else token.value.casefold()
            if token.kind in {"word", "identifier"}
            else token.value
        )
        for token in _strip_outer_parentheses(tokens)
    )


def _next_depth(depth: int, token: QueryToken) -> int:
    if token.kind == "symbol" and token.value == "(":
        return depth + 1
    if token.kind == "symbol" and token.value == ")":
        return depth - 1
    return depth


def parse_query(text: str) -> ParsedQuery:
    """Tokenize and split a query package without interpreting business data."""

    tokens = _tokenize(text)
    statements_tokens, trailing_semicolon = _split_statements(tokens)
    if len(statements_tokens) > 16:
        raise QueryParseError(
            "QUERY_STATEMENT_LIMIT_EXCEEDED",
            "разрешено не более 16 statements",
            statements_tokens[16][0].start,
        )
    statements = tuple(
        _build_statement(index, statement_tokens)
        for index, statement_tokens in enumerate(statements_tokens, start=1)
    )
    return ParsedQuery(statements=statements, trailing_semicolon=trailing_semicolon)


def escaped_like_parameters(
    parsed: ParsedQuery, *, escape_symbol: str = "~"
) -> frozenset[str]:
    """Return direct LIKE parameters coupled to an explicit escape marker."""

    result: set[str] = set()
    for statement in parsed.statements:
        tokens = statement.tokens
        for index, token in enumerate(tokens):
            if token.kind != "word" or token.upper not in {"LIKE", "ПОДОБНО"}:
                continue
            cursor = index + 1
            opening = 0
            while cursor < len(tokens) and tokens[cursor].value == "(":
                opening += 1
                cursor += 1
            if cursor >= len(tokens) or tokens[cursor].kind != "parameter":
                continue
            parameter = tokens[cursor].value
            cursor += 1
            while opening and cursor < len(tokens) and tokens[cursor].value == ")":
                opening -= 1
                cursor += 1
            if opening or cursor + 1 >= len(tokens):
                continue
            marker = tokens[cursor]
            value = tokens[cursor + 1]
            if (
                marker.kind == "word"
                and marker.upper in {"ESCAPE", "СПЕЦСИМВОЛ"}
                and value.kind == "string"
                and value.value == escape_symbol
            ):
                result.add(parameter.casefold())
    return frozenset(result)


def _tokenize(text: str) -> tuple[QueryToken, ...]:
    tokens: list[QueryToken] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index) or text.startswith("--", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise QueryParseError(
                    "QUERY_UNTERMINATED_COMMENT",
                    "block comment не закрыт",
                    index,
                )
            index = end + 2
            continue
        if char in {'"', "'"}:
            token, index = _read_string(text, index, char)
            tokens.append(token)
            continue
        if char == "[":
            token, index = _read_quoted_identifier(text, index)
            tokens.append(token)
            continue
        if char == "&":
            start = index
            index += 1
            if index >= len(text) or not _is_word_start(text[index]):
                raise QueryParseError(
                    "QUERY_PARAMETER_INVALID",
                    "после '&' требуется имя параметра",
                    start,
                )
            index = _consume_word(text, index)
            tokens.append(
                QueryToken("parameter", text[start:index], text[start + 1 : index], start, index)
            )
            continue
        if _is_word_start(char):
            start = index
            index = _consume_word(text, index)
            value = text[start:index]
            tokens.append(QueryToken("word", value, value, start, index))
            continue
        if char.isdigit():
            token, index = _read_number(text, index)
            tokens.append(token)
            continue
        start = index
        operator = next(
            (
                candidate
                for candidate in ("<=", ">=", "<>", "!=")
                if text.startswith(candidate, index)
            ),
            char,
        )
        index += len(operator)
        tokens.append(QueryToken("symbol", operator, operator, start, index))
    return tuple(tokens)


def _read_string(text: str, start: int, quote: str) -> tuple[QueryToken, int]:
    value: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                value.append(quote)
                index += 2
                continue
            end = index + 1
            return (
                QueryToken("string", text[start:end], "".join(value), start, end),
                end,
            )
        value.append(char)
        index += 1
    raise QueryParseError(
        "QUERY_UNTERMINATED_STRING",
        "строковый литерал не закрыт",
        start,
    )


def _read_quoted_identifier(text: str, start: int) -> tuple[QueryToken, int]:
    value: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "]":
            if index + 1 < len(text) and text[index + 1] == "]":
                value.append("]")
                index += 2
                continue
            end = index + 1
            return (
                QueryToken("identifier", text[start:end], "".join(value), start, end),
                end,
            )
        value.append(char)
        index += 1
    raise QueryParseError(
        "QUERY_UNTERMINATED_IDENTIFIER",
        "quoted identifier не закрыт",
        start,
    )


def _read_number(text: str, start: int) -> tuple[QueryToken, int]:
    index = start
    while index < len(text) and text[index].isdigit():
        index += 1
    if index < len(text) and text[index] == ".":
        decimal_end = index + 1
        while decimal_end < len(text) and text[decimal_end].isdigit():
            decimal_end += 1
        if decimal_end > index + 1:
            index = decimal_end
    if index < len(text) and text[index] in {"e", "E"}:
        exponent = index + 1
        if exponent < len(text) and text[exponent] in {"+", "-"}:
            exponent += 1
        digits = exponent
        while exponent < len(text) and text[exponent].isdigit():
            exponent += 1
        if exponent > digits:
            index = exponent
    value = text[start:index]
    return QueryToken("number", value, value, start, index), index


def _is_word_start(char: str) -> bool:
    return char == "_" or char.isalpha()


def _consume_word(text: str, index: int) -> int:
    while index < len(text) and (text[index] == "_" or text[index].isalnum()):
        index += 1
    return index


def _split_statements(
    tokens: Sequence[QueryToken],
) -> tuple[tuple[tuple[QueryToken, ...], ...], bool]:
    statements: list[tuple[QueryToken, ...]] = []
    current: list[QueryToken] = []
    depth = 0
    trailing_semicolon = False
    for token in tokens:
        if token.kind == "symbol" and token.value == "(":
            depth += 1
        elif token.kind == "symbol" and token.value == ")":
            depth -= 1
            if depth < 0:
                raise QueryParseError(
                    "QUERY_PARENTHESES_UNBALANCED",
                    "лишняя закрывающая скобка",
                    token.start,
                )
        if token.kind == "symbol" and token.value == ";":
            if depth != 0:
                raise QueryParseError(
                    "QUERY_BAD_SEMICOLON",
                    "разделитель statement находится внутри выражения",
                    token.start,
                )
            if not current:
                raise QueryParseError(
                    "QUERY_BAD_SEMICOLON",
                    "обнаружен пустой statement",
                    token.start,
                )
            statements.append(tuple(current))
            current = []
            trailing_semicolon = True
            continue
        if trailing_semicolon:
            trailing_semicolon = False
        current.append(token)
    if depth != 0:
        offset = tokens[-1].end if tokens else 0
        raise QueryParseError(
            "QUERY_PARENTHESES_UNBALANCED",
            "скобки не сбалансированы",
            offset,
        )
    if current:
        statements.append(tuple(current))
    if not statements:
        raise QueryParseError("QUERY_EMPTY", "query package пуст", 0)
    return tuple(statements), trailing_semicolon


def _build_statement(number: int, tokens: tuple[QueryToken, ...]) -> QueryStatement:
    depths = _token_depths(tokens)
    producers: list[str] = []
    for index, token in enumerate(tokens):
        if token.kind == "word" and token.upper in _INTO_WORDS and depths[index] == 0:
            name = _next_identifier(tokens, index + 1)
            if name is not None:
                producers.append(name)
    sources = _source_names(tokens)
    aliases = _projection_aliases(tokens, depths)
    parameters = frozenset(
        token.value for token in tokens if token.kind == "parameter"
    )
    literals = _statement_literals(number, tokens, depths)
    return QueryStatement(
        number=number,
        tokens=tokens,
        producer_names=tuple(producers),
        source_names=tuple(sources),
        projection_aliases=tuple(aliases),
        parameters=parameters,
        literals=tuple(literals),
    )


def _token_depths(tokens: Sequence[QueryToken]) -> tuple[int, ...]:
    depths: list[int] = []
    depth = 0
    for token in tokens:
        if token.kind == "symbol" and token.value == ")":
            depth = max(0, depth - 1)
        depths.append(depth)
        if token.kind == "symbol" and token.value == "(":
            depth += 1
    return tuple(depths)


def _next_identifier(tokens: Sequence[QueryToken], start: int) -> str | None:
    if start >= len(tokens) or tokens[start].kind not in {"word", "identifier"}:
        return None
    return tokens[start].value


def _source_names(tokens: Sequence[QueryToken]) -> list[str]:
    sources: list[str] = []
    for index, token in enumerate(tokens):
        if token.kind != "word" or token.upper not in _SOURCE_WORDS:
            continue
        symbol = _dotted_symbol(tokens, index + 1)
        if symbol is not None:
            sources.append(symbol)
    return sources


def _dotted_symbol(tokens: Sequence[QueryToken], start: int) -> str | None:
    if start >= len(tokens) or tokens[start].kind not in {"word", "identifier"}:
        return None
    parts = [tokens[start].value]
    index = start + 1
    while (
        index + 1 < len(tokens)
        and tokens[index].kind == "symbol"
        and tokens[index].value == "."
        and tokens[index + 1].kind in {"word", "identifier"}
    ):
        parts.append(tokens[index + 1].value)
        index += 2
    return ".".join(parts)


def _projection_aliases(
    tokens: Sequence[QueryToken], depths: Sequence[int]
) -> list[str]:
    if not tokens or tokens[0].kind != "word" or tokens[0].upper not in _SELECT_WORDS:
        return []
    end = len(tokens)
    for index in range(1, len(tokens)):
        if (
            depths[index] == 0
            and tokens[index].kind == "word"
            and tokens[index].upper in (_SOURCE_WORDS | _INTO_WORDS)
        ):
            end = index
            break
    expressions = _split_token_ranges(tokens, depths, 1, end, ",")
    aliases: list[str] = []
    for start, stop in expressions:
        for index in range(stop - 2, start - 1, -1):
            if (
                depths[index] == 0
                and tokens[index].kind == "word"
                and tokens[index].upper in {"AS", "КАК"}
                and tokens[index + 1].kind in {"word", "identifier"}
            ):
                aliases.append(tokens[index + 1].value)
                break
    return aliases


def _split_token_ranges(
    tokens: Sequence[QueryToken],
    depths: Sequence[int],
    start: int,
    end: int,
    delimiter: str,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    for index in range(start, end):
        if (
            depths[index] == 0
            and tokens[index].kind == "symbol"
            and tokens[index].value == delimiter
        ):
            ranges.append((cursor, index))
            cursor = index + 1
    ranges.append((cursor, end))
    return ranges


def _read_only_problems(parsed: ParsedQuery) -> Iterable[QueryProblem]:
    for statement in parsed.statements:
        first = statement.tokens[0]
        if first.kind != "word" or first.upper not in _SELECT_WORDS:
            yield QueryProblem(
                "QUERY_NOT_READ_ONLY",
                "/text",
                f"Statement {statement.number} должен начинаться с ВЫБРАТЬ/SELECT.",
            )
        words = {
            token.upper
            for token in statement.tokens
            if token.kind == "word" and token.upper in _FORBIDDEN_WORDS
        }
        categories = (
            ("QUERY_DML_FORBIDDEN", words & _DML_WORDS, "DML"),
            ("QUERY_DDL_FORBIDDEN", words & _DDL_WORDS, "DDL"),
            ("QUERY_ADMIN_FORBIDDEN", words & _ADMIN_WORDS, "administrative"),
            ("QUERY_BSL_FORBIDDEN", words & _BSL_WORDS, "BSL"),
        )
        for code, forbidden, category in categories:
            if not forbidden:
                continue
            yield QueryProblem(
                code,
                "/text",
                f"Statement {statement.number} содержит запрещенные {category} tokens: "
                f"{', '.join(sorted(forbidden))}.",
            )


def _execution_problems(
    parsed: ParsedQuery,
    execution: object,
) -> Iterable[QueryProblem]:
    statement_count = len(parsed.statements)
    all_into_count = sum(
        1
        for statement in parsed.statements
        for token in statement.tokens
        if token.kind == "word" and token.upper in _INTO_WORDS
    )
    top_level_into_count = sum(
        len(statement.producer_names) for statement in parsed.statements
    )
    if all_into_count != top_level_into_count:
        yield QueryProblem(
            "QUERY_TEMP_PRODUCER_NOT_TOP_LEVEL",
            "/text",
            "ПОМЕСТИТЬ разрешен только один раз на верхнем уровне intermediate statement.",
        )

    if not isinstance(execution, LinkedTempBatchExecution):
        if statement_count != 1:
            yield QueryProblem(
                "QUERY_EXECUTION_MISMATCH",
                "/execution/statement_count",
                "single_select должен содержать ровно один statement.",
            )
        if any(statement.producer_names for statement in parsed.statements):
            yield QueryProblem(
                "QUERY_EXECUTION_MISMATCH",
                "/execution/kind",
                "single_select не может создавать temporary table.",
            )
        return

    if execution.statement_count != statement_count:
        yield QueryProblem(
            "QUERY_EXECUTION_MISMATCH",
            "/execution/statement_count",
            "statement_count не совпадает с разобранным query package.",
        )
    if execution.final_statement != statement_count:
        yield QueryProblem(
            "QUERY_EXECUTION_MISMATCH",
            "/execution/final_statement",
            "final_statement должен быть последним statement package.",
        )

    parsed_producers: dict[str, tuple[int, str]] = {}
    for statement in parsed.statements[:-1]:
        if len(statement.producer_names) != 1:
            yield QueryProblem(
                (
                    "QUERY_INDEPENDENT_SELECT"
                    if not statement.producer_names
                    else "QUERY_EXECUTION_MANIFEST_MISMATCH"
                ),
                "/text",
                f"Intermediate statement {statement.number} должен создать ровно одну temporary table.",
            )
            continue
        name = statement.producer_names[0]
        normalized = name.casefold()
        if normalized in parsed_producers:
            yield QueryProblem(
                "QUERY_TEMP_DUPLICATE_PRODUCER",
                "/text",
                f"Temporary table '{name}' создается повторно.",
            )
        else:
            parsed_producers[normalized] = (statement.number, name)
    if parsed.statements[-1].producer_names:
        yield QueryProblem(
            "QUERY_FINAL_PUT",
            "/text",
            "Final statement не может содержать ПОМЕСТИТЬ.",
        )

    manifest: dict[str, tuple[int, TemporaryTableContract]] = {}
    for index, table in enumerate(execution.temporary_tables):
        normalized = table.name.casefold()
        if normalized in manifest:
            yield QueryProblem(
                "QUERY_TEMP_DUPLICATE_PRODUCER",
                f"/execution/temporary_tables/{index}/name",
                "Имя temporary table повторяется в execution manifest.",
            )
        else:
            manifest[normalized] = (index, table)

    if set(manifest) != set(parsed_producers):
        yield QueryProblem(
            "QUERY_TEMP_MANIFEST_MISMATCH",
            "/execution/temporary_tables",
            "Execution manifest не совпадает с temporary tables в query text.",
        )

    graph: dict[int, set[int]] = defaultdict(set)
    declared_names = set(manifest)
    for normalized, (index, table) in manifest.items():
        parsed_producer = parsed_producers.get(normalized)
        if parsed_producer is None:
            continue
        producer_statement = parsed_producer[0]
        if table.producer_statement != producer_statement:
            yield QueryProblem(
                "QUERY_TEMP_PRODUCER_MISMATCH",
                f"/execution/temporary_tables/{index}/producer_statement",
                "producer_statement не совпадает с query text.",
            )
        parsed_consumers = {
            statement.number
            for statement in parsed.statements
            if any(source.casefold() == normalized for source in statement.source_names)
        }
        declared_consumers = set(table.consumer_statements)
        if parsed_consumers != declared_consumers:
            yield QueryProblem(
                "QUERY_TEMP_CONSUMERS_MISMATCH",
                f"/execution/temporary_tables/{index}/consumer_statements",
                "consumer_statements не совпадают с query text.",
            )
        for consumer in parsed_consumers:
            graph[producer_statement].add(consumer)
            if consumer == producer_statement:
                yield QueryProblem(
                    "QUERY_TEMP_SELF_REFERENCE",
                    "/text",
                    f"Temporary table '{table.name}' читает сама себя при создании.",
                )
            elif consumer < producer_statement:
                yield QueryProblem(
                    "QUERY_TEMP_FORWARD_REFERENCE",
                    "/text",
                    f"Temporary table '{table.name}' читается до создания.",
                )

    for statement in parsed.statements:
        for source in statement.source_names:
            normalized = source.casefold()
            if "." not in source and normalized not in declared_names:
                yield QueryProblem(
                    "QUERY_TEMP_UNDECLARED",
                    "/text",
                    f"Simple source '{source}' не объявлен как temporary table.",
                )

    cycle = _statement_cycle(graph)
    if cycle:
        yield QueryProblem(
            "QUERY_TEMP_CYCLE",
            "/execution/temporary_tables",
            f"Temporary table graph содержит cycle: {' -> '.join(map(str, cycle))}.",
        )
    final = statement_count
    for normalized, (_, table) in manifest.items():
        parsed_producer = parsed_producers.get(normalized)
        if parsed_producer is not None and not _reaches(
            graph, parsed_producer[0], final
        ):
            yield QueryProblem(
                "QUERY_TEMP_ORPHAN",
                "/execution/temporary_tables",
                f"Temporary table '{table.name}' не имеет пути к final statement.",
            )


def _statement_cycle(graph: dict[int, set[int]]) -> list[int]:
    visiting: set[int] = set()
    visited: set[int] = set()
    stack: list[int] = []

    def visit(node: int) -> list[int]:
        if node in visiting:
            start = stack.index(node)
            return [*stack[start:], node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for neighbor in sorted(graph.get(node, ())):
            if cycle := visit(neighbor):
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        if cycle := visit(node):
            return cycle
    return []


def _reaches(graph: dict[int, set[int]], start: int, target: int) -> bool:
    pending = deque([start])
    visited: set[int] = set()
    while pending:
        node = pending.popleft()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(graph.get(node, ()))
    return False


def _statement_literals(
    statement: int,
    tokens: Sequence[QueryToken],
    depths: Sequence[int],
) -> list[ParsedLiteral]:
    literals: list[ParsedLiteral] = []
    metadata_token_indexes: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind == "word" and token.upper in {"VALUE", "ЗНАЧЕНИЕ"}:
            parsed = _metadata_literal(statement, tokens, index, depths)
            if parsed is not None:
                literal, consumed = parsed
                literals.append(literal)
                metadata_token_indexes.update(consumed)

    for index, token in enumerate(tokens):
        if index in metadata_token_indexes:
            continue
        if token.kind == "string" and not _is_like_escape_marker(tokens, index):
            literals.append(_string_literal(statement, token, tokens, index))
        elif token.kind == "number":
            literals.append(_number_literal(statement, token, tokens, index))
        elif token.kind == "word" and token.upper in {
            "FALSE",
            "NULL",
            "TRUE",
            "UNDEFINED",
            "ИСТИНА",
            "ЛОЖЬ",
            "НЕОПРЕДЕЛЕНО",
        }:
            literals.append(_word_literal(statement, token, tokens, index))
    return literals


def _is_like_escape_marker(tokens: Sequence[QueryToken], index: int) -> bool:
    if index == 0 or tokens[index].value != "~":
        return False
    previous = tokens[index - 1]
    return previous.kind == "word" and previous.upper in {"ESCAPE", "СПЕЦСИМВОЛ"}


def _metadata_literal(
    statement: int,
    tokens: Sequence[QueryToken],
    index: int,
    depths: Sequence[int],
) -> tuple[ParsedLiteral, set[int]] | None:
    if (
        index + 3 >= len(tokens)
        or tokens[index + 1].value != "("
        or tokens[index + 2].kind not in {"word", "identifier"}
    ):
        return None
    parts = [tokens[index + 2].value]
    consumed = {index + 2}
    cursor = index + 3
    while (
        cursor + 1 < len(tokens)
        and tokens[cursor].value == "."
        and tokens[cursor + 1].kind in {"word", "identifier"}
    ):
        parts.append(tokens[cursor + 1].value)
        consumed.add(cursor + 1)
        cursor += 2
    if cursor >= len(tokens) or tokens[cursor].value != ")" or len(parts) < 2:
        return None
    symbol = ".".join(parts)
    upper_parts = [part.upper() for part in parts]
    if upper_parts[-1] in {"EMPTYREF", "ПУСТАЯССЫЛКА"}:
        constant_kind = "empty_reference"
        role = "absence_sentinel" if _is_filter_context(tokens, index) else "computed_value"
    elif upper_parts[0] in {"ENUM", "ПЕРЕЧИСЛЕНИЕ"}:
        constant_kind = "enum_member"
        role = "state_filter" if _is_filter_context(tokens, index) else "computed_value"
    else:
        constant_kind = "predefined_reference"
        role = "state_filter" if _is_filter_context(tokens, index) else "computed_value"
    if _near_word(tokens, index, {"TYPEOF", "ТИПЗНАЧЕНИЯ"}):
        role = "type_discriminator"
    key = InvariantKey(
        kind="metadata_constant",
        statement=statement,
        role=role,
        value=symbol.casefold(),
        constant_kind=constant_kind,
    )
    return ParsedLiteral(key, tokens[index].start), consumed


def _string_literal(
    statement: int,
    token: QueryToken,
    tokens: Sequence[QueryToken],
    index: int,
) -> ParsedLiteral:
    if token.value:
        return ParsedLiteral(
            None,
            token.start,
            violation_code=_business_string_code(token.value),
        )
    if _in_null_substitution(tokens, index):
        role = "null_substitution"
    elif _is_filter_context(tokens, index):
        role = "absence_filter"
    else:
        role = "computed_value"
    return ParsedLiteral(
        InvariantKey("empty_literal", statement, role, ""), token.start
    )


def _business_string_code(value: str) -> str:
    if _GUID_LITERAL_RE.fullmatch(value):
        return "INVARIANT_BUSINESS_GUID_FORBIDDEN"
    if _DATE_LITERAL_RE.fullmatch(value):
        return "INVARIANT_BUSINESS_DATE_FORBIDDEN"
    upper = value.upper()
    if "ARTICLE" in upper or "АРТИКУЛ" in upper:
        return "INVARIANT_BUSINESS_ARTICLE_FORBIDDEN"
    if "DOC" in upper or "ДОК" in upper:
        return "INVARIANT_BUSINESS_DOCUMENT_NUMBER_FORBIDDEN"
    return "INVARIANT_BUSINESS_TEXT_FORBIDDEN"


def _number_literal(
    statement: int,
    token: QueryToken,
    tokens: Sequence[QueryToken],
    index: int,
) -> ParsedLiteral:
    try:
        decimal = Decimal(token.value)
    except InvalidOperation:
        return ParsedLiteral(
            None, token.start, violation_code="INVARIANT_LITERAL_UNDECLARED"
        )
    value: int | float = (
        int(decimal) if decimal == decimal.to_integral_value() else float(decimal)
    )
    previous = tokens[index - 1] if index > 0 else None
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    enclosing = _enclosing_function(tokens, index)
    undeclared_code = _numeric_undeclared_code(tokens, index)

    if decimal == 0:
        if _in_null_substitution(tokens, index):
            role = "null_substitution"
        elif previous is not None and previous.value in {"=", "!=", "<>"}:
            role = "zero_equality"
        elif previous is not None and previous.value in {"<", "<=", ">", ">="}:
            role = "sign_boundary"
        elif (
            previous is not None and previous.value in {"+", "-"}
        ) or (following is not None and following.value in {"+", "-"}):
            role = "arithmetic_identity"
        else:
            return ParsedLiteral(None, token.start, violation_code=undeclared_code)
        business = role in {"zero_equality", "sign_boundary"} and not _measure_context(
            tokens, index
        )
        return ParsedLiteral(
            InvariantKey("zero_boundary", statement, role, 0),
            token.start,
            violation_code=(
                "CONCRETE_VALUE_IN_QUERY_TEMPLATE" if business else None
            ),
            undeclared_code=undeclared_code,
        )

    if previous is not None and previous.kind == "word":
        if previous.upper in {"TOP", "ПЕРВЫЕ"}:
            return ParsedLiteral(
                InvariantKey("structural_integer", statement, "top_limit", value),
                token.start,
                violation_code=(
                    "INVARIANT_LITERAL_UNDECLARED"
                    if not isinstance(value, int) or value <= 0
                    else None
                ),
                undeclared_code=undeclared_code,
            )
        if previous.upper in {"LIMIT", "ОГРАНИЧИТЬ"}:
            return ParsedLiteral(
                InvariantKey("structural_integer", statement, "rank_limit", value),
                token.start,
                violation_code=(
                    "INVARIANT_LITERAL_UNDECLARED"
                    if not isinstance(value, int) or value <= 0
                    else None
                ),
                undeclared_code=undeclared_code,
            )
    if enclosing in {"NUMBER", "STRING", "СТРОКА", "ЧИСЛО"}:
        return ParsedLiteral(
            InvariantKey(
                "structural_integer", statement, "query_language_arity", value
            ),
            token.start,
            violation_code=(
                "INVARIANT_LITERAL_UNDECLARED"
                if not isinstance(value, int) or value <= 0
                else None
            ),
            undeclared_code=undeclared_code,
        )
    if (previous is not None and previous.value in {"*", "/"}) or (
        following is not None and following.value in {"*", "/"}
    ):
        role = "percentage_scale" if decimal == 100 else "unit_conversion"
        return ParsedLiteral(
            InvariantKey("unit_scale", statement, role, value),
            token.start,
            undeclared_code=undeclared_code,
        )
    return ParsedLiteral(None, token.start, violation_code=undeclared_code)


def _numeric_undeclared_code(
    tokens: Sequence[QueryToken], index: int
) -> str:
    contexts: set[str] = set()
    if _enclosing_function(tokens, index) in {"IN", "В"}:
        contexts.add("INVARIANT_NUMERIC_BYPASS_IN")
    words = {
        token.upper
        for token in tokens
        if token.kind == "word"
    }
    if words & {"BETWEEN", "МЕЖДУ"}:
        contexts.add("INVARIANT_NUMERIC_BYPASS_BETWEEN")
    if words & {"CASE", "ВЫБОР"}:
        contexts.add("INVARIANT_NUMERIC_BYPASS_CASE")
    return min(contexts, default="INVARIANT_LITERAL_UNDECLARED")


def _word_literal(
    statement: int,
    token: QueryToken,
    tokens: Sequence[QueryToken],
    index: int,
) -> ParsedLiteral:
    if token.upper in {"TRUE", "FALSE", "ИСТИНА", "ЛОЖЬ"}:
        bool_value = token.upper in {"TRUE", "ИСТИНА"}
        if _in_null_substitution(tokens, index):
            role = "null_substitution"
        elif _near_word(tokens, index, {"ELSE", "THEN", "ИНАЧЕ", "ТОГДА"}):
            role = "computed_flag"
        else:
            role = "state_filter"
        return ParsedLiteral(
            InvariantKey("boolean_literal", statement, role, bool_value),
            token.start,
        )
    null_value = "NULL" if token.upper == "NULL" else "НЕОПРЕДЕЛЕНО"
    if _in_null_substitution(tokens, index):
        role = "null_substitution"
    elif _is_filter_context(tokens, index):
        role = "absence_filter"
    else:
        role = "computed_value"
    return ParsedLiteral(
        InvariantKey("null_literal", statement, role, null_value), token.start
    )


def _enclosing_function(tokens: Sequence[QueryToken], index: int) -> str | None:
    stack: list[int] = []
    for cursor, token in enumerate(tokens[:index]):
        if token.value == "(":
            stack.append(cursor)
        elif token.value == ")" and stack:
            stack.pop()
    if not stack:
        return None
    opening = stack[-1]
    if opening > 0 and tokens[opening - 1].kind == "word":
        return tokens[opening - 1].upper
    return None


def _in_null_substitution(tokens: Sequence[QueryToken], index: int) -> bool:
    return _enclosing_function(tokens, index) in {
        "COALESCE",
        "ISNULL",
        "ЕСТЬNULL",
    }


def _is_filter_context(tokens: Sequence[QueryToken], index: int) -> bool:
    words = [token.upper for token in tokens[:index] if token.kind == "word"]
    last_where = max(
        (position for position, word in enumerate(words) if word in {"WHERE", "ГДЕ"}),
        default=-1,
    )
    last_clause_end = max(
        (
            position
            for position, word in enumerate(words)
            if word in _CLAUSE_END_WORDS and word not in {"WHERE", "ГДЕ"}
        ),
        default=-1,
    )
    return last_where > last_clause_end or any(
        token.value in {"=", "!=", "<", "<=", "<>", ">", ">="}
        for token in tokens[max(0, index - 3) : index + 3]
    )


def _near_word(
    tokens: Sequence[QueryToken], index: int, expected: set[str]
) -> bool:
    return any(
        token.kind == "word" and token.upper in expected
        for token in tokens[max(0, index - 4) : index + 5]
    )


def _measure_context(tokens: Sequence[QueryToken], index: int) -> bool:
    nearby_words = [
        token.upper
        for token in tokens[max(0, index - 7) : index]
        if token.kind in {"word", "identifier"}
    ]
    if not nearby_words:
        return False
    candidate = nearby_words[-1]
    if any(part in candidate for part in _BUSINESS_IDENTITY_PARTS):
        return False
    return any(part in candidate for part in _MEASURE_NAME_PARTS)


def _invariant_problems(
    parsed: ParsedQuery,
    declarations: Sequence[InvariantConstant],
) -> Iterable[QueryProblem]:
    parsed_counts: Counter[InvariantKey] = Counter()
    undeclared_codes: dict[InvariantKey, set[str]] = defaultdict(set)
    for statement in parsed.statements:
        for literal in statement.literals:
            if literal.violation_code is not None:
                yield QueryProblem(
                    literal.violation_code,
                    "/text",
                    f"Business-instance literal в statement {statement.number} должен быть parameter binding.",
                )
                if literal.violation_code != "CONCRETE_VALUE_IN_QUERY_TEMPLATE":
                    yield QueryProblem(
                        "CONCRETE_VALUE_IN_QUERY_TEMPLATE",
                        "/text",
                        f"Concrete literal в statement {statement.number} не имеет допустимой typed declaration.",
                    )
            elif literal.key is None:
                yield QueryProblem(
                    "INVARIANT_LITERAL_UNDECLARED",
                    "/invariant_constants",
                    f"Literal в statement {statement.number} не имеет typed declaration.",
                )
            else:
                parsed_counts[literal.key] += 1
                undeclared_codes[literal.key].add(literal.undeclared_code)

    declared_counts: dict[InvariantKey, tuple[int, int]] = {}
    for index, declaration in enumerate(declarations):
        key = _declaration_key(declaration)
        if key in declared_counts:
            yield QueryProblem(
                "INVARIANT_CONSTANT_DUPLICATE",
                f"/invariant_constants/{index}",
                "Typed invariant declaration повторяется.",
            )
        declared_counts[key] = (index, declaration.occurrences)

    for key, occurrences in parsed_counts.items():
        declared = declared_counts.get(key)
        if declared is None:
            yield QueryProblem(
                min(undeclared_codes[key]),
                "/invariant_constants",
                f"Invariant literal {key.kind} statement={key.statement} role={key.role} не объявлен.",
            )
        elif declared[1] != occurrences:
            yield QueryProblem(
                "INVARIANT_DECLARATION_MISMATCH",
                f"/invariant_constants/{declared[0]}/occurrences",
                "occurrences не совпадает с количеством literals в query text.",
            )
    for key, (index, _) in declared_counts.items():
        if key not in parsed_counts:
            yield QueryProblem(
                "INVARIANT_DECLARATION_MISMATCH",
                f"/invariant_constants/{index}",
                "Typed invariant declaration не имеет exact literal в query text.",
            )


def _declaration_key(declaration: InvariantConstant) -> InvariantKey:
    if isinstance(declaration, MetadataConstantInvariant):
        return InvariantKey(
            declaration.kind,
            declaration.statement,
            declaration.role,
            declaration.symbol.casefold(),
            declaration.constant_kind,
        )
    if isinstance(
        declaration,
        (
            BooleanInvariant,
            EmptyInvariant,
            NullInvariant,
            StructuralIntegerInvariant,
            UnitScaleInvariant,
            ZeroBoundaryInvariant,
        ),
    ):
        return InvariantKey(
            declaration.kind,
            declaration.statement,
            declaration.role,
            declaration.value,
        )
    raise TypeError(f"Unsupported invariant declaration: {type(declaration)!r}")
