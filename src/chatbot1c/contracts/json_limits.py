"""Bounded JSON ingress applied before materializing contract documents."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from chatbot1c.contracts.errors import ContractIssue, ContractValidationError

MIB = 1024 * 1024
DOCUMENT_BYTE_LIMITS = {
    "skill": 1 * MIB,
    "skill_package": 32 * MIB,
    "planner_output": 256 * 1024,
    "evidence_bundle": 64 * MIB,
}
MAX_DOCUMENT_BYTES = max(DOCUMENT_BYTE_LIMITS.values())
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 500_000
MAX_JSON_ARRAY_ITEMS = 100_000
MAX_EMBEDDED_SKILL_BYTES = 1 * MIB

_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_COMPRESSED_PREFIXES = (
    b"\x1f\x8b",
    b"BZh",
    b"PK\x03\x04",
    b"\x28\xb5\x2f\xfd",
    b"\xfd7zXZ\x00",
)


@dataclass(frozen=True, slots=True)
class JsonPreflight:
    document_type: str | None
    node_count: int
    array_item_count: int
    maximum_depth: int


def load_bounded_json(path: Path | str) -> dict[str, Any]:
    document_path = Path(path)
    try:
        with document_path.open("rb") as stream:
            payload = stream.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as error:
        raise RuntimeError(
            f"Cannot read contract document {document_path}: {error}"
        ) from error
    return loads_bounded_json(payload)


def loads_bounded_json(payload: bytes) -> dict[str, Any]:
    if any(payload.startswith(prefix) for prefix in _COMPRESSED_PREFIXES):
        _raise_limit(
            "JSON_COMPRESSED_NOT_ALLOWED",
            "",
            "Compressed JSON не принимается contract boundary.",
        )
    if len(payload) > MAX_DOCUMENT_BYTES:
        _raise_limit(
            "JSON_BYTES_LIMIT",
            "",
            f"JSON document превышает абсолютный предел {MAX_DOCUMENT_BYTES} bytes.",
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        _raise_limit(
            "JSON_ENCODING_ERROR",
            "",
            f"JSON должен быть strict UTF-8: byte {error.start}.",
        )

    preflight = _JsonScanner(text).scan()
    byte_limit = (
        DOCUMENT_BYTE_LIMITS.get(preflight.document_type)
        if preflight.document_type is not None
        else None
    )
    if byte_limit is None:
        byte_limit = min(DOCUMENT_BYTE_LIMITS.values())
    if len(payload) > byte_limit:
        _raise_limit(
            "JSON_BYTES_LIMIT",
            "",
            f"JSON document_type={preflight.document_type!r} превышает {byte_limit} bytes.",
        )
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError as error:
        _raise_limit(
            "JSON_PARSE_ERROR",
            "",
            (
                f"Некорректный JSON в строке {error.lineno}, "
                f"столбце {error.colno}: {error.msg}."
            ),
        )
    if not isinstance(value, dict):
        _raise_limit(
            "JSON_DOCUMENT_NOT_OBJECT",
            "",
            "Корневое значение контрактного документа должно быть объектом.",
            keyword="type",
        )
    return cast(dict[str, Any], value)


def validate_json_structure(value: object) -> None:
    """Apply non-byte limits to an already materialized internal document."""

    stack: list[tuple[object, str, int]] = [(value, "", 1)]
    nodes = 0
    while stack:
        current, pointer, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            _raise_limit(
                "JSON_NODE_LIMIT",
                pointer,
                f"JSON содержит более {MAX_JSON_NODES} value nodes.",
            )
        if depth > MAX_JSON_DEPTH:
            _raise_limit(
                "JSON_DEPTH_LIMIT",
                pointer,
                f"JSON depth превышает {MAX_JSON_DEPTH}.",
            )
        if isinstance(current, Mapping):
            stack.extend(
                (
                    child,
                    _join_pointer(pointer, str(key)),
                    depth + 1,
                )
                for key, child in current.items()
            )
        elif isinstance(current, list):
            if len(current) > MAX_JSON_ARRAY_ITEMS:
                _raise_limit(
                    "JSON_ARRAY_LIMIT",
                    pointer,
                    f"JSON array содержит более {MAX_JSON_ARRAY_ITEMS} items.",
                )
            stack.extend(
                (child, _join_pointer(pointer, str(index)), depth + 1)
                for index, child in enumerate(current)
            )


class _JsonScanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0
        self.nodes = 0
        self.array_items = 0
        self.maximum_depth = 0
        self.document_type: str | None = None

    def scan(self) -> JsonPreflight:
        self._skip_whitespace()
        self._parse_value("", 1, root_key=None)
        self._skip_whitespace()
        if self.index != len(self.text):
            self._parse_error("после корневого значения есть лишние bytes")
        return JsonPreflight(
            document_type=self.document_type,
            node_count=self.nodes,
            array_item_count=self.array_items,
            maximum_depth=self.maximum_depth,
        )

    def _parse_value(
        self, pointer: str, depth: int, root_key: str | None
    ) -> str | None:
        self._count_node(pointer, depth)
        if self.index >= len(self.text):
            self._parse_error("ожидалось JSON value")
        char = self.text[self.index]
        if char == "{":
            self._parse_object(pointer, depth)
            return None
        if char == "[":
            self._parse_array(pointer, depth)
            return None
        if char == '"':
            value = self._parse_string()
            if depth == 2 and root_key == "document_type":
                self.document_type = value
            return value
        if char in "-0123456789":
            self._parse_number()
            return None
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, self.index):
                self.index += len(literal)
                return None
        self._parse_error("неизвестный JSON token")

    def _parse_object(self, pointer: str, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._consume("}"):
            return
        keys: set[str] = set()
        while True:
            if self.index >= len(self.text) or self.text[self.index] != '"':
                self._parse_error("object key должен быть строкой")
            key = self._parse_string()
            if key in keys:
                _raise_limit(
                    "JSON_DUPLICATE_KEY",
                    _join_pointer(pointer, key),
                    "JSON object содержит duplicate key.",
                )
            keys.add(key)
            self._skip_whitespace()
            if not self._consume(":"):
                self._parse_error("после object key требуется ':'")
            self._skip_whitespace()
            self._parse_value(_join_pointer(pointer, key), depth + 1, root_key=key)
            self._skip_whitespace()
            if self._consume("}"):
                return
            if not self._consume(","):
                self._parse_error("между object members требуется ','")
            self._skip_whitespace()

    def _parse_array(self, pointer: str, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._consume("]"):
            return
        item_index = 0
        while True:
            item_index += 1
            self.array_items += 1
            if item_index > MAX_JSON_ARRAY_ITEMS:
                _raise_limit(
                    "JSON_ARRAY_LIMIT",
                    pointer,
                    f"JSON array ceiling {MAX_JSON_ARRAY_ITEMS} превышен.",
                )
            self._parse_value(
                _join_pointer(pointer, str(item_index - 1)),
                depth + 1,
                root_key=None,
            )
            self._skip_whitespace()
            if self._consume("]"):
                return
            if not self._consume(","):
                self._parse_error("между array items требуется ','")
            self._skip_whitespace()

    def _parse_string(self) -> str:
        start = self.index
        self.index += 1
        while self.index < len(self.text):
            char = self.text[self.index]
            if char == '"':
                self.index += 1
                raw = self.text[start : self.index]
                try:
                    value: object = json.loads(raw)
                except json.JSONDecodeError:
                    self._parse_error("некорректный escape в JSON string")
                if not isinstance(value, str):  # pragma: no cover - string token
                    self._parse_error("некорректный JSON string")
                return value
            if char == "\\":
                self.index += 2
            else:
                if ord(char) < 0x20:
                    self._parse_error("control character в JSON string")
                self.index += 1
        self._parse_error("JSON string не закрыт")

    def _parse_number(self) -> None:
        match = _NUMBER_RE.match(self.text, self.index)
        if match is None:
            self._parse_error("некорректное JSON number")
        self.index = match.end()

    def _count_node(self, pointer: str, depth: int) -> None:
        self.nodes += 1
        self.maximum_depth = max(self.maximum_depth, depth)
        if depth > MAX_JSON_DEPTH:
            _raise_limit(
                "JSON_DEPTH_LIMIT",
                pointer,
                f"JSON depth превышает {MAX_JSON_DEPTH}.",
            )
        if self.nodes > MAX_JSON_NODES:
            _raise_limit(
                "JSON_NODE_LIMIT",
                pointer,
                f"JSON содержит более {MAX_JSON_NODES} value nodes.",
            )

    def _skip_whitespace(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _consume(self, expected: str) -> bool:
        if self.text.startswith(expected, self.index):
            self.index += len(expected)
            return True
        return False

    def _parse_error(self, message: str) -> NoReturn:
        _raise_limit(
            "JSON_PARSE_ERROR",
            "",
            f"Некорректный JSON около char {self.index}: {message}.",
        )


def _join_pointer(pointer: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{escaped}"


def _raise_limit(
    code: str,
    pointer: str,
    message: str,
    *,
    keyword: str | None = None,
) -> NoReturn:
    raise ContractValidationError(
        (
            ContractIssue(
                code=code,
                json_pointer=pointer,
                message_ru=message,
                keyword=keyword,
            ),
        )
    )
