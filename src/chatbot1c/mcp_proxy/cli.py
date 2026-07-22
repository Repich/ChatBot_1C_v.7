"""Command-line launcher for the local 1C MCP proxy."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chatbot1c-mcp-proxy")
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_PROXY_HOST", "127.0.0.1"),
        help="HTTP bind host; keep 127.0.0.1 unless 1C runs on another machine",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_port_from_env(),
        help="HTTP port used by both ChatBot and the 1C processing",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default=os.getenv("MCP_PROXY_LOG_LEVEL", "info").casefold(),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    uvicorn.run(
        "chatbot1c.mcp_proxy:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        log_level=args.log_level,
    )
    return 0


def _port_from_env() -> int:
    raw = os.getenv("MCP_PROXY_PORT", "6003")
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError("MCP_PROXY_PORT must be an integer") from error


if __name__ == "__main__":  # pragma: no cover - module launcher boundary
    raise SystemExit(main())
