"""Draft 2020-12 schema loading and validation from the project root."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

from chatbot1c.contracts.errors import (
    ContractIssue,
    raise_for_issues,
)
from chatbot1c.contracts.json_limits import load_bounded_json

REQUIRED_SCHEMAS = frozenset(
    {
        "evidence.schema.json",
        "planner-output.schema.json",
        "skill-package.schema.json",
        "skill.schema.json",
    }
)


def escape_json_pointer_token(token: object) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def json_pointer(path: Sequence[object]) -> str:
    if not path:
        return ""
    return "/" + "/".join(escape_json_pointer_token(item) for item in path)


@dataclass(frozen=True, slots=True)
class SchemaRepository:
    schemas_dir: Traversable
    _schemas: Mapping[str, dict[str, Any]]
    _registry: Registry

    @classmethod
    def discover(cls, project_root: Path | str | None = None) -> "SchemaRepository":
        schemas_dir = _find_schemas_dir(project_root)
        schemas: dict[str, dict[str, Any]] = {}
        registry = Registry()

        for path in _schema_files(schemas_dir):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Cannot load JSON Schema {path}: {error}"
                ) from error
            if not isinstance(document, dict):
                raise RuntimeError(f"JSON Schema must be an object: {path}")
            try:
                Draft202012Validator.check_schema(document)
            except SchemaError as error:
                raise RuntimeError(
                    f"Invalid Draft 2020-12 schema {path}: {error}"
                ) from error
            schema_id = document.get("$id")
            if not isinstance(schema_id, str):
                raise RuntimeError(f"JSON Schema has no string $id: {path}")
            schemas[path.name] = document
            registry = registry.with_resource(
                schema_id, Resource.from_contents(document)
            )

        missing = REQUIRED_SCHEMAS - schemas.keys()
        if missing:
            names = ", ".join(sorted(missing))
            raise FileNotFoundError(
                f"Required schemas are missing from {schemas_dir}: {names}"
            )
        return cls(
            schemas_dir=schemas_dir,
            _schemas=MappingProxyType(schemas),
            _registry=registry,
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))

    def schema(self, name: str) -> dict[str, Any]:
        try:
            return deepcopy(self._schemas[name])
        except KeyError as error:
            raise KeyError(f"Unknown contract schema: {name}") from error

    def validator(self, name: str) -> Draft202012Validator:
        try:
            schema = self._schemas[name]
        except KeyError as error:
            raise KeyError(f"Unknown contract schema: {name}") from error
        return Draft202012Validator(
            schema,
            registry=self._registry,
            format_checker=FormatChecker(),
        )

    def issues(self, instance: object, schema_name: str) -> tuple[ContractIssue, ...]:
        errors = self.validator(schema_name).iter_errors(instance)
        issues = {_schema_issue(error) for error in _leaf_errors(errors)}
        discriminator_parents = {
            issue.json_pointer.rsplit("/", 1)[0]
            for issue in issues
            if issue.keyword == "const"
            and issue.json_pointer.rsplit("/", 1)[-1]
            in {"kind", "operator", "source", "strategy"}
        }
        if discriminator_parents:
            issues = {
                issue
                for issue in issues
                if not any(
                    issue.json_pointer.startswith(parent + "/")
                    and issue.json_pointer.rsplit("/", 1)[-1]
                    not in {"kind", "operator", "source", "strategy"}
                    for parent in discriminator_parents
                )
            }
        return tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.json_pointer,
                    issue.keyword or "",
                    issue.message_ru,
                ),
            )
        )

    def validate(self, instance: object, schema_name: str) -> None:
        raise_for_issues(self.issues(instance, schema_name))

    def load_json(self, path: Path | str) -> dict[str, Any]:
        return load_bounded_json(path)

    def load_and_validate(self, path: Path | str, schema_name: str) -> dict[str, Any]:
        document = self.load_json(path)
        self.validate(document, schema_name)
        return document


def _find_schemas_dir(project_root: Path | str | None) -> Traversable:
    if project_root is not None:
        supplied = Path(project_root).expanduser().resolve()
        candidate = supplied if supplied.name == "schemas" else supplied / "schemas"
        if _contains_required_schemas(candidate):
            return candidate
        raise FileNotFoundError(
            f"Project schemas directory is missing required schemas: {candidate}"
        )

    starts = (Path.cwd().resolve(), Path(__file__).resolve())
    visited: set[Path] = set()
    for start in starts:
        for parent in (start, *start.parents):
            if parent in visited:
                continue
            visited.add(parent)
            candidate = parent / "schemas"
            if (parent / "pyproject.toml").is_file() and _contains_required_schemas(
                candidate
            ):
                return candidate

    packaged_schemas = files("chatbot1c").joinpath("schemas")
    if _contains_required_schemas(packaged_schemas):
        return packaged_schemas
    raise FileNotFoundError(
        "Cannot locate canonical project schemas or packaged schema resources; "
        "pass project_root explicitly."
    )


def _schema_files(directory: Traversable) -> tuple[Traversable, ...]:
    return tuple(
        sorted(
            (
                child
                for child in directory.iterdir()
                if child.is_file() and child.name.endswith(".json")
            ),
            key=lambda child: child.name,
        )
    )


def _contains_required_schemas(directory: Traversable) -> bool:
    if not directory.is_dir():
        return False
    names = {path.name for path in _schema_files(directory)}
    return REQUIRED_SCHEMAS <= names


def _leaf_errors(errors: Iterator[ValidationError]) -> Iterator[ValidationError]:
    pending = deque(errors)
    while pending:
        error = pending.popleft()
        if error.context and error.validator in {"oneOf", "anyOf"}:
            pending.extend(_closest_branch(error))
            continue
        yield error


def _closest_branch(error: ValidationError) -> tuple[ValidationError, ...]:
    branches: dict[int, list[ValidationError]] = {}
    ungrouped: list[ValidationError] = []
    for child in error.context:
        relative_path = list(child.relative_schema_path)
        if relative_path and isinstance(relative_path[0], int):
            branches.setdefault(relative_path[0], []).append(child)
        else:
            ungrouped.append(child)
    if not branches:
        return tuple(ungrouped or error.context)

    expanded: list[tuple[ValidationError, ...]] = []
    for branch in branches.values():
        leaves: list[ValidationError] = []
        for child in branch:
            if child.context and child.validator in {"oneOf", "anyOf"}:
                leaves.extend(_closest_branch(child))
            else:
                leaves.append(child)
        expanded.append(tuple(leaves))
    return min(
        expanded,
        key=lambda leaves: (
            len(leaves),
            sum(len(error.absolute_path) for error in leaves),
        ),
    )


def _schema_issue(error: ValidationError) -> ContractIssue:
    path = list(error.absolute_path)
    detail: str | None = None

    if error.validator == "required":
        detail = _quoted_value(error.message)
        if detail is not None:
            path.append(detail)
    elif error.validator == "additionalProperties":
        detail = _unexpected_property(error)
        if detail is not None:
            path.append(detail)

    return ContractIssue(
        code="JSON_SCHEMA_VALIDATION_ERROR",
        json_pointer=json_pointer(path),
        message_ru=_localized_message(error, detail),
        keyword=str(error.validator),
    )


def _quoted_value(message: str) -> str | None:
    match = re.search(r"'([^']+)'", message)
    return match.group(1) if match else None


def _unexpected_property(error: ValidationError) -> str | None:
    if isinstance(error.instance, Mapping) and isinstance(error.schema, Mapping):
        declared = set(error.schema.get("properties", {}))
        unexpected = sorted(set(error.instance) - declared)
        if unexpected:
            return str(unexpected[0])
    return _quoted_value(error.message)


def _localized_message(error: ValidationError, detail: str | None) -> str:
    validator = error.validator
    if validator == "required" and detail:
        return f"Отсутствует обязательное поле '{detail}'."
    if validator == "additionalProperties" and detail:
        return f"Поле '{detail}' не разрешено контрактом."
    if validator == "const":
        return f"Значение должно быть равно {error.validator_value!r}."
    if validator == "enum":
        return "Значение не входит в закрытый список допустимых значений."
    if validator == "type":
        return f"Значение должно иметь JSON-тип {error.validator_value!r}."
    if validator == "format":
        return f"Значение не соответствует формату {error.validator_value!r}."
    if validator == "pattern":
        return "Строка не соответствует обязательному шаблону."
    if validator == "uniqueItems":
        return "Массив содержит повторяющиеся элементы."
    return error.message
