from __future__ import annotations

import argparse
import copy
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

ASSET_SKILL = "qa.synthetic.asset.resolve"
DETAIL_SKILL = "qa.synthetic.asset.snapshot"
SET_SKILL = "qa.synthetic.asset.batch"
FIXED_MOMENT = "2037-11-19T08:17:43.123456+03:00"
PHYSICAL_TYPE = "СправочникСсылка.СинтетическийАктив"


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scenario = "display"
        self.options: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.deepseek_ordinal = 0
        self.query_ordinal = 0

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("fixture scenario name is required")
        with self.lock:
            self.scenario = name
            self.options = {
                key: copy.deepcopy(value)
                for key, value in payload.items()
                if key not in {"name", "clear_requests"}
            }
            self.deepseek_ordinal = 0
            self.query_ordinal = 0
            if payload.get("clear_requests", True):
                self.requests.clear()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "fixture_version": "slice3-independent-v1",
                "scenario": self.scenario,
                "options": copy.deepcopy(self.options),
                "requests": copy.deepcopy(self.requests),
                "deepseek_ordinal": self.deepseek_ordinal,
                "query_ordinal": self.query_ordinal,
            }

    def option(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return copy.deepcopy(self.options.get(key, default))

    def record(self, kind: str, body: Any, **extra: Any) -> None:
        with self.lock:
            self.requests.append(
                {"kind": kind, "body": copy.deepcopy(body), **copy.deepcopy(extra)}
            )

    def next_deepseek(self) -> int:
        with self.lock:
            self.deepseek_ordinal += 1
            return self.deepseek_ordinal

    def next_query(self) -> int:
        with self.lock:
            self.query_ordinal += 1
            return self.query_ordinal


class Handler(BaseHTTPRequestHandler):
    server: "FixtureServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/__fixture__/health":
            self._send_json(HTTPStatus.OK, {"status": "ready"})
        elif path == "/__fixture__/state":
            self._send_json(HTTPStatus.OK, self.server.state.snapshot())
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._read_json()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        path = urlsplit(self.path).path
        if path == "/__fixture__/scenario":
            try:
                snapshot = self.server.state.configure(body)
            except ValueError as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return
            self._send_json(HTTPStatus.OK, snapshot)
        elif path in {"/chat/completions", "/v1/chat/completions"}:
            self._deepseek(body)
        elif path == "/mcp":
            self._mcp(body)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _deepseek(self, body: dict[str, Any]) -> None:
        ordinal = self.server.state.next_deepseek()
        planner_input = _planner_input(body)
        self.server.state.record(
            "deepseek", body, ordinal=ordinal, planner_input=planner_input
        )
        output = _planner_output(planner_input, self.server.state, ordinal)
        content = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        self._send_json(
            HTTPStatus.OK,
            {
                "id": f"slice3-{ordinal}",
                "object": "chat.completion",
                "created": 1893456000,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 100,
                    "total_tokens": 200,
                },
            },
        )

    def _mcp(self, body: dict[str, Any]) -> None:
        method = body.get("method")
        request_id = body.get("id")
        if method == "initialize":
            self.server.state.record("mcp_initialize", body)
            self._send_json(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "slice3-fixture", "version": "1"},
                    },
                },
            )
            return
        if method == "notifications/initialized":
            self.server.state.record("mcp_initialized", body)
            self._send_empty(HTTPStatus.ACCEPTED)
            return
        if method == "tools/list":
            self.server.state.record("mcp_tools_list", body)
            self._send_json(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "execute_query",
                                "description": "Synthetic read-only query",
                                "inputSchema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "query",
                                        "params",
                                        "limit",
                                        "include_schema",
                                    ],
                                    "properties": {
                                        "query": {"type": "string"},
                                        "params": {"type": "object"},
                                        "limit": {"type": "integer"},
                                        "include_schema": {"const": True},
                                    },
                                },
                            },
                            {
                                "name": "get_metadata",
                                "description": "Synthetic metadata",
                                "inputSchema": {"type": "object"},
                            },
                        ]
                    },
                },
            )
            return
        if method != "tools/call":
            self._rpc_error(request_id, -32601, "Method not found")
            return
        params = body.get("params")
        if not isinstance(params, dict):
            self._rpc_error(request_id, -32602, "Invalid params")
            return
        name = params.get("name")
        arguments = params.get("arguments")
        if name == "get_metadata":
            self.server.state.record("mcp_get_metadata", body)
            self._tool_result(request_id, {"success": True, "data": {}})
            return
        if name != "execute_query" or not isinstance(arguments, dict):
            self._rpc_error(request_id, -32602, "Unknown tool")
            return
        ordinal = self.server.state.next_query()
        parameter_bytes = {
            key: json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            .encode("utf-8")
            .hex()
            for key, value in arguments.get("params", {}).items()
        }
        self.server.state.record(
            "mcp_execute_query",
            body,
            ordinal=ordinal,
            arguments=arguments,
            parameter_bytes=parameter_bytes,
        )
        self._tool_result(request_id, _query_envelope(arguments, self.server.state))

    def _tool_result(self, request_id: Any, envelope: dict[str, Any]) -> None:
        text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        result = {
            "structuredContent": envelope,
            "content": [{"type": "text", "text": text}],
        }
        self._send_json(
            HTTPStatus.OK,
            {"jsonrpc": "2.0", "id": request_id, "result": result},
        )

    def _rpc_error(self, request_id: Any, code: int, message: str) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise TypeError("body must be object")
        return value

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, Handler)
        self.state = FixtureState()


