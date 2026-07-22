from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = Path(__file__).resolve().parent

PROXY_IMPLEMENTATIONS = (
    pytest.param("oracle", marks=pytest.mark.proxy_harness, id="oracle"),
    pytest.param("target", marks=pytest.mark.proxy_target_gap, id="target"),
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


@dataclass
class RunningProxy:
    kind: str
    root: Path
    process: subprocess.Popen[bytes] | None = None
    base_url: str = ""
    startup_error: str = ""
    log_path: Path = field(init=False)
    _log_stream: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.log_path = self.root / f"{self.kind}-proxy.log"

    def start(self) -> RunningProxy:
        self.stop()
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        self.startup_error = ""
        environment = os.environ.copy()
        python_path = os.pathsep.join((str(REPO_ROOT / "src"), str(TEST_ROOT)))
        if environment.get("PYTHONPATH"):
            python_path += os.pathsep + environment["PYTHONPATH"]
        environment.update(
            {
                "PYTHONPATH": python_path,
                "PYTHONDONTWRITEBYTECODE": "1",
                "MCP_PROXY_COMMAND_TIMEOUT_SECONDS": "0.35",
                "MCP_PROXY_HEARTBEAT_SECONDS": "0.25",
                "MCP_PROXY_POLL_WAIT_SECONDS": "0.05",
                "MCP_PROXY_MAX_PENDING_PER_CHANNEL": "4",
                "MCP_PROXY_MAX_CHANNELS": "4",
                "MCP_PROXY_MAX_RESULT_BYTES": str(16 * 1024 * 1024),
                "MCP_PROXY_MAX_ROWS": "1000",
                "MCP_PROXY_MAX_JSON_DEPTH": "32",
                "MCP_PROXY_MAX_JSON_NODES": "100000",
            }
        )
        app = (
            "oracle_app:create_app"
            if self.kind == "oracle"
            else "chatbot1c.mcp_proxy:create_app"
        )
        self._log_stream = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                app,
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=REPO_ROOT,
            env=environment,
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                response = httpx.get(f"{self.base_url}/health/live", timeout=0.2)
                if response.status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.025)
        log = self._read_log()
        self.startup_error = (
            "[PROXY-GAP:ASGI_ENTRYPOINT] Expected public ASGI factory "
            f"{app!r} did not become live.\n{log[-4000:]}"
        )
        self.stop()
        return self

    def restart(self) -> RunningProxy:
        return self.start()

    def require_started(self) -> None:
        assert not self.startup_error, self.startup_error
        assert self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None

    def _read_log(self) -> str:
        if self._log_stream is not None:
            self._log_stream.flush()
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: httpx.Headers
    body: bytes

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body.decode("utf-8"))
        assert isinstance(value, dict)
        return value


class ProxyHttpClient:
    def __init__(self, server: RunningProxy) -> None:
        server.require_started()
        self.server = server

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 2,
    ) -> HttpResult:
        async with httpx.AsyncClient(base_url=self.server.base_url) as client:
            response = await client.request(
                method,
                path,
                json=json_body,
                content=content,
                headers=headers,
                timeout=timeout,
            )
        return HttpResult(response.status_code, response.headers, response.content)

    async def poll(self, channel: str = "default") -> HttpResult:
        return await self.request("GET", f"/1c/poll?channel={channel}")

    async def poll_command(
        self,
        channel: str = "default",
        *,
        timeout: float = 1,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            response = await self.poll(channel)
            if response.status == 200:
                return response.json()
            assert response.status == 204, response.body.decode("utf-8", "replace")
            await asyncio.sleep(0.01)
        raise AssertionError(f"No command was leased for channel {channel!r}")

    async def result(
        self,
        payload: dict[str, Any],
        channel: str = "default",
    ) -> HttpResult:
        return await self.request(
            "POST", f"/1c/result?channel={channel}", json_body=payload
        )


class McpSession:
    def __init__(self, server: RunningProxy, channel: str = "default") -> None:
        server.require_started()
        self.http = ProxyHttpClient(server)
        self.channel = channel
        self.session_id: str | None = None
        self._next_id = 1

    async def initialize(self) -> dict[str, Any]:
        response = await self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "proxy-acceptance", "version": "1.0"},
            },
        )
        self.session_id = response.headers.get("mcp-session-id") or self.session_id
        payload = decode_mcp(response)
        assert payload["result"]["serverInfo"]["name"]
        initialized = await self.notification("notifications/initialized", {})
        assert initialized.status in {200, 202, 204}
        return payload

    async def tools(self) -> list[dict[str, Any]]:
        response = await self.rpc("tools/list", {})
        payload = decode_mcp(response)
        tools = payload["result"]["tools"]
        assert isinstance(tools, list)
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: int | None = None,
        timeout: float = 2,
    ) -> dict[str, Any]:
        response = await self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            request_id=request_id,
            timeout=timeout,
        )
        return decode_mcp(response)

    def start_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: int | None = None,
        timeout: float = 2,
    ) -> asyncio.Task[dict[str, Any]]:
        return asyncio.create_task(
            self.call_tool(
                name,
                arguments,
                request_id=request_id,
                timeout=timeout,
            )
        )

    async def cancel(self, request_id: int, reason: str = "acceptance") -> HttpResult:
        return await self.notification(
            "notifications/cancelled",
            {"requestId": request_id, "reason": reason},
        )

    async def rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_id: int | None = None,
        timeout: float = 2,
    ) -> HttpResult:
        if request_id is None:
            request_id = self._next_id
            self._next_id += 1
        return await self.http.request(
            "POST",
            f"/mcp?channel={self.channel}",
            json_body={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            headers=self._headers(),
            timeout=timeout,
        )

    async def notification(self, method: str, params: dict[str, Any]) -> HttpResult:
        return await self.http.request(
            "POST",
            f"/mcp?channel={self.channel}",
            json_body={"jsonrpc": "2.0", "method": method, "params": params},
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers


def decode_mcp(response: HttpResult) -> dict[str, Any]:
    assert response.status == 200, response.body.decode("utf-8", "replace")
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        events = []
        for line in response.body.decode("utf-8").splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
        assert events, response.body.decode("utf-8", "replace")
        value = events[-1]
    else:
        value = json.loads(response.body.decode("utf-8"))
    assert isinstance(value, dict)
    return value


def tool_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in payload, payload
    result = payload["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    assert isinstance(content, list) and len(content) == 1
    assert content[0].get("type") == "text"
    value = json.loads(content[0]["text"])
    assert isinstance(value, dict)
    return value


def execute_arguments() -> dict[str, Any]:
    return {
        "query": "ВЫБРАТЬ ПЕРВЫЕ 10 Номенклатура.Ссылка ИЗ Справочник.Номенклатура",
        "params": {},
        "limit": 10,
        "include_schema": True,
    }
