from __future__ import annotations

import asyncio
import json

import pytest

from .support import (
    PROXY_IMPLEMENTATIONS,
    McpSession,
    ProxyHttpClient,
    RunningProxy,
    execute_arguments,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_liveness_is_separate_from_1c_readiness_and_heartbeat(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    http = ProxyHttpClient(proxy_server)
    live = await http.request("GET", "/health/live")
    assert live.status == 200
    assert live.json() == {"status": "live"}

    before = await http.request("GET", "/health/ready?channel=heartbeat")
    assert before.status == 503
    assert before.json()["proxy_ready"] is True
    assert before.json()["one_c_connected"] is False

    assert (await http.poll("heartbeat")).status == 204
    connected = await http.request("GET", "/health/ready?channel=heartbeat")
    assert connected.status == 200
    assert connected.json()["one_c_connected"] is True

    await asyncio.sleep(0.3)
    stale = await http.request("GET", "/health/ready?channel=heartbeat")
    assert stale.status == 503
    assert stale.json()["proxy_ready"] is True
    assert stale.json()["one_c_connected"] is False
    assert (await http.request("GET", "/health/live")).status == 200


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_invalid_and_excess_channels_are_rejected(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    http = ProxyHttpClient(proxy_server)
    assert (await http.poll("bad channel/with spaces")).status == 400
    for channel in ("c1", "c2", "c3", "c4"):
        assert (await http.poll(channel)).status == 204
    assert (await http.poll("c5")).status == 400


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
@pytest.mark.parametrize("case", ["invalid-utf8", "oversized", "too-deep"])
async def test_bounded_json_result_ingress_rejects_invalid_payloads(
    proxy_server: RunningProxy,
    case: str,
) -> None:
    proxy_server.require_started()
    http = ProxyHttpClient(proxy_server)
    headers = {"Content-Type": "application/json"}
    if case == "invalid-utf8":
        content = b'{"id":"x","value":"\xff"}'
    elif case == "oversized":
        content = b'{"padding":"' + b"x" * (16 * 1024 * 1024) + b'"}'
    else:
        nested: object = "leaf"
        for _ in range(34):
            nested = {"child": nested}
        content = json.dumps(nested).encode("utf-8")
    rejected = await http.request(
        "POST",
        "/1c/result?channel=default",
        content=content,
        headers=headers,
    )
    assert rejected.status in {400, 413}


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_row_limit_rejects_result_and_releases_pending_call(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments())
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()
    rows = [{"Код": str(index)} for index in range(1001)]
    rejected = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": rows,
            "schema": {"columns": [{"name": "Код", "types": ["Строка"]}]},
            "count": len(rows),
        }
    )
    assert rejected.status in {413, 422}
    response = await call
    assert "error" in response
    assert "row" in response["error"]["message"].lower()