def _planner_input(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return {}
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "expected_echo" in value:
            return value
    return {}


def _planner_output(
    payload: dict[str, Any], state: FixtureState, ordinal: int
) -> dict[str, Any]:
    echo = payload.get("expected_echo")
    manifest = payload.get("skill_manifest")
    if not isinstance(echo, dict) or not isinstance(manifest, list):
        raise ValueError("production planner payload missing")
    cards = {
        card["skill_id"]: card
        for card in manifest
        if isinstance(card, dict) and isinstance(card.get("skill_id"), str)
    }
    resolver = _card(cards, ASSET_SKILL)
    detail = _card(cards, DETAIL_SKILL)
    batch = _card(cards, SET_SKILL)
    mode = state.option("plan_kind", state.scenario)

    if mode in {"interpretation_resume", "interpretation_optional_bypass"}:
        return _interpretation_plan(
            payload,
            echo,
            resolver,
            ordinal=ordinal,
            optional_bypass=mode == "interpretation_optional_bypass",
        )

    if mode in {"display", "select_only"}:
        steps = [
            _call(
                "s1", resolver, [_literal("name_fragment", "normalized_text", "лазур")]
            )
        ]
        finals = _finals("s1", resolver)
    elif mode == "resolve_one":
        steps = [
            _call(
                "s1", resolver, [_literal("name_fragment", "normalized_text", "лазур")]
            ),
            _call(
                "s2",
                detail,
                [
                    _step("asset", "s1", "asset.ref", "one"),
                    _literal("moment", "datetime", FIXED_MOMENT),
                ],
            ),
        ]
        finals = _finals("s2", detail)
    elif mode == "resolve_set":
        steps = [
            _call(
                "s1", resolver, [_literal("name_fragment", "normalized_text", "лазур")]
            ),
            _call(
                "s2",
                batch,
                [_step("assets", "s1", "asset.ref", "many")],
            ),
        ]
        finals = _finals("s2", batch)
    elif mode in {"followup", "followup_scalar", "forged", "wrong_semantic"}:
        context = payload.get("context", {}).get("confirmed_facts", [])
        asset = _context_by_type(context, "synthetic.asset")
        snapshot = _context_by_type(context, "synthetic.snapshot")
        asset_handle = state.option("forced_handle") or asset.get("handle")
        expected_type = (
            "synthetic.incompatible" if mode == "wrong_semantic" else "synthetic.asset"
        )
        arguments = [_context("asset", asset_handle, expected_type)]
        if mode == "followup_scalar":
            arguments.append(
                _context("moment", snapshot.get("handle"), "synthetic.snapshot")
            )
        else:
            arguments.append(_literal("moment", "datetime", FIXED_MOMENT))
        steps = [_call("s1", detail, arguments)]
        finals = _finals("s1", detail)
    else:
        raise ValueError(f"unknown plan kind {mode}")

    requirements = _requirements(steps, finals, cards)
    if mode == "select_only":
        for requirement in requirements:
            if requirement["semantic_type"] == "synthetic.asset":
                requirement["cardinality"] = "one"
    return {
        "schema_version": "1.0.0",
        "document_type": "planner_output",
        "request_id": echo["request_id"],
        "session_context_version": echo["session_context_version"],
        "catalog_snapshot_id": echo["catalog_snapshot_id"],
        "catalog_revision": echo["catalog_revision"],
        "decision": "execute",
        "interpretation": {
            "intent_kind": "data",
            "goal_ru": "Проверить переносимый synthetic entity/context contract.",
            "required_facts": requirements,
            "slots": [],
        },
        "result": {
            "kind": "execute",
            "plan_id": "00000000-0000-4000-8000-000000003001",
            "steps": steps,
            "final_outputs": finals,
        },
    }


def _interpretation_plan(
    payload: dict[str, Any],
    echo: dict[str, Any],
    resolver: dict[str, Any],
    *,
    ordinal: int,
    optional_bypass: bool,
) -> dict[str, Any]:
    requirements = [
        {
            "requirement_id": "r1",
            "semantic_type": "synthetic.asset.name",
            "value_type": "string",
            "cardinality": "many",
            "required": True,
        },
        {
            "requirement_id": "r2",
            "semantic_type": "synthetic.asset.code",
            "value_type": "string",
            "cardinality": "many",
            "required": False,
        },
    ]
    frozen_slot = {
        "slot_id": "name_filter",
        "semantic_type": "synthetic.asset.search_text",
        "value_type": "normalized_text",
        "status": "ambiguous",
        "mentions": ["лазурный"],
    }
    interpretation = {
        "intent_kind": "data",
        "goal_ru": "Показать выбранные пользователем синтетические активы.",
        "required_facts": requirements,
        "slots": [frozen_slot],
    }
    if ordinal == 1:
        return {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": echo["request_id"],
            "session_context_version": echo["session_context_version"],
            "catalog_snapshot_id": echo["catalog_snapshot_id"],
            "catalog_revision": echo["catalog_revision"],
            "decision": "clarify",
            "interpretation": interpretation,
            "result": {
                "kind": "clarify",
                "question_ru": "Какой оттенок использовать в поиске?",
                "missing_requirement_ids": ["r1"],
                "choices": [
                    {
                        "choice_id": "c1",
                        "label_ru": "Лазурный",
                        "slot_id": "name_filter",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "лазур",
                        },
                    },
                    {
                        "choice_id": "c2",
                        "label_ru": "Бирюзовый",
                        "slot_id": "name_filter",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "бирюз",
                        },
                    },
                ],
            },
        }
    resume = payload.get("interpretation_resume")
    if not isinstance(resume, dict) or not isinstance(
        resume.get("selected_binding"), dict
    ):
        raise ValueError("interpretation resume DTO is missing")
    selected = copy.deepcopy(resume["selected_binding"])
    resumed_interpretation = _without_none(
        copy.deepcopy(resume["frozen_interpretation"])
    )
    resumed_interpretation["slots"][0] = {
        **resumed_interpretation["slots"][0],
        "status": "resolved_literal",
        "binding": selected,
    }
    required_binding = (
        {
            "source": "literal",
            "value_type": "normalized_text",
            "value": "другой",
        }
        if optional_bypass
        else selected
    )
    steps = [
        _call(
            "s1",
            resolver,
            [{"parameter": "name_fragment", "binding": required_binding}],
        )
    ]
    finals = [{"step_id": "s1", "fact_id": "asset.name"}]
    if optional_bypass:
        steps.append(
            _call(
                "s2",
                resolver,
                [{"parameter": "name_fragment", "binding": selected}],
            )
        )
        finals.append({"step_id": "s2", "fact_id": "asset.code"})
    return {
        "schema_version": "1.0.0",
        "document_type": "planner_output",
        "request_id": echo["request_id"],
        "session_context_version": echo["session_context_version"],
        "catalog_snapshot_id": echo["catalog_snapshot_id"],
        "catalog_revision": echo["catalog_revision"],
        "decision": "execute",
        "interpretation": resumed_interpretation,
        "result": {
            "kind": "execute",
            "plan_id": "00000000-0000-4000-8000-000000003101",
            "steps": steps,
            "final_outputs": finals,
        },
    }


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


