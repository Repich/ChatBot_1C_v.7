from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient

from chatbot1c.mcp_proxy import state as state_module
from chatbot1c.mcp_proxy.app import create_app
from chatbot1c.mcp_proxy.config import ProxySettings
from chatbot1c.mcp_proxy.events import LOGGER_NAME
from chatbot1c.mcp_proxy.state import BridgeCommand, ProxyState


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _settings(*, heartbeat_seconds: float = 15.0) -> ProxySettings:
    return ProxySettings(
        command_timeout_seconds=1.0,
        heartbeat_seconds=heartbeat_seconds,
        poll_wait_seconds=0.001,
        max_pending_per_channel=8,
        max_channels=4,
        max_result_bytes=1024,
        max_rows=10,
        max_json_depth=8,
        max_json_nodes=100,
    )


def _lifecycle_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER_NAME]


def _event_records(
    records: Iterable[logging.LogRecord], event_name: str
) -> list[logging.LogRecord]:
    return [record for record in records if record.event_name == event_name]


def _captured_record_text(records: Iterable[logging.LogRecord]) -> str:
    return "\n".join(
        f"{record.getMessage()} {record.__dict__!r}" for record in records
    )


def _enqueue(
    state: ProxyState,
    *,
    request_key: str,
    query: str,
    params: dict[str, str] | None = None,
) -> asyncio.Future[BridgeCommand]:
    return asyncio.ensure_future(
        state.enqueue(
            channel="production",
            session_id="session-must-not-be-logged",
            request_key=request_key,
            tool="execute_query",
            params={
                "query": query,
                "params": params or {},
                "limit": 10,
                "include_schema": True,
            },
        )
    )


