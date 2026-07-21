"""Dynamic planner facade over the independent slice-1 MCP fixture server."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "tests/fixtures/slice1/transport_server.py"


def _load_transport() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "slice1_independent_transport", TRANSPORT
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("independent fixture transport cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


TRANSPORT_MODULE = _load_transport()


class DynamicFixtureHandler(TRANSPORT_MODULE.FixtureHandler):  # type: ignore[misc]
    """Accept the production MCP channel query without changing owned fixtures."""

    server: "DynamicFixtureServer"

    def _send_json(self, status: int, payload: Any) -> None:
        if isinstance(payload, dict):
            result = payload.get("result")
            if (
                isinstance(result, dict)
                and isinstance(result.get("structuredContent"), dict)
                and "content" not in result
            ):
                structured = result["structuredContent"]
                payload = copy.deepcopy(payload)
                payload["result"]["content"] = [
                    {
                        "type": "text",
                        "text": json.dumps(
                            structured,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                ]
        super()._send_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path != "/mcp":
            super().do_POST()
            return
        if parse_qs(parsed.query).get("channel") != ["default"]:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "fixture_requires_channel_default"},
            )
            return
        original_path = self.path
        self.path = parsed.path
        try:
            super().do_POST()
        finally:
            self.path = original_path


class DynamicFixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], state: "DynamicFixtureState"
    ) -> None:
        super().__init__(address, DynamicFixtureHandler)
        self.state = state


class DynamicFixtureState(TRANSPORT_MODULE.FixtureState):  # type: ignore[misc]
    """Hydrates trusted echo/handles while retaining independent MCP scenarios."""

    def deepseek_response(self) -> dict[str, Any]:
        response = cast(dict[str, Any], super().deepseek_response())
        message = response.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            return response
        try:
            plan = json.loads(content)
        except json.JSONDecodeError:
            return response
        if not isinstance(plan, dict) or plan.get("document_type") != "planner_output":
            return response
        request_payload = self._latest_planner_payload()
        if request_payload is None:
            return response
        hydrated = _hydrate_plan(plan, request_payload)
        message["content"] = json.dumps(
            hydrated, ensure_ascii=False, separators=(",", ":")
        )
        return response

    def _latest_planner_payload(self) -> dict[str, Any] | None:
        with self.lock:
            requests = copy.deepcopy(self.requests)
        for request in reversed(requests):
            if request.get("boundary") != "deepseek":
                continue
            body = request.get("body")
            if not isinstance(body, dict):
                continue
            messages = body.get("messages")
            if not isinstance(messages, list):
                continue
            for message in reversed(messages):
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and "expected_echo" in payload:
                    return cast(dict[str, Any], payload)
        return None

    def mcp_envelope(self) -> dict[str, Any] | None:
        envelope = super().mcp_envelope()
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            return envelope
        _complete_canonical_projection(envelope)
        return cast(dict[str, Any], envelope)


def _hydrate_plan(plan: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = copy.deepcopy(plan)
    echo = payload.get("expected_echo")
    if not isinstance(echo, dict):
        return hydrated
    hydrated["request_id"] = echo["request_id"]
    hydrated["session_context_version"] = echo["session_context_version"]
    hydrated["catalog_snapshot_id"] = echo["catalog_snapshot_id"]
    hydrated["catalog_revision"] = echo["catalog_revision"]

    cards = payload.get("skill_manifest")
    versions = {
        card["skill_id"]: card["version"]
        for card in cards
        if isinstance(card, dict)
        and isinstance(card.get("skill_id"), str)
        and isinstance(card.get("version"), str)
    } if isinstance(cards, list) else {}
    result = hydrated.get("result")
    if isinstance(result, dict):
        for step in result.get("steps", []):
            if isinstance(step, dict) and step.get("skill_id") in versions:
                step["skill_version"] = versions[step["skill_id"]]

    context = payload.get("context")
    confirmed = context.get("confirmed_facts", []) if isinstance(context, dict) else []
    handles = {
        fact["semantic_type"]: fact["handle"]
        for fact in confirmed
        if isinstance(fact, dict)
        and isinstance(fact.get("semantic_type"), str)
        and isinstance(fact.get("handle"), str)
    }
    _hydrate_context_bindings(hydrated, handles)
    return hydrated


def _hydrate_context_bindings(value: object, handles: dict[str, str]) -> None:
    if isinstance(value, dict):
        if value.get("source") == "context":
            semantic_type = value.get("expected_semantic_type")
            if isinstance(semantic_type, str) and semantic_type in handles:
                value["context_handle"] = handles[semantic_type]
        for child in value.values():
            _hydrate_context_bindings(child, handles)
    elif isinstance(value, list):
        for child in value:
            _hydrate_context_bindings(child, handles)


def _complete_canonical_projection(envelope: dict[str, Any]) -> None:
    rows = envelope.get("data")
    schema = envelope.get("schema")
    if not isinstance(rows, list) or not isinstance(schema, dict):
        return
    columns = schema.get("columns")
    if not isinstance(columns, list):
        return
    present = {
        column.get("name")
        for column in columns
        if isinstance(column, dict) and isinstance(column.get("name"), str)
    }

    def ensure(name: str, mcp_type: str, values: list[Any]) -> None:
        if name not in present:
            columns.append({"name": name, "types": [mcp_type]})
            present.add(name)
        for row, value in zip(rows, values, strict=True):
            if isinstance(row, dict):
                row.setdefault(name, value)

    if rows and all(isinstance(row, dict) and "Артикул" in row for row in rows):
        ensure("Код", "Строка", [f"SYNTHETIC-{index:03d}" for index in range(1, len(rows) + 1)])
    if rows and all(
        isinstance(row, dict) and "Заказ" in row and "Статус" in row
        for row in rows
    ):
        ensure("Номер", "Строка", ["0000-000005"] * len(rows))
        ensure("Дата", "Дата", ["2026-01-15T10:00:00+03:00"] * len(rows))
        ensure("Проведен", "Булево", [True] * len(rows))
    if rows and all(
        isinstance(row, dict) and "Заказ" in row and "Количество" in row
        for row in rows
    ):
        ensure("НомерСтроки", "Число", list(range(1, len(rows) + 1)))
        ensure("Цена", "Число", [100.0] * len(rows))
        ensure("Валюта", "Строка", ["RUB"] * len(rows))
        ensure(
            "Сумма",
            "Число",
            [
                float(cast(dict[str, Any], row).get("Количество", 0)) * 100.0
                for row in rows
            ],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = DynamicFixtureServer((args.host, args.port), DynamicFixtureState())
    host, port = server.server_address
    print(json.dumps({"base_url": f"http://{host}:{port}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
