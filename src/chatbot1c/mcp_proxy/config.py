"""Environment-only configuration for the local 1C MCP bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProxySettings:
    command_timeout_seconds: float
    heartbeat_seconds: float
    poll_wait_seconds: float
    max_pending_per_channel: int
    max_channels: int
    max_result_bytes: int
    max_rows: int
    max_json_depth: int
    max_json_nodes: int

    @classmethod
    def from_env(cls) -> "ProxySettings":
        return cls(
            command_timeout_seconds=_positive_float(
                "MCP_PROXY_COMMAND_TIMEOUT_SECONDS", 11.0
            ),
            heartbeat_seconds=_positive_float("MCP_PROXY_HEARTBEAT_SECONDS", 15.0),
            poll_wait_seconds=_positive_float("MCP_PROXY_POLL_WAIT_SECONDS", 1.0),
            max_pending_per_channel=_positive_int(
                "MCP_PROXY_MAX_PENDING_PER_CHANNEL", 32
            ),
            max_channels=_positive_int("MCP_PROXY_MAX_CHANNELS", 16),
            max_result_bytes=_positive_int(
                "MCP_PROXY_MAX_RESULT_BYTES", 16 * 1024 * 1024
            ),
            max_rows=_positive_int("MCP_PROXY_MAX_ROWS", 1000),
            max_json_depth=_positive_int("MCP_PROXY_MAX_JSON_DEPTH", 32),
            max_json_nodes=_positive_int("MCP_PROXY_MAX_JSON_NODES", 100_000),
        )


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value
