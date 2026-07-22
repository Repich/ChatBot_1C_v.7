"""Safe structured lifecycle events for the local MCP proxy."""

from __future__ import annotations

import json
import logging
from typing import Final, TypeAlias

# The proxy is always hosted by Uvicorn in the supported launcher. A child of
# its error logger inherits the configured runtime handler and level, while
# retaining a distinct name for structured filtering and tests.
LOGGER_NAME: Final = "uvicorn.error.chatbot1c.mcp_proxy.lifecycle"

STARTUP_EVENT: Final = "mcp_proxy.startup"
HEARTBEAT_EVENT: Final = "mcp_proxy.channel.heartbeat"
COMMAND_QUEUED_EVENT: Final = "mcp_proxy.command.queued"
COMMAND_LEASED_EVENT: Final = "mcp_proxy.command.leased"
COMMAND_COMPLETED_EVENT: Final = "mcp_proxy.command.completed"
RESULT_REJECTED_EVENT: Final = "mcp_proxy.result.rejected"
COMMAND_EXPIRED_EVENT: Final = "mcp_proxy.command.expired"
COMMAND_CANCELLED_EVENT: Final = "mcp_proxy.command.cancelled"
SHUTDOWN_EVENT: Final = "mcp_proxy.shutdown"

_EVENT_NAMES: Final = frozenset(
    {
        STARTUP_EVENT,
        HEARTBEAT_EVENT,
        COMMAND_QUEUED_EVENT,
        COMMAND_LEASED_EVENT,
        COMMAND_COMPLETED_EVENT,
        RESULT_REJECTED_EVENT,
        COMMAND_EXPIRED_EVENT,
        COMMAND_CANCELLED_EVENT,
        SHUTDOWN_EVENT,
    }
)
_FIELD_NAMES: Final = frozenset(
    {
        "active_commands",
        "channel",
        "channel_count",
        "command_id",
        "command_timeout_seconds",
        "elapsed_ms",
        "heartbeat_seconds",
        "max_channels",
        "max_json_depth",
        "max_json_nodes",
        "max_pending_per_channel",
        "max_result_bytes",
        "max_rows",
        "poll_wait_seconds",
        "status",
        "tool",
    }
)

LogField: TypeAlias = str | int | float | bool

_logger = logging.getLogger(LOGGER_NAME)


def log_event(
    event_name: str, *, level: int = logging.INFO, **fields: LogField
) -> None:
    """Emit an allowlisted event without accepting payload-shaped values."""
    if event_name not in _EVENT_NAMES:
        raise ValueError(f"unknown lifecycle event: {event_name}")
    unknown_fields = fields.keys() - _FIELD_NAMES
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unsafe lifecycle fields: {names}")
    if any(type(value) not in {str, int, float, bool} for value in fields.values()):
        raise TypeError("lifecycle fields must be scalar values")
    wire = {"event": event_name, **fields}
    message = json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _logger.log(level, message, extra={"event_name": event_name, **fields})
