"""Read-only MCP adapter with a strict tool allowlist and envelope normalization."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import JsonValue, ValidationError

from chatbot1c.application.deadlines import retry_fits, stage_timeout
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import (
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    GetMetadataRequest,
    McpSchema,
    MetadataEnvelope,
)
from chatbot1c.application.ports import ReadOnly1CPort
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.json_limits import validate_json_structure

MCP_ALLOWLIST = frozenset({"execute_query", "get_metadata"})
MAX_MCP_ENVELOPE_BYTES = 16 * 1024 * 1024
_TRANSPORT_FIELDS = frozenset(
    {
        "success",
        "data",
        "schema",
        "count",
        "error",
        "truncated",
        "limit",
        "returned",
        "offset",
        "has_more",
        "next_offset",
        "configuration",
        "extension",
        "last_date",
        "next_same_second_offset",
        "received",
    }
)


class McpTransport(Protocol):
    async def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> object: ...


class McpTransportError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class InMemoryMcpTransport(McpTransport):
    def __init__(
        self,
        handlers: Mapping[
            str,
            object | Callable[[Mapping[str, JsonValue]], object | Awaitable[object]],
        ],
    ) -> None:
        self._handlers = dict(handlers)
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []

    async def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> object:
        copied = dict(arguments)
        self.calls.append((name, copied))
        if name not in self._handlers:
            raise McpTransportError(f"No fixture for MCP tool {name}", retryable=False)
        value = self._handlers[name]
        if callable(value):
            result = value(copied)
            if isinstance(result, Awaitable):
                return await result
            return result
        return value


class FixtureMcpTransport(InMemoryMcpTransport):
    @classmethod
    def from_json_files(cls, execute_query: bytes, get_metadata: bytes) -> "FixtureMcpTransport":
        return cls(
            {
                "execute_query": json.loads(execute_query),
                "get_metadata": json.loads(get_metadata),
            }
        )


class LiveMcpTransport(McpTransport):
    """Official MCP Streamable HTTP client, opened for one bounded operation."""

    def __init__(self, url: str, *, channel: str | None = None) -> None:
        self._url = _with_channel(url, channel)

    async def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue]
    ) -> object:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(self._url) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments=dict(arguments))
                    if hasattr(result, "model_dump"):
                        return result.model_dump(by_alias=True)
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise McpTransportError(str(error)) from error


class McpReadOnlyAdapter(ReadOnly1CPort):
    def __init__(
        self,
        transport: McpTransport,
        *,
        basic_timeout: float = 12.0,
        metadata_timeout: float = 12.0,
        attempts: int = 2,
    ) -> None:
        self._transport = transport
        self._basic_timeout = basic_timeout
        self._metadata_timeout = metadata_timeout
        self._attempts = attempts

    async def execute_query(
        self, request: ExecuteQueryRequest
    ) -> ExecuteQueryEnvelope:
        raw, attempts = await self._call_tool(
            "execute_query",
            cast(
                dict[str, JsonValue],
                request.model_dump(mode="json", by_alias=True),
            ),
            timeout=self._basic_timeout,
            deadline_at=request.deadline_at,
        )
        envelope = _extract_envelope(raw)
        _reject_transport_extras(envelope)
        success = envelope.get("success")
        if type(success) is not bool:
            raise _invalid("Поле MCP success должно быть boolean.")
        if success is False:
            error = envelope.get("error")
            return ExecuteQueryEnvelope(
                success=False,
                data=(),
                schema=McpSchema(columns=()),
                count=0,
                error=error if isinstance(error, str) else "Ошибка выполнения запроса.",
                attempts=attempts,
            )
        required = {"data", "schema"}
        if not required <= envelope.keys():
            raise _invalid("Успешный MCP envelope не содержит data/schema.")
        data = envelope["data"]
        if not isinstance(data, list):
            raise _invalid("MCP data должно быть массивом строк.")
        inferred_boundary = len(data) == request.limit
        domain_envelope = {
            "success": True,
            "data": data,
            "schema": envelope["schema"],
            "count": envelope.get("count", len(data)),
            "truncated": envelope.get("truncated", inferred_boundary),
            "has_more": envelope.get("has_more", inferred_boundary),
            "attempts": attempts,
        }
        try:
            normalized = ExecuteQueryEnvelope.model_validate(domain_envelope)
        except ValidationError as error:
            raise _invalid("Успешный MCP envelope не соответствует typed DTO.") from error
        if normalized.count != len(normalized.data):
            raise _invalid("MCP count не совпадает с количеством data rows.")
        _validate_rows_against_schema(normalized)
        return normalized

    async def get_metadata(self, request: GetMetadataRequest) -> MetadataEnvelope:
        arguments = request.model_dump(mode="json", exclude={"mode"})
        raw, _ = await self._call_tool(
            "get_metadata",
            cast(dict[str, JsonValue], arguments),
            timeout=self._metadata_timeout,
            deadline_at=None,
        )
        envelope = _extract_envelope(raw)
        _reject_transport_extras(envelope)
        success = envelope.get("success")
        if type(success) is not bool:
            raise _invalid("Metadata envelope не содержит boolean success.")
        if success is False:
            error = envelope.get("error")
            return MetadataEnvelope(
                success=False,
                data=None,
                error=error if isinstance(error, str) else "Ошибка metadata.",
            )
        if "data" not in envelope:
            raise _invalid("Успешный metadata envelope не содержит data.")
        try:
            return MetadataEnvelope.model_validate(
                {"success": True, "data": envelope["data"], "error": envelope.get("error")}
            )
        except ValidationError as error:
            raise _invalid("Metadata envelope не соответствует typed DTO.") from error

    async def _call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        timeout: float,
        deadline_at: datetime | None,
    ) -> tuple[object, int]:
        if name not in MCP_ALLOWLIST:
            raise ApplicationError(
                "MCP_TOOL_FORBIDDEN",
                f"MCP tool {name!r} не входит в read-only allowlist.",
                403,
            )
        last_error: BaseException | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                try:
                    attempt_timeout = stage_timeout(deadline_at, timeout)
                except ApplicationError as error:
                    raise _mcp_unavailable() from error
                async with asyncio.timeout(attempt_timeout):
                    return await self._transport.call_tool(name, arguments), attempt
            except asyncio.TimeoutError as error:
                last_error = error
            except McpTransportError as error:
                last_error = error
                if not error.retryable:
                    break
            if attempt < self._attempts and retry_fits(deadline_at, backoff=0.25):
                await asyncio.sleep(0.25)
                continue
            break
        raise _mcp_unavailable() from last_error


def _extract_envelope(raw: object) -> dict[str, Any]:
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(by_alias=True)
    validate_json_structure(raw)
    try:
        size = len(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise _invalid("MCP result нельзя представить как JSON.") from error
    if size > MAX_MCP_ENVELOPE_BYTES:
        raise ApplicationError(
            "MCP_ENVELOPE_LIMIT", "MCP envelope превышает 16 MiB.", 502
        )
    if not isinstance(raw, dict):
        raise _invalid("MCP result должен быть object wrapper.")
    structured = raw.get("structuredContent")
    content = raw.get("content")
    if isinstance(structured, dict):
        if content not in (None, []):
            if not isinstance(content, list):
                raise _invalid("MCP content должен быть массивом blocks.")
            text_blocks = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            for block in text_blocks:
                if not isinstance(block, str):
                    raise _invalid("MCP text content имеет неверный тип.")
                try:
                    decoded: object = json.loads(block)
                except json.JSONDecodeError as error:
                    raise _invalid(
                        "MCP text content противоречит structuredContent."
                    ) from error
                if not isinstance(decoded, dict) or canonicalize(decoded) != canonicalize(
                    structured
                ):
                    raise _invalid(
                        "MCP text content противоречит structuredContent."
                    )
        return cast(dict[str, Any], structured)
    if isinstance(content, list):
        text_blocks = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(text_blocks) != 1 or not isinstance(text_blocks[0], str):
            raise _invalid("MCP wrapper должен содержать ровно один text JSON block.")
        try:
            value: object = json.loads(text_blocks[0])
        except json.JSONDecodeError as error:
            raise _invalid("MCP text block содержит невалидный JSON.") from error
        validate_json_structure(value)
        if not isinstance(value, dict):
            raise _invalid("MCP text block JSON должен быть object.")
        return cast(dict[str, Any], value)
    if "success" in raw:
        return cast(dict[str, Any], raw)
    raise _invalid("MCP wrapper не содержит поддерживаемый envelope.")


def _validate_rows_against_schema(envelope: ExecuteQueryEnvelope) -> None:
    names = [column.name for column in envelope.schema_.columns]
    if len(names) != len(set(name.casefold() for name in names)):
        raise _invalid("MCP schema содержит duplicate columns.")
    expected = set(names)
    for row in envelope.data:
        if set(row) != expected:
            raise _invalid("MCP row columns не совпадают со schema columns.")


def _invalid(message: str) -> ApplicationError:
    return ApplicationError("MCP_ENVELOPE_INVALID", message, 502)


def _mcp_unavailable() -> ApplicationError:
    return ApplicationError(
        "MCP_UNAVAILABLE", "Read-only MCP 1С временно недоступен.", 503
    )


def _reject_transport_extras(envelope: Mapping[str, object]) -> None:
    unknown = set(envelope) - _TRANSPORT_FIELDS
    if unknown:
        raise _invalid(
            "MCP envelope содержит неизвестные поля: " + ", ".join(sorted(unknown))
        )


def _with_channel(url: str, channel: str | None) -> str:
    if not channel:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["channel"] = channel
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
