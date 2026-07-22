"""Strict bounded JSON parsing shared by MCP and 1C ingress routes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request


@dataclass(frozen=True, slots=True)
class PayloadError(Exception):
    status_code: int
    message: str


async def load_json_object(
    request: Request,
    *,
    max_bytes: int,
    max_depth: int,
    max_nodes: int,
) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type.casefold() != "application/json":
        raise PayloadError(415, "Content-Type must be application/json")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise PayloadError(413, "JSON payload exceeds byte limit")
        except ValueError as error:
            raise PayloadError(400, "Invalid Content-Length header") from error

    body = await request.body()
    if len(body) > max_bytes:
        raise PayloadError(413, "JSON payload exceeds byte limit")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PayloadError(400, "JSON payload must be strict UTF-8") from error
    try:
        value = json.loads(
            text,
            parse_constant=_reject_non_finite,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise PayloadError(400, f"Invalid JSON payload: {error}") from error
    _validate_bounds(value, max_depth=max_depth, max_nodes=max_nodes)
    if not isinstance(value, dict):
        raise PayloadError(400, "JSON payload must be an object")
    return cast(dict[str, Any], value)


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _validate_bounds(value: Any, *, max_depth: int, max_nodes: int) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise PayloadError(413, "JSON payload exceeds node limit")
        if depth > max_depth:
            raise PayloadError(400, "JSON payload exceeds depth limit")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
