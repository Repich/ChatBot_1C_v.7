from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from time import monotonic
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RejectedResult(Exception):
    pass


@dataclass
class Command:
    command_id: str
    rpc_id: int | str
    tool: str
    params: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    state: str = "queued"


class OracleState:
    def __init__(self) -> None:
        self.timeout = float(os.getenv("MCP_PROXY_COMMAND_TIMEOUT_SECONDS", "0.35"))
        self.heartbeat = float(os.getenv("MCP_PROXY_HEARTBEAT_SECONDS", "0.25"))
        self.poll_wait = float(os.getenv("MCP_PROXY_POLL_WAIT_SECONDS", "0.05"))
        self.max_pending = int(os.getenv("MCP_PROXY_MAX_PENDING_PER_CHANNEL", "4"))
        self.max_channels = int(os.getenv("MCP_PROXY_MAX_CHANNELS", "4"))
        self.max_bytes = int(os.getenv("MCP_PROXY_MAX_RESULT_BYTES", "16777216"))
        self.max_rows = int(os.getenv("MCP_PROXY_MAX_ROWS", "1000"))
        self.max_depth = int(os.getenv("MCP_PROXY_MAX_JSON_DEPTH", "32"))
        self.max_nodes = int(os.getenv("MCP_PROXY_MAX_JSON_NODES", "100000"))
        self.commands: dict[str, dict[str, Command]] = {}
        self.rpc_commands: dict[tuple[str, int | str], Command] = {}
        self.terminal_ids: dict[str, set[str]] = {}
        self.last_poll: dict[str, float] = {}
        self.lock = asyncio.Lock()

    async def create_command(
        self,
        channel: str,
        rpc_id: int | str,
        tool: str,
        params: dict[str, Any],
    ) -> Command:
        async with self.lock:
            channel_commands = self.commands.setdefault(channel, {})
            active = sum(
                command.state in {"queued", "leased_to_1c"}
                for command in channel_commands.values()
            )
            if active >= self.max_pending:
                raise RejectedResult("pending command limit exceeded")
            command = Command(
                command_id=uuid.uuid4().hex,
                rpc_id=rpc_id,
                tool=tool,
                params=params,
                future=asyncio.get_running_loop().create_future(),
            )
            channel_commands[command.command_id] = command
            self.rpc_commands[(channel, rpc_id)] = command
            return command

    async def finish(self, channel: str, command: Command, state: str) -> None:
        async with self.lock:
            command.state = state
            self.rpc_commands.pop((channel, command.rpc_id), None)
            self.commands.get(channel, {}).pop(command.command_id, None)
            self.terminal_ids.setdefault(channel, set()).add(command.command_id)


