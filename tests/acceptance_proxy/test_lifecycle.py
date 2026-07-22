from __future__ import annotations

import asyncio

import httpx
import pytest

from .support import (
    PROXY_IMPLEMENTATIONS,
    McpSession,
    ProxyHttpClient,
    RunningProxy,
    execute_arguments,
    tool_envelope,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_transport_timeout_expires_and_removes_command(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments())
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()
    response = await call
    assert "error" in response
    assert "transport" in response["error"]["message"].lower()
    late = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [],
            "schema": {"columns": []},
            "count": 0,
        }
    )
    assert late.status == 409
    assert (await bridge.poll()).status == 204


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_mcp_cancellation_removes_command_and_rejects_late_result(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    request_id = 77
    call = session.start_tool_call(
        "execute_query", execute_arguments(), request_id=request_id
    )
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()

    cancelled = await session.cancel(request_id)
    assert cancelled.status in {200, 202, 204}
    response = await call
    assert "error" in response
    assert "cancel" in response["error"]["message"].lower()
    late = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [],
            "schema": {"columns": []},
            "count": 0,
        }
    )
    assert late.status == 409


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_duplicate_and_unknown_results_are_rejected(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments())
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()
    payload = {
        "id": command["id"],
        "success": True,
        "data": [],
        "schema": {"columns": []},
        "count": 0,
    }
    assert (await bridge.result(payload)).status == 200
    assert tool_envelope(await call)["success"] is True
    assert (await bridge.result(payload)).status == 409
    assert (await bridge.result({**payload, "id": "unknown-command"})).status == 409


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_restart_does_not_resurrect_leased_command(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments(), timeout=1)
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()

    proxy_server.restart()
    proxy_server.require_started()
    try:
        interrupted = await call
    except (httpx.HTTPError, asyncio.CancelledError):
        interrupted = None
    if interrupted is not None:
        assert "error" in interrupted
        message = interrupted["error"]["message"].lower()
        assert "transport" in message or "unavailable" in message
    fresh_bridge = ProxyHttpClient(proxy_server)
    late = await fresh_bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [],
            "schema": {"columns": []},
            "count": 0,
        }
    )
    assert late.status == 409


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_pending_limit_is_fail_closed(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    active = [
        session.start_tool_call("execute_query", execute_arguments()) for _ in range(4)
    ]
    await asyncio.sleep(0.05)
    overflow = await session.call_tool("execute_query", execute_arguments())
    assert "error" in overflow
    assert "limit" in overflow["error"]["message"].lower()
    completed = await asyncio.gather(*active)
    assert all("error" in response for response in completed)