def _card(cards: dict[str, dict[str, Any]], skill_id: str) -> dict[str, Any]:
    if skill_id not in cards:
        raise ValueError(f"skill {skill_id} not shortlisted")
    return cards[skill_id]


def _call(
    step_id: str, card: dict[str, Any], arguments: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "kind": "skill_call",
        "skill_id": card["skill_id"],
        "skill_version": card["version"],
        "arguments": arguments,
        "required_output_fact_ids": [
            fact["fact_id"] for fact in card["output"]["facts"] if fact["required"]
        ],
        "on_empty": "stop_not_found",
    }


def _literal(parameter: str, value_type: str, value: Any) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "binding": {"source": "literal", "value_type": value_type, "value": value},
    }


def _step(
    parameter: str, step_id: str, fact_id: str, cardinality: str
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "binding": {
            "source": "step",
            "step_id": step_id,
            "fact_id": fact_id,
            "cardinality": cardinality,
        },
    }


def _context(parameter: str, handle: Any, semantic_type: str) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "binding": {
            "source": "context",
            "context_handle": handle or "ctx_AAAAAAAAAAAAAAAA",
            "expected_semantic_type": semantic_type,
        },
    }


def _context_by_type(context: Any, semantic_type: str) -> dict[str, Any]:
    if isinstance(context, list):
        for fact in context:
            if isinstance(fact, dict) and fact.get("semantic_type") == semantic_type:
                return fact
    return {}


