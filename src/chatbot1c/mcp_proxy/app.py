"""ASGI application for the local read-only 1C MCP bridge."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import ProxySettings
from .jsonio import PayloadError, load_json_object
from .state import (
    BridgeCommand,
    ChannelError,
    CommandOutcome,
    PendingLimitError,
    ProxyState,
)

_PROTOCOL_VERSION = "2025-06-18"
_SERVER_INFO = {"name": "chatbot1c-local-proxy", "version": "1.0"}

_EXECUTE_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query", "params", "limit", "include_schema"],
    "properties": {
        "query": {"type": "string", "minLength": 10, "maxLength": 50000},
        "params": {"type": "object"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "include_schema": {"const": True},
    },
}

_METADATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "filter": {"type": ["string", "null"]},
        "meta_type": {"type": ["string", "null"]},
        "name_mask": {"type": ["string", "null"]},
        "attribute_mask": {"type": ["string", "null"]},
        "sections": {"type": "array", "items": {"type": "string"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "offset": {"type": "integer", "minimum": 0},
        "extension_name": {"type": ["string", "null"]},
    },
}

_TOOLS = [
    {
        "name": "execute_query",
        "description": "Execute one bounded read-only 1C query.",
        "inputSchema": _EXECUTE_QUERY_SCHEMA,
    },
    {
        "name": "get_metadata",
        "description": "Read 1C metadata without local adapter-only fields.",
        "inputSchema": _METADATA_SCHEMA,
    },
]


def create_app() -> FastAPI:
    settings = ProxySettings.from_env()
    state = ProxyState(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await state.shutdown()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health/live")
    async def health_live() -> JSONResponse:
        return JSONResponse({"status": "live"})

    @app.get("/health/ready")
    async def health_ready(channel: str = Query(default="default")) -> JSONResponse:
        try:
            connected = await state.readiness(channel)
        except ChannelError as error:
            return _http_error(400, str(error))
        body = {"proxy_ready": True, "one_c_connected": connected}
        return JSONResponse(body, status_code=200 if connected else 503)

    @app.get("/1c/poll")
    async def one_c_poll(channel: str = Query(default="default")) -> Response:
        try:
            command = await state.lease(channel)
        except ChannelError as error:
            return _http_error(400, str(error))
        if command is None:
            return Response(status_code=204)
        return JSONResponse(command.wire())

    @app.post("/1c/result")
    async def one_c_result(
        request: Request, channel: str = Query(default="default")
    ) -> Response:
        try:
            state.validate_channel(channel)
            payload = await _load(request, settings)
        except ChannelError as error:
            return _http_error(400, str(error))
        except PayloadError as error:
            return _http_error(error.status_code, error.message)

        command_id = payload.get("id")
        if not isinstance(command_id, str) or not command_id:
            return _http_error(400, "result id must be a non-empty string")
        command = await state.command_for_result(channel, command_id)
        if command is None:
            return _http_error(409, "unknown, late, or duplicate command result")
        try:
            public_payload = _validate_result(command, payload, settings)
        except PayloadError as error:
            await state.fail_result(channel, command_id, error.message)
            return _http_error(error.status_code, error.message)
        accepted = await state.submit_result(channel, command_id, public_payload)
        if not accepted:
            return _http_error(409, "command is no longer pending")
        return JSONResponse({"accepted": True})

    # The official SDK owns general MCP semantics. This bridge implements only the
    # bounded request/response subset needed for two read-only tools because their
    # completion and cancellation are controlled by a separate 1C long-poller.
    @app.post("/mcp")
    async def mcp_post(
        request: Request, channel: str = Query(default="default")
    ) -> Response:
        try:
            state.validate_channel(channel)
            payload = await _load(request, settings)
        except ChannelError as error:
            return _http_error(400, str(error))
        except PayloadError as error:
            return _http_error(error.status_code, error.message)

        method = payload.get("method")
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _rpc_error(request_id, -32600, "Invalid JSON-RPC request")
        session_id = request.headers.get("mcp-session-id") or ""

        if method == "initialize":
            session_id = uuid4().hex
            result = {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
            }
            return _rpc_result(request_id, result, session_id=session_id)
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "notifications/cancelled":
            params = payload.get("params")
            cancelled_id = params.get("requestId") if isinstance(params, dict) else None
            if cancelled_id is not None:
                await state.cancel_request(
                    channel, session_id, _request_key(cancelled_id)
                )
            return Response(status_code=202)
        if method == "tools/list":
            return _rpc_result(request_id, {"tools": _TOOLS}, session_id=session_id)
        if method == "tools/call":
            return await _handle_tool_call(
                request=request,
                state=state,
                channel=channel,
                session_id=session_id,
                request_id=request_id,
                params=payload.get("params"),
            )
        return _rpc_error(request_id, -32601, "Method not found")

    @app.get("/mcp")
    async def mcp_get(
        request: Request, channel: str = Query(default="default")
    ) -> Response:
        try:
            state.validate_channel(channel)
        except ChannelError as error:
            return _http_error(400, str(error))
        if not request.headers.get("mcp-session-id"):
            return _http_error(400, "Mcp-Session-Id header is required")

        async def idle_stream() -> AsyncIterator[bytes]:
            yield b": connected\n\n"
            while not await request.is_disconnected():
                await asyncio.sleep(10)
                yield b": keepalive\n\n"

        return StreamingResponse(idle_stream(), media_type="text/event-stream")

    @app.delete("/mcp")
    async def mcp_delete(
        request: Request, channel: str = Query(default="default")
    ) -> Response:
        session_id = request.headers.get("mcp-session-id")
        if not session_id:
            return _http_error(400, "Mcp-Session-Id header is required")
        try:
            await state.cancel_session(channel, session_id)
        except ChannelError as error:
            return _http_error(400, str(error))
        return Response(status_code=204)

    return app


async def _handle_tool_call(
    *,
    request: Request,
    state: ProxyState,
    channel: str,
    session_id: str,
    request_id: Any,
    params: Any,
) -> Response:
    if request_id is None:
        return _rpc_error(None, -32600, "tools/call requires request id")
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Invalid tools/call params")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if name not in {"execute_query", "get_metadata"}:
        return _rpc_error(request_id, -32602, "Unknown or forbidden tool")
    if not isinstance(arguments, dict):
        return _rpc_error(request_id, -32602, "Tool arguments must be an object")
    try:
        normalized = _validate_arguments(cast(str, name), cast(dict[str, Any], arguments))
        command = await state.enqueue(
            channel=channel,
            session_id=session_id,
            request_key=_request_key(request_id),
            tool=cast(Literal["execute_query", "get_metadata"], name),
            params=normalized,
        )
    except PayloadError as error:
        return _rpc_error(request_id, -32602, error.message)
    except PendingLimitError as error:
        return _rpc_error(request_id, -32001, f"Pending command limit: {error}")
    except (ChannelError, RuntimeError) as error:
        return _rpc_error(request_id, -32002, f"MCP bridge unavailable: {error}")

    outcome = await _wait_for_outcome(request, state, command)
    if outcome.kind == "result" and outcome.payload is not None:
        return _tool_result(request_id, outcome.payload, session_id=session_id)
    return _rpc_error(request_id, -32000, outcome.message, session_id=session_id)


async def _wait_for_outcome(
    request: Request, state: ProxyState, command: BridgeCommand
) -> CommandOutcome:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + state.settings.command_timeout_seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await state.expire(command)
                return await command.future
            done, _ = await asyncio.wait(
                {command.future}, timeout=min(0.025, remaining)
            )
            if done:
                return command.future.result()
            if await request.is_disconnected():
                await state.cancel(command, "MCP request cancelled after disconnect")
                return await command.future
    except asyncio.CancelledError:
        await state.cancel(command, "MCP request cancelled")
        raise


async def _load(request: Request, settings: ProxySettings) -> dict[str, Any]:
    return await load_json_object(
        request,
        max_bytes=settings.max_result_bytes,
        max_depth=settings.max_json_depth,
        max_nodes=settings.max_json_nodes,
    )


def _validate_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "execute_query":
        expected = {"query", "params", "limit", "include_schema"}
        if set(arguments) != expected:
            raise PayloadError(400, "execute_query arguments do not match schema")
        query = arguments.get("query")
        limit = arguments.get("limit")
        if not isinstance(query, str) or not 10 <= len(query) <= 50_000:
            raise PayloadError(400, "execute_query query is invalid")
        if not isinstance(arguments.get("params"), dict):
            raise PayloadError(400, "execute_query params must be an object")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise PayloadError(400, "execute_query limit is invalid")
        if arguments.get("include_schema") is not True:
            raise PayloadError(400, "execute_query include_schema must be true")
        return dict(arguments)

    allowed = {
        "filter",
        "meta_type",
        "name_mask",
        "attribute_mask",
        "sections",
        "limit",
        "offset",
        "extension_name",
    }
    if not set(arguments) <= allowed or "mode" in arguments:
        raise PayloadError(400, "get_metadata arguments do not match schema")
    for key in ("filter", "meta_type", "name_mask", "attribute_mask", "extension_name"):
        value = arguments.get(key)
        if value is not None and not isinstance(value, str):
            raise PayloadError(400, f"get_metadata {key} is invalid")
    sections = arguments.get("sections", [])
    if not isinstance(sections, list) or not all(
        isinstance(item, str) for item in sections
    ):
        raise PayloadError(400, "get_metadata sections is invalid")
    limit = arguments.get("limit", 100)
    offset = arguments.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise PayloadError(400, "get_metadata limit is invalid")
    if type(offset) is not int or offset < 0:
        raise PayloadError(400, "get_metadata offset is invalid")
    return dict(arguments)


def _validate_result(
    command: BridgeCommand, payload: dict[str, Any], settings: ProxySettings
) -> dict[str, Any]:
    success = payload.get("success")
    if type(success) is not bool:
        raise PayloadError(422, "result success must be boolean")
    public = {key: value for key, value in payload.items() if key != "id"}
    if success is False:
        if set(payload) != {"id", "success", "error"} or not isinstance(
            payload.get("error"), str
        ):
            raise PayloadError(422, "failed result requires only a string error")
        return public
    if command.tool == "get_metadata":
        if not set(payload) <= {"id", "success", "data", "error"}:
            raise PayloadError(422, "metadata result contains unknown fields")
        if "data" not in payload:
            raise PayloadError(422, "metadata result requires data")
        if payload.get("error") is not None and not isinstance(payload["error"], str):
            raise PayloadError(422, "metadata error must be a string or null")
        return public

    allowed = {
        "id",
        "success",
        "data",
        "schema",
        "count",
        "truncated",
        "has_more",
        "error",
    }
    if not set(payload) <= allowed or not {"data", "schema", "count"} <= set(
        payload
    ):
        raise PayloadError(422, "execute_query result does not match schema")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise PayloadError(422, "execute_query data rows must be an array")
    if len(rows) > settings.max_rows:
        raise PayloadError(413, "execute_query row limit exceeded")
    count = payload.get("count")
    if type(count) is not int or count != len(rows):
        raise PayloadError(422, "execute_query count does not match row count")
    schema = payload.get("schema")
    if not isinstance(schema, dict) or set(schema) != {"columns"}:
        raise PayloadError(422, "execute_query schema is invalid")
    columns = schema.get("columns")
    if not isinstance(columns, list):
        raise PayloadError(422, "execute_query schema columns must be an array")
    names: list[str] = []
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"name", "types"}:
            raise PayloadError(422, "execute_query schema column is invalid")
        name = column.get("name")
        types = column.get("types")
        if not isinstance(name, str) or not name or not isinstance(types, list):
            raise PayloadError(422, "execute_query schema column is invalid")
        if not all(isinstance(item, str) and item for item in types):
            raise PayloadError(422, "execute_query schema types are invalid")
        names.append(name)
    if len({name.casefold() for name in names}) != len(names):
        raise PayloadError(422, "execute_query schema has duplicate columns")
    expected = set(names)
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected:
            raise PayloadError(422, "execute_query row does not match schema columns")
    for key in ("truncated", "has_more"):
        if key in payload and type(payload[key]) is not bool:
            raise PayloadError(422, f"execute_query {key} must be boolean")
    if payload.get("error") is not None and not isinstance(payload["error"], str):
        raise PayloadError(422, "execute_query error must be a string or null")
    return public


def _request_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rpc_result(
    request_id: Any, result: dict[str, Any], *, session_id: str = ""
) -> JSONResponse:
    headers = {"Mcp-Session-Id": session_id} if session_id else None
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers
    )


def _tool_result(
    request_id: Any, payload: dict[str, Any], *, session_id: str
) -> JSONResponse:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _rpc_result(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": payload.get("success") is False,
        },
        session_id=session_id,
    )


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    session_id: str = "",
) -> JSONResponse:
    headers = {"Mcp-Session-Id": session_id} if session_id else None
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        headers=headers,
    )


def _http_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)
