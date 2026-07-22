from __future__ import annotations

import asyncio
from typing import Any

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
async def test_initialize_publishes_exact_read_only_tool_surface(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    initialized = await session.initialize()
    assert initialized["result"]["protocolVersion"]

    tools = await session.tools()
    assert {tool["name"] for tool in tools} == {"execute_query", "get_metadata"}
    assert len(tools) == 2
    metadata = next(tool for tool in tools if tool["name"] == "get_metadata")
    metadata_schema = metadata["inputSchema"]
    assert metadata_schema["additionalProperties"] is False
    assert "mode" not in metadata_schema.get("properties", {})


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
@pytest.mark.parametrize(
    "result_envelope",
    [
        pytest.param(
            {
                "success": True,
                "data": [
                    {
                        "Наименование": 'Куртка "Север"',
                        "Цена": None,
                        "Ссылка": {
                            "_objectRef": True,
                            "УникальныйИдентификатор": (
                                "dca2b724-4df1-11ed-b81e-001dd8b71dca"
                            ),
                            "ТипОбъекта": "СправочникСсылка.Номенклатура",
                            "Представление": 'Куртка "Север"',
                        },
                    }
                ],
                "schema": {
                    "columns": [
                        {"name": "Наименование", "types": ["Строка"]},
                        {"name": "Цена", "types": ["Число", "Null"]},
                        {
                            "name": "Ссылка",
                            "types": ["СправочникСсылка.Номенклатура"],
                        },
                    ]
                },
                "count": 1,
            },
            id="rows-null-object-ref-utf8",
        ),
        pytest.param(
            {
                "success": True,
                "data": [],
                "schema": {"columns": []},
                "count": 0,
            },
            id="confirmed-empty",
        ),
        pytest.param(
            {
                "success": False,
                "error": "Ошибка выполнения запроса 1С",
            },
            id="query-error",
        ),
    ],
)
async def test_tool_call_round_trips_observable_1c_envelopes(
    proxy_server: RunningProxy,
    result_envelope: dict[str, Any],
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments())

    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()
    assert command["tool"] == "execute_query"
    assert command["params"] == execute_arguments()
    accepted = await bridge.result({"id": command["id"], **result_envelope})
    assert accepted.status == 200

    response = await call
    public_envelope = tool_envelope(response)
    assert public_envelope == result_envelope
    assert "id" not in public_envelope
    assert response["result"]["isError"] is (result_envelope["success"] is False)


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_metadata_local_mode_never_reaches_1c_wire(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call(
        "get_metadata",
        {
            "filter": None,
            "meta_type": "Справочник",
            "name_mask": "Номенклатура",
            "attribute_mask": None,
            "sections": ["attributes"],
            "limit": 25,
            "offset": 0,
            "extension_name": None,
        },
    )

    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()
    assert command["tool"] == "get_metadata"
    assert "mode" not in command["params"]
    result = {
        "success": True,
        "data": {"objects": [{"name": "Номенклатура"}]},
    }
    accepted = await bridge.result({"id": command["id"], **result})
    assert accepted.status == 200
    assert tool_envelope(await call) == result


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_channels_are_isolated_for_poll_and_result(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    alpha = McpSession(proxy_server, "alpha")
    beta = McpSession(proxy_server, "beta")
    await asyncio.gather(alpha.initialize(), beta.initialize())
    call = alpha.start_tool_call("execute_query", execute_arguments())

    bridge = ProxyHttpClient(proxy_server)
    wrong_channel = await bridge.poll("beta")
    assert wrong_channel.status == 204
    command = await bridge.poll_command("alpha")
    wrong_result = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [],
            "schema": {"columns": []},
            "count": 0,
        },
        "beta",
    )
    assert wrong_result.status == 409
    accepted = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [],
            "schema": {"columns": []},
            "count": 0,
        },
        "alpha",
    )
    assert accepted.status == 200
    assert tool_envelope(await call)["count"] == 0


@pytest.mark.parametrize("proxy_server", PROXY_IMPLEMENTATIONS, indirect=True)
async def test_malformed_schema_is_rejected_and_fails_the_pending_call(
    proxy_server: RunningProxy,
) -> None:
    proxy_server.require_started()
    session = McpSession(proxy_server)
    await session.initialize()
    call = session.start_tool_call("execute_query", execute_arguments())
    bridge = ProxyHttpClient(proxy_server)
    command = await bridge.poll_command()

    rejected = await bridge.result(
        {
            "id": command["id"],
            "success": True,
            "data": [{"Код": "001"}],
            "schema": {"columns": [{"name": "Наименование"}]},
            "count": 1,
        }
    )
    assert rejected.status == 422
    response = await call
    assert "error" in response
    assert "schema" in response["error"]["message"].lower()
    assert (await bridge.poll()).status == 204