def _finals(step_id: str, card: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"step_id": step_id, "fact_id": fact["fact_id"]}
        for fact in card["output"]["facts"]
        if fact["required"] and not fact["nullable"]
    ]


def _requirements(
    steps: list[dict[str, Any]],
    finals: list[dict[str, str]],
    cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_step = {step["step_id"]: cards[step["skill_id"]] for step in steps}
    result = []
    for index, final in enumerate(finals, start=1):
        card = by_step[final["step_id"]]
        fact = next(
            fact
            for fact in card["output"]["facts"]
            if fact["fact_id"] == final["fact_id"]
        )
        result.append(
            {
                "requirement_id": f"r{index}",
                "semantic_type": fact["semantic_type"],
                "value_type": fact["value_type"],
                "cardinality": {
                    "exactly_one": "one",
                    "zero_or_one": "zero_or_one",
                    "many": "many",
                    "aggregate": "aggregate",
                }[card["output"]["cardinality"]],
                "required": True,
            }
        )
    return result


def _query_envelope(arguments: dict[str, Any], state: FixtureState) -> dict[str, Any]:
    query = arguments.get("query")
    params = arguments.get("params")
    limit = arguments.get("limit")
    if (
        not isinstance(query, str)
        or not isinstance(params, dict)
        or type(limit) is not int
    ):
        return {"success": False, "error": "invalid fixture query"}
    if "Справочник.СинтетическиеАктивы" in query:
        rows = _asset_rows(state, params)[:limit]
        schema = {
            "columns": [
                {"name": "Актив", "types": [PHYSICAL_TYPE]},
                {"name": "Наименование", "types": ["Строка"]},
                {"name": "Код", "types": ["Строка"]},
            ]
        }
    elif "РегистрСведений.СинтетическиеСнимки" in query:
        asset = copy.deepcopy(params.get("Актив"))
        if state.option("detail_wrong_uuid", False) and isinstance(asset, dict):
            asset["УникальныйИдентификатор"] = _uuid(999)
        rows = [
            {
                "Актив": asset,
                "Наименование": (
                    state.option("detail_presentation")
                    or (asset or {}).get("Представление", "Лазурный актив")
                ),
                "Момент": state.option("snapshot_moment", FIXED_MOMENT),
                "Значение": 17.25,
            }
        ]
        schema = {
            "columns": [
                {"name": "Актив", "types": [PHYSICAL_TYPE]},
                {"name": "Наименование", "types": ["Строка"]},
                {"name": "Момент", "types": ["Дата"]},
                {"name": "Значение", "types": ["Число"]},
            ]
        }
    elif "РегистрСведений.СинтетическиеНаборы" in query:
        assets = params.get("Активы")
        if not isinstance(assets, list):
            assets = []
        rows = [{"Количество": len(assets)}]
        schema = {"columns": [{"name": "Количество", "types": ["Число"]}]}
    else:
        return {"success": False, "error": "unknown synthetic query"}
    return {
        "success": True,
        "data": rows,
        "schema": schema,
        "count": len(rows),
        "truncated": bool(state.option("truncated", False)),
        "has_more": bool(state.option("has_more", False)),
    }


def _asset_rows(
    state: FixtureState, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    count = int(state.option("asset_count", 1))
    duplicate = bool(state.option("duplicate_identity", False))
    same_presentation = bool(state.option("same_presentation", False))
    uuid_start = int(state.option("uuid_start", 1))
    physical = state.option("physical_type", PHYSICAL_TYPE)
    presentation_override = state.option("presentation")
    rows = []
    for index in range(count):
        identity_index = 0 if duplicate else index
        presentation = presentation_override or (
            "Одинаковое имя" if same_presentation else f"Лазурный актив {index + 1}"
        )
        rows.append(
            {
                "Актив": {
                    "_objectRef": True,
                    "УникальныйИдентификатор": _uuid(uuid_start + identity_index),
                    "ТипОбъекта": physical,
                    "Представление": presentation,
                },
                "Наименование": presentation,
                "Код": f"SYN-{uuid_start + identity_index:03d}",
            }
        )
    rows.sort(
        key=lambda row: (
            row["Наименование"],
            row["Актив"]["УникальныйИдентификатор"],
        )
    )
    parameters = params or {}
    if parameters.get("ЕстьКурсор") is True:
        cursor_name = parameters.get("ИмяКурсора")
        cursor_ref = parameters.get("СсылкаКурсора")
        cursor_uuid = (
            cursor_ref.get("УникальныйИдентификатор")
            if isinstance(cursor_ref, dict)
            else None
        )
        rows = [
            row
            for row in rows
            if row["Наименование"] > cursor_name
            or (
                row["Наименование"] == cursor_name
                and row["Актив"]["УникальныйИдентификатор"] > cursor_uuid
            )
        ]
    return rows


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = FixtureServer((args.host, args.port))
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
