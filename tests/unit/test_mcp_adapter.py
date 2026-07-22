from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from chatbot1c.adapters.mcp import (
    InMemoryMcpTransport,
    LiveMcpTransport,
    McpReadOnlyAdapter,
    McpTransportError,
)
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import ExecuteQueryRequest, GetMetadataRequest


def _request(*, limit: int = 20) -> ExecuteQueryRequest:
    return ExecuteQueryRequest(
        query="ВЫБРАТЬ Номенклатура.Ссылка ИЗ Справочник.Номенклатура",
        params={},
        limit=limit,
        include_schema=True,
    )


def _minimal() -> dict[str, object]:
    return {
        "success": True,
        "data": [{"Код": "001"}],
        "schema": {"columns": [{"name": "Код", "types": ["Строка"]}]},
    }


def test_real_minimal_success_infers_count_and_boundary_completeness() -> None:
    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"execute_query": _minimal()}))
    result = asyncio.run(adapter.execute_query(_request()))
    assert result.success
    assert result.count == 1
    assert not result.truncated
    assert not result.has_more

    boundary = _minimal()
    boundary["data"] = [{"Код": "001"}]
    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"execute_query": boundary}))
    result = asyncio.run(adapter.execute_query(_request(limit=1)))
    assert result.truncated
    assert result.has_more


def test_failure_only_envelope_normalizes_without_success_fields() -> None:
    adapter = McpReadOnlyAdapter(
        InMemoryMcpTransport(
            {"execute_query": {"success": False, "error": "fixture failure"}}
        )
    )
    result = asyncio.run(adapter.execute_query(_request()))
    assert not result.success
    assert result.data == ()
    assert result.schema_.columns == ()
    assert result.count == 0
    assert result.error == "fixture failure"


def test_structured_content_accepts_equivalent_sdk_text_mirror() -> None:
    envelope = _minimal()
    wrapped = {
        "structuredContent": envelope,
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, ensure_ascii=False, indent=2),
            }
        ],
    }
    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"execute_query": wrapped}))
    assert asyncio.run(adapter.execute_query(_request())).count == 1


def test_conflicting_dual_or_ambiguous_text_envelope_is_rejected() -> None:
    envelope = _minimal()
    conflicting = {
        "structuredContent": envelope,
        "content": [{"type": "text", "text": '{"success":false}'}],
    }
    ambiguous = {
        "content": [
            {"type": "text", "text": json.dumps(envelope)},
            {"type": "text", "text": json.dumps(envelope)},
        ]
    }
    for wrapped in (conflicting, ambiguous):
        adapter = McpReadOnlyAdapter(
            InMemoryMcpTransport({"execute_query": wrapped})
        )
        with pytest.raises(ApplicationError) as rejected:
            asyncio.run(adapter.execute_query(_request()))
        assert rejected.value.code == "MCP_ENVELOPE_INVALID"


def test_documented_transport_metadata_is_ignored_but_arbitrary_extra_fails() -> None:
    envelope = {
        **_minimal(),
        "returned": 1,
        "limit": 20,
        "received": 1,
        "configuration": "UT",
        "extension": None,
    }
    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"execute_query": envelope}))
    assert asyncio.run(adapter.execute_query(_request())).count == 1

    envelope["unexpected"] = "not documented"
    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"execute_query": envelope}))
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(adapter.execute_query(_request()))
    assert rejected.value.code == "MCP_ENVELOPE_INVALID"


def test_live_transport_normalizes_nested_sdk_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def broken_client(url: str) -> AsyncIterator[tuple[object, object]]:
        del url
        raise ExceptionGroup("streamable HTTP failed", [RuntimeError("404")])
        yield object(), object()

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamablehttp_client", broken_client
    )
    transport = LiveMcpTransport("http://127.0.0.1:1/mcp", channel="default")
    with pytest.raises(McpTransportError):
        asyncio.run(transport.call_tool("execute_query", {}))


def test_live_transport_does_not_swallow_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def cancelled_client(url: str) -> AsyncIterator[tuple[object, object]]:
        del url
        raise asyncio.CancelledError
        yield object(), object()

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamablehttp_client", cancelled_client
    )
    transport = LiveMcpTransport("http://127.0.0.1:1/mcp", channel="default")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(transport.call_tool("execute_query", {}))


def test_metadata_mode_is_local_and_not_sent_to_toolkit() -> None:
    captured: dict[str, object] = {}

    def metadata(arguments: dict[str, object]) -> dict[str, object]:
        captured.update(arguments)
        return {"success": True, "data": {"objects": []}}

    adapter = McpReadOnlyAdapter(InMemoryMcpTransport({"get_metadata": metadata}))
    result = asyncio.run(
        adapter.get_metadata(
            GetMetadataRequest(
                mode="list",
                meta_type="Справочник",
                name_mask="Номенклатура",
                sections=("attributes",),
                limit=25,
                offset=10,
            )
        )
    )

    assert result.success
    assert "mode" not in captured
    assert captured == {
        "filter": None,
        "meta_type": "Справочник",
        "name_mask": "Номенклатура",
        "attribute_mask": None,
        "sections": ["attributes"],
        "limit": 25,
        "offset": 10,
        "extension_name": None,
    }