def create_app() -> FastAPI:
    app = FastAPI()
    state = OracleState()

    def checked_channel(value: str) -> str:
        if not CHANNEL_PATTERN.fullmatch(value):
            raise RejectedResult("invalid channel")
        known = set(state.commands) | set(state.last_poll)
        if value not in known and len(known) >= state.max_channels:
            raise RejectedResult("channel limit exceeded")
        return value

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def health_ready(channel: str = "default") -> JSONResponse:
        try:
            checked_channel(channel)
        except RejectedResult as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        last_poll = state.last_poll.get(channel)
        connected = last_poll is not None and monotonic() - last_poll <= state.heartbeat
        payload = {
            "proxy_ready": True,
            "one_c_connected": connected,
            "channel": channel,
        }
        return JSONResponse(payload, status_code=200 if connected else 503)

    @app.get("/1c/poll")
    async def poll(channel: str = "default") -> Response:
        try:
            checked_channel(channel)
        except RejectedResult as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        state.last_poll[channel] = monotonic()
        deadline = monotonic() + state.poll_wait
        while monotonic() <= deadline:
            async with state.lock:
                for command in state.commands.get(channel, {}).values():
                    if command.state == "queued":
                        command.state = "leased_to_1c"
                        return JSONResponse(
                            {
                                "id": command.command_id,
                                "tool": command.tool,
                                "params": command.params,
                            }
                        )
            await asyncio.sleep(0.005)
        return Response(status_code=204)

    @app.post("/1c/result")
    async def result(request: Request, channel: str = "default") -> Response:
        try:
            checked_channel(channel)
            payload = await _bounded_json(request, state)
        except RejectedResult as error:
            status = 413 if "limit" in str(error) else 400
            return JSONResponse({"error": str(error)}, status_code=status)
        command_id = payload.get("id")
        if not isinstance(command_id, str):
            return JSONResponse({"error": "id must be string"}, status_code=422)
        async with state.lock:
            command = state.commands.get(channel, {}).get(command_id)
            terminal = command_id in state.terminal_ids.get(channel, set())
        if command is None:
            return JSONResponse(
                {"error": "result is late or duplicate" if terminal else "unknown id"},
                status_code=409,
            )
        try:
            _validate_envelope(payload, state, command.tool)
        except RejectedResult as error:
            if not command.future.done():
                command.future.set_exception(error)
            await state.finish(channel, command, "expired")
            return JSONResponse({"error": str(error)}, status_code=422)
        if command.state != "leased_to_1c":
            return JSONResponse({"error": "command is not leased"}, status_code=409)
        if not command.future.done():
            command.future.set_result(payload)
        await state.finish(channel, command, "completed")
        return JSONResponse({"accepted": True})

    @app.post("/mcp")
    async def mcp(request: Request, channel: str = "default") -> Response:
        try:
            checked_channel(channel)
            message = await _bounded_json(request, state)
        except RejectedResult as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        method = message.get("method")
        rpc_id = message.get("id")
        params = message.get("params", {})
        if method == "initialize":
            return _rpc(
                rpc_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "acceptance-oracle", "version": "1.0"},
                },
                headers={"Mcp-Session-Id": uuid.uuid4().hex},
            )
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "tools/list":
            return _rpc(rpc_id, {"tools": _tool_specs()})
        if method == "notifications/cancelled":
            request_id = params.get("requestId") if isinstance(params, dict) else None
            async with state.lock:
                command = state.rpc_commands.get((channel, request_id))
            if command is not None:
                if not command.future.done():
                    command.future.set_exception(RejectedResult("command cancelled"))
                await state.finish(channel, command, "cancelled")
            return Response(status_code=202)
        if method != "tools/call" or not isinstance(params, dict):
            return _rpc_error(rpc_id, -32601, "method not found")
        name = params.get("name")
        arguments = params.get("arguments")
        if name not in {"execute_query", "get_metadata"}:
            return _rpc_error(rpc_id, -32602, "tool is not allowed")
        if not isinstance(arguments, dict):
            return _rpc_error(rpc_id, -32602, "arguments must be object")
        if name == "get_metadata" and "mode" in arguments:
            arguments = {
                key: value for key, value in arguments.items() if key != "mode"
            }
        try:
            command = await state.create_command(channel, rpc_id, name, arguments)
        except RejectedResult as error:
            return _rpc_error(rpc_id, -32002, str(error))
        try:
            envelope = await asyncio.wait_for(
                asyncio.shield(command.future), timeout=state.timeout
            )
        except TimeoutError:
            await state.finish(channel, command, "expired")
            return _rpc_error(rpc_id, -32001, "1C transport timeout")
        except RejectedResult as error:
            return _rpc_error(rpc_id, -32002, str(error))
        except asyncio.CancelledError:
            if not command.future.done():
                command.future.cancel()
            await state.finish(channel, command, "cancelled")
            raise
        public_envelope = {key: value for key, value in envelope.items() if key != "id"}
        result_payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(public_envelope, ensure_ascii=False),
                }
            ],
            "structuredContent": public_envelope,
            "isError": public_envelope.get("success") is False,
        }
        return _rpc(rpc_id, result_payload)

    return app


async def _bounded_json(request: Request, state: OracleState) -> dict[str, Any]:
    body = await request.body()
    if len(body) > state.max_bytes:
        raise RejectedResult("byte limit exceeded")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RejectedResult("strict UTF-8 required") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RejectedResult("malformed JSON") from error
    if not isinstance(value, dict):
        raise RejectedResult("root JSON must be object")
    _check_shape(value, state)
    return value


def _check_shape(value: object, state: OracleState) -> None:
    stack = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > state.max_nodes:
            raise RejectedResult("node limit exceeded")
        if depth > state.max_depth:
            raise RejectedResult("depth limit exceeded")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _validate_envelope(payload: dict[str, Any], state: OracleState, tool: str) -> None:
    success = payload.get("success")
    if not isinstance(success, bool):
        raise RejectedResult("success must be boolean")
    if not success:
        if not isinstance(payload.get("error"), str):
            raise RejectedResult("query error must contain error string")
        return
    if tool == "get_metadata":
        if "data" not in payload:
            raise RejectedResult("successful metadata result requires data")
        return
    data = payload.get("data")
    schema = payload.get("schema")
    if not isinstance(data, list) or not isinstance(schema, dict):
        raise RejectedResult("successful result requires data and schema")
    if len(data) > state.max_rows:
        raise RejectedResult("row limit exceeded")
    if payload.get("count") != len(data):
        raise RejectedResult("count does not match rows")
    columns = schema.get("columns")
    if not isinstance(columns, list):
        raise RejectedResult("schema columns must be array")
    names = set()
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("name"), str):
            raise RejectedResult("malformed schema column")
        if not isinstance(column.get("types"), list):
            raise RejectedResult("malformed schema types")
        names.add(column["name"])
    if any(not isinstance(row, dict) or set(row) != names for row in data):
        raise RejectedResult("rows do not match schema")


def _rpc(
    rpc_id: object,
    result: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result}, headers=headers
    )


def _rpc_error(rpc_id: object, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        }
    )


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "execute_query",
            "description": "Execute a read-only 1C query",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query", "params", "limit"],
                "properties": {
                    "query": {"type": "string"},
                    "params": {"type": "object"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "include_schema": {"const": True},
                },
            },
        },
        {
            "name": "get_metadata",
            "description": "Read 1C metadata",
            "inputSchema": {
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
            },
        },
    ]
