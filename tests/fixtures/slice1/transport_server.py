"""Independent HTTP fixture transport for slice 1 black-box acceptance tests."""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return value


class FixtureState:
    def __init__(self) -> None:
        self.spec = _load_json(FIXTURE_DIR / "transport_scenarios.json")
        self.deepseek_responses = {
            "valid": _load_json(
                REPO_ROOT / "tests/fixtures/deepseek/valid_planner_response.json"
            ),
            "malformed": _load_json(
                REPO_ROOT / "tests/fixtures/deepseek/malformed_json_response.json"
            ),
            "schema_invalid": _load_json(
                REPO_ROOT
                / "tests/fixtures/deepseek/schema_invalid_planner_response.json"
            ),
            "context_execute": self._planner_response(
                "planner_q037_context_execute.json", "synthetic-chatcmpl-context"
            ),
            "missing_context": self._planner_response(
                "planner_q037_missing_context.json", "synthetic-chatcmpl-clarify"
            ),
        }
        self.lock = threading.Lock()
        self.scenario = "q011"
        self.delay_ms: int | None = None
        self.deepseek_content: str | None = None
        self.requests: list[dict[str, Any]] = []

    def _planner_response(self, name: str, response_id: str) -> dict[str, Any]:
        planner = _load_json(FIXTURE_DIR / name)
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": 1893456000,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            planner, ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        }

    def reset(self) -> None:
        with self.lock:
            self.scenario = "q011"
            self.delay_ms = None
            self.deepseek_content = None
            self.requests.clear()

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        modes = self.spec["scenario_modes"]
        if name not in modes:
            raise ValueError(f"Unknown fixture scenario: {name!r}")

        delay_ms = payload.get("delay_ms")
        if delay_ms is not None and (
            not isinstance(delay_ms, int) or not 0 <= delay_ms < 30_000
        ):
            raise ValueError("delay_ms must be an integer from 0 through 29999")

        deepseek_content = payload.get("deepseek_content")
        if deepseek_content is not None and not isinstance(deepseek_content, str):
            raise ValueError("deepseek_content must be a string")

        with self.lock:
            self.scenario = name
            self.delay_ms = delay_ms
            self.deepseek_content = deepseek_content
            if payload.get("clear_requests", True):
                self.requests.clear()
        return self.snapshot()

    def record(self, *, boundary: str, path: str, headers: Any, body: Any) -> None:
        # Header values are deliberately excluded so bearer tokens and cookies cannot leak.
        record = {
            "boundary": boundary,
            "path": path,
            "header_names": sorted(name.lower() for name in headers.keys()),
            "body": copy.deepcopy(body),
        }
        with self.lock:
            self.requests.append(record)

    def mode(self) -> dict[str, Any]:
        with self.lock:
            configured = copy.deepcopy(self.spec["scenario_modes"][self.scenario])
            if self.delay_ms is not None:
                configured["delay_ms"] = self.delay_ms
            return configured

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "fixture_version": self.spec["fixture_version"],
                "scenario": self.scenario,
                "delay_ms": self.delay_ms,
                "requests": copy.deepcopy(self.requests),
                "object_refs": copy.deepcopy(self.spec["object_refs"]),
            }

    def deepseek_response(self) -> dict[str, Any]:
        mode = self.mode()["deepseek"]
        response = copy.deepcopy(self.deepseek_responses[mode])
        with self.lock:
            content = self.deepseek_content
        if content is not None:
            response["choices"][0]["message"]["content"] = content
        return response

    def mcp_envelope(self) -> dict[str, Any] | None:
        mode = self.mode()["mcp"]
        if mode == "malformed":
            return None
        return copy.deepcopy(self.spec["mcp_envelopes"][mode])


class FixtureHandler(BaseHTTPRequestHandler):
    server: "FixtureServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("Request body must be a JSON object")
        return value

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _delay(self) -> None:
        delay_ms = self.server.state.mode()["delay_ms"]
        if delay_ms:
            time.sleep(delay_ms / 1000)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/__fixture__/health":
            self._send_json(HTTPStatus.OK, {"status": "ready"})
            return
        if self.path == "/__fixture__/state":
            self._send_json(HTTPStatus.OK, self.server.state.snapshot())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "fixture_route_not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._read_json()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if self.path == "/__fixture__/reset":
            self.server.state.reset()
            self._send_json(HTTPStatus.OK, self.server.state.snapshot())
            return
        if self.path == "/__fixture__/scenario":
            try:
                snapshot = self.server.state.configure(body)
            except ValueError as exc:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, snapshot)
            return
        if self.path in {"/chat/completions", "/v1/chat/completions"}:
            self.server.state.record(
                boundary="deepseek", path=self.path, headers=self.headers, body=body
            )
            self._delay()
            self._send_json(HTTPStatus.OK, self.server.state.deepseek_response())
            return
        if self.path == "/mcp":
            self.server.state.record(
                boundary="mcp", path=self.path, headers=self.headers, body=body
            )
            self._delay()
            self._handle_mcp(body)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "fixture_route_not_found"})

    def _handle_mcp(self, request: dict[str, Any]) -> None:
        request_id = request.get("id")
        method = request.get("method")
        if method == "notifications/initialized":
            self._send_empty(HTTPStatus.ACCEPTED)
            return
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "slice1-fixture", "version": "1.0.0"},
            }
            self._send_json(
                HTTPStatus.OK,
                {"jsonrpc": "2.0", "id": request_id, "result": result},
            )
            return
        if method == "tools/list":
            tools = [
                {
                    "name": "execute_query",
                    "description": "Execute a fixed read-only fixture query.",
                    "inputSchema": {
                        "type": "object",
                        "required": ["query", "params", "limit", "include_schema"],
                        "additionalProperties": False,
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
                    "description": "Return the synthetic fixture metadata profile.",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                },
            ]
            self._send_json(
                HTTPStatus.OK,
                {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}},
            )
            return
        if method != "tools/call":
            self._mcp_error(request_id, -32601, "Method not found")
            return

        params = request.get("params")
        if not isinstance(params, dict):
            self._mcp_error(request_id, -32602, "Invalid params")
            return
        tool_name = params.get("name")
        if tool_name == "get_metadata":
            structured = copy.deepcopy(self.server.state.spec["metadata_envelope"])
        elif tool_name == "execute_query":
            structured = self.server.state.mcp_envelope()
            if structured is None:
                ambiguous = {
                    "content": [
                        {"type": "text", "text": "{\"success\":true,\"data\":[]}"},
                        {"type": "text", "text": "{\"success\":false}"},
                    ]
                }
                self._send_json(
                    HTTPStatus.OK,
                    {"jsonrpc": "2.0", "id": request_id, "result": ambiguous},
                )
                return
        else:
            self._mcp_error(request_id, -32602, "Unknown tool")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"structuredContent": structured},
            },
        )

    def _mcp_error(self, request_id: Any, code: int, message: str) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
        )


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: FixtureState) -> None:
        super().__init__(address, FixtureHandler)
        self.state = state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    server = FixtureServer((args.host, args.port), FixtureState())
    host, port = server.server_address
    print(
        json.dumps({"base_url": f"http://{host}:{port}"}, separators=(",", ":")),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