def test_lifespan_logs_safe_startup_config_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    values = {
        "MCP_PROXY_COMMAND_TIMEOUT_SECONDS": "12.5",
        "MCP_PROXY_HEARTBEAT_SECONDS": "4.5",
        "MCP_PROXY_POLL_WAIT_SECONDS": "0.25",
        "MCP_PROXY_MAX_PENDING_PER_CHANNEL": "7",
        "MCP_PROXY_MAX_CHANNELS": "3",
        "MCP_PROXY_MAX_RESULT_BYTES": "4096",
        "MCP_PROXY_MAX_ROWS": "25",
        "MCP_PROXY_MAX_JSON_DEPTH": "9",
        "MCP_PROXY_MAX_JSON_NODES": "250",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with TestClient(create_app()) as client:
            assert client.get("/health/live").json() == {"status": "live"}

    records = _lifecycle_records(caplog)
    startup = _event_records(records, "mcp_proxy.startup")
    shutdown = _event_records(records, "mcp_proxy.shutdown")
    assert len(startup) == 1
    assert startup[0].status == "ready"
    assert startup[0].command_timeout_seconds == 12.5
    assert startup[0].heartbeat_seconds == 4.5
    assert startup[0].poll_wait_seconds == 0.25
    assert startup[0].max_pending_per_channel == 7
    assert startup[0].max_channels == 3
    assert startup[0].max_result_bytes == 4096
    assert startup[0].max_rows == 25
    assert startup[0].max_json_depth == 9
    assert startup[0].max_json_nodes == 250
    assert len(shutdown) == 1
    assert shutdown[0].status == "complete"
    assert shutdown[0].channel_count == 0
    assert shutdown[0].active_commands == 0


def test_command_logs_success_and_query_error_without_query_params_or_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    query_canary = "QUERY_CANARY_7E12 SELECT SecretField FROM SecretTable"
    param_canary = "PARAM_CANARY_B948"
    row_canary = "ROW_CANARY_31AF"
    error_canary = "QUERY_ERROR_CANARY_669C"

    async def exercise() -> tuple[BridgeCommand, BridgeCommand]:
        state = ProxyState(_settings())
        success = await _enqueue(
            state,
            request_key="request-1",
            query=query_canary,
            params={"needle": param_canary},
        )
        assert await state.lease("production") is success
        assert await state.submit_result(
            "production",
            success.command_id,
            {
                "success": True,
                "data": [{"SecretField": row_canary}],
                "schema": {
                    "columns": [{"name": "SecretField", "types": ["string"]}]
                },
                "count": 1,
            },
        )

        query_error = await _enqueue(
            state,
            request_key="request-2",
            query=f"{query_canary} ERROR_VARIANT",
        )
        assert await state.lease("production") is query_error
        assert await state.submit_result(
            "production",
            query_error.command_id,
            {"success": False, "error": error_canary},
        )
        await state.shutdown()
        return success, query_error

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        success, query_error = asyncio.run(exercise())

    records = _lifecycle_records(caplog)
    for command in (success, query_error):
        command_records = [
            record
            for record in records
            if getattr(record, "command_id", None) == command.command_id
        ]
        assert [record.event_name for record in command_records] == [
            "mcp_proxy.command.queued",
            "mcp_proxy.command.leased",
            "mcp_proxy.command.completed",
        ]
        assert all(record.channel == "production" for record in command_records)
        assert all(record.tool == "execute_query" for record in command_records)
        assert all(type(record.elapsed_ms) is int for record in command_records)
        assert all(record.elapsed_ms >= 0 for record in command_records)

    completed = _event_records(records, "mcp_proxy.command.completed")
    assert [record.status for record in completed] == ["success", "query_error"]
    captured = _captured_record_text(records)
    for canary in (query_canary, param_canary, row_canary, error_canary):
        assert canary not in captured
    assert "session-must-not-be-logged" not in captured
    assert "request-1" not in captured
    assert "request-2" not in captured


def test_rejected_expired_cancelled_and_shutdown_commands_are_observable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    invalid_result_canary = "INVALID_RESULT_CANARY_9D44"
    cancellation_canary = "CANCEL_REASON_CANARY_2AC8"

    async def exercise() -> tuple[BridgeCommand, BridgeCommand, BridgeCommand]:
        state = ProxyState(_settings())
        rejected = await _enqueue(
            state,
            request_key="rejected-request",
            query="SELECT RejectedResult FROM Table",
        )
        assert await state.lease("production") is rejected
        assert await state.fail_result(
            "production", rejected.command_id, invalid_result_canary
        )

        expired = await _enqueue(
            state,
            request_key="expired-request",
            query="SELECT ExpiredResult FROM Table",
        )
        assert await state.expire(expired)

        cancelled = await _enqueue(
            state,
            request_key="cancelled-request",
            query="SELECT CancelledResult FROM Table",
        )
        assert await state.cancel(cancelled, cancellation_canary)

        await _enqueue(
            state,
            request_key="shutdown-request",
            query="SELECT ShutdownResult FROM Table",
        )
        await state.shutdown()
        return rejected, expired, cancelled

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        rejected, expired, cancelled = asyncio.run(exercise())

    records = _lifecycle_records(caplog)
    rejected_record = _event_records(records, "mcp_proxy.result.rejected")
    assert len(rejected_record) == 1
    assert rejected_record[0].command_id == rejected.command_id
    assert rejected_record[0].status == "invalid_result"

    expired_record = _event_records(records, "mcp_proxy.command.expired")
    assert len(expired_record) == 1
    assert expired_record[0].command_id == expired.command_id
    assert expired_record[0].status == "expired"

    cancelled_records = _event_records(records, "mcp_proxy.command.cancelled")
    assert len(cancelled_records) == 2
    assert cancelled_records[0].command_id == cancelled.command_id
    assert all(record.status == "cancelled" for record in cancelled_records)

    shutdown_record = _event_records(records, "mcp_proxy.shutdown")
    assert len(shutdown_record) == 1
    assert shutdown_record[0].status == "complete"
    assert shutdown_record[0].active_commands == 1
    assert invalid_result_canary not in _captured_record_text(records)
    assert cancellation_canary not in _captured_record_text(records)


def test_heartbeat_logs_only_connection_transitions_not_empty_polls(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock(100.0)
    monkeypatch.setattr(state_module, "time", clock)

    async def exercise() -> None:
        state = ProxyState(_settings(heartbeat_seconds=5.0))
        command = await _enqueue(
            state,
            request_key="heartbeat-request-1",
            query="SELECT HeartbeatOne FROM Table",
        )
        clock.now = 101.0
        assert await state.lease("production") is command
        clock.now = 102.0
        assert await state.readiness("production")
        clock.now = 107.0
        assert not await state.readiness("production")

        command = await _enqueue(
            state,
            request_key="heartbeat-request-2",
            query="SELECT HeartbeatTwo FROM Table",
        )
        clock.now = 108.0
        assert await state.lease("production") is command
        clock.now = 109.0
        assert await state.lease("production") is None
        clock.now = 110.0
        assert await state.lease("production") is None
        await state.shutdown()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        asyncio.run(exercise())

    heartbeat = _event_records(
        _lifecycle_records(caplog), "mcp_proxy.channel.heartbeat"
    )
    assert [record.status for record in heartbeat] == [
        "connected",
        "disconnected",
        "connected",
    ]
    assert not hasattr(heartbeat[0], "elapsed_ms")
    assert heartbeat[1].elapsed_ms == 6000
    assert heartbeat[2].elapsed_ms == 7000
