"""Independent DeepSeek/MCP transport for slice 2 black-box acceptance."""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

WAREHOUSE_SKILL = "ut115.ref.warehouse.resolve"
STOCK_SKILL = "ut115.stock.balance"
SHIPMENT_SKILL = "ut115.sales.shipment-list"
ORDER_SKILL = "ut115.sales.order-header-status-by-number"
BARCODE_SKILL = "ut115.ref.item.resolve-barcode-exact"

_ALLOWED_OPTIONS = {
    "block_boundary",
    "barcode_items",
    "clear_requests",
    "deepseek_delay_ms",
    "deepseek_failures",
    "deepseek_mode",
    "mcp_delay_ms",
    "mcp_failures",
    "mcp_wrapper",
    "missing_column_at",
    "null_required_at",
    "null_sentinel",
    "order_rows",
    "plan_kind",
    "query_error_at",
    "retail_only",
    "row_count",
    "shipment_rows",
    "skill_id",
    "stock_balance",
    "stock_rows",
    "warehouse_rows",
    "warehouse_department_null",
    "wrong_type_at",
}


def _object_ref(object_type: str, serial: int, presentation: str) -> dict[str, Any]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": (f"00000000-0000-4000-8000-{serial:012d}"),
        "ТипОбъекта": object_type,
        "Представление": presentation,
    }


def _columns(*items: tuple[str, str]) -> dict[str, Any]:
    return {
        "columns": [{"name": name, "types": [type_name]} for name, type_name in items]
    }


WAREHOUSE_SCHEMA = _columns(
    ("Склад", "СправочникСсылка.Склады"),
    ("Наименование", "Строка"),
    ("ТипСклада", "Строка"),
    ("Подразделение", "СправочникСсылка.СтруктураПредприятия"),
)

BARCODE_SCHEMA = _columns(
    ("Номенклатура", "СправочникСсылка.Номенклатура"),
    ("Код", "Строка"),
    ("Артикул", "Строка"),
    ("Наименование", "Строка"),
)

SHIPMENT_SCHEMA = _columns(
    ("Реализация", "ДокументСсылка.РеализацияТоваровУслуг"),
    ("Номер", "Строка"),
    ("Дата", "Дата"),
    ("Проведен", "Булево"),
    ("Клиент", "СправочникСсылка.Партнеры"),
    ("Организация", "СправочникСсылка.Организации"),
    ("Склад", "СправочникСсылка.Склады"),
    ("Статус", "Строка"),
    ("СуммаДокумента", "Число"),
    ("Валюта", "Строка"),
    ("ЗаказКлиента", "ДокументСсылка.ЗаказКлиента"),
)

STOCK_SCHEMA = _columns(
    ("Номенклатура", "СправочникСсылка.Номенклатура"),
    ("Склад", "СправочникСсылка.Склады"),
    ("Помещение", "СправочникСсылка.СкладскиеПомещения"),
    ("Характеристика", "СправочникСсылка.ХарактеристикиНоменклатуры"),
    ("Назначение", "СправочникСсылка.Назначения"),
    ("Единица", "Строка"),
    ("ВНаличииОстаток", "Число"),
    ("Момент", "Дата"),
)

ZERO_AGGREGATE_SCHEMA = _columns(("ВНаличииОстаток", "Число"))

ORDER_SCHEMA = _columns(
    ("Заказ", "ДокументСсылка.ЗаказКлиента"),
    ("Номер", "Строка"),
    ("Дата", "Дата"),
    ("Проведен", "Булево"),
    ("Клиент", "СправочникСсылка.Партнеры"),
    ("Организация", "СправочникСсылка.Организации"),
    ("Склад", "СправочникСсылка.Склады"),
    ("Статус", "Строка"),
    ("СуммаДокумента", "Число"),
    ("Валюта", "Строка"),
    ("СостояниеИсполнения", "Строка"),
    ("ПроцентОплаты", "Число"),
    ("ПроцентОтгрузки", "Число"),
    ("ПроцентДолга", "Число"),
    ("ДатаСобытия", "Дата"),
)


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.release_event = threading.Event()
        self.release_event.set()
        self.scenario = "warehouse"
        self.options: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.deepseek_ordinal = 0
        self.query_ordinal = 0
        self.blocked_boundaries: list[str] = []

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        unknown = set(payload) - _ALLOWED_OPTIONS - {"name"}
        if unknown:
            raise ValueError("unknown fixture options: " + ", ".join(sorted(unknown)))
        for key in (
            "deepseek_delay_ms",
            "deepseek_failures",
            "barcode_items",
            "mcp_delay_ms",
            "mcp_failures",
            "missing_column_at",
            "null_required_at",
            "order_rows",
            "query_error_at",
            "row_count",
            "shipment_rows",
            "stock_rows",
            "warehouse_rows",
            "wrong_type_at",
        ):
            value = payload.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{key} must be a non-negative integer")
        if payload.get("block_boundary") not in {None, "deepseek", "mcp"}:
            raise ValueError("block_boundary must be deepseek, mcp, or null")

        with self.lock:
            self.scenario = name
            self.options = {
                key: copy.deepcopy(value)
                for key, value in payload.items()
                if key not in {"name", "clear_requests"}
            }
            self.deepseek_ordinal = 0
            self.query_ordinal = 0
            self.blocked_boundaries.clear()
            if payload.get("clear_requests", True):
                self.requests.clear()
            if self.options.get("block_boundary") is None:
                self.release_event.set()
            else:
                self.release_event.clear()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "fixture_version": "slice2-independent-v1",
                "configuration_proof": {
                    "barcode_register": "InformationRegister.BarcodeItems",
                    "dimensions": [
                        "barcode",
                        "item",
                        "characteristic",
                        "series",
                    ],
                    "register_rows_per_item": 2,
                },
                "scenario": self.scenario,
                "options": copy.deepcopy(self.options),
                "requests": copy.deepcopy(self.requests),
                "blocked_boundaries": list(self.blocked_boundaries),
                "deepseek_ordinal": self.deepseek_ordinal,
                "query_ordinal": self.query_ordinal,
            }

    def record(self, kind: str, body: Any, **details: Any) -> None:
        item = {
            "kind": kind,
            "at_monotonic": time.monotonic(),
            "body": copy.deepcopy(body),
            **copy.deepcopy(details),
        }
        with self.lock:
            self.requests.append(item)

    def option(self, name: str, default: Any = None) -> Any:
        with self.lock:
            return copy.deepcopy(self.options.get(name, default))

    def next_deepseek(self) -> int:
        with self.lock:
            self.deepseek_ordinal += 1
            return self.deepseek_ordinal

    def next_query(self) -> int:
        with self.lock:
            self.query_ordinal += 1
            return self.query_ordinal

    def wait_at_boundary(self, boundary: str) -> None:
        if self.option("block_boundary") != boundary:
            return
        with self.lock:
            self.blocked_boundaries.append(boundary)
        self.release_event.wait(timeout=60)

    def release(self) -> None:
        self.release_event.set()


class FixtureHandler(BaseHTTPRequestHandler):
    server: "FixtureServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("request body must be a JSON object")
        return value

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/__fixture__/health":
            self._send_json(HTTPStatus.OK, {"status": "ready"})
            return
        if path == "/__fixture__/state":
            self._send_json(HTTPStatus.OK, self.server.state.snapshot())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "fixture_route_not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._read_json()
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
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
            return
        if path == "/__fixture__/release":
            self.server.state.release()
            self._send_json(HTTPStatus.OK, {"status": "released"})
            return
        if path in {"/chat/completions", "/v1/chat/completions"}:
            self._deepseek(body)
            return
        if path == "/mcp":
            self._mcp(body)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "fixture_route_not_found"})

    def _deepseek(self, body: dict[str, Any]) -> None:
        ordinal = self.server.state.next_deepseek()
        planner_input = _planner_input(body)
        self.server.state.record(
            "deepseek", body, ordinal=ordinal, planner_input=planner_input
        )
        self.server.state.wait_at_boundary("deepseek")
        _delay(self.server.state.option("deepseek_delay_ms", 0))
        if ordinal <= self.server.state.option("deepseek_failures", 0):
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "synthetic"})
            return
        mode = self.server.state.option("deepseek_mode", "valid")
        if mode == "invalid_envelope":
            self._send_json(HTTPStatus.OK, {"choices": []})
            return
        if mode == "malformed_json":
            content = "{not-json"
        else:
            content = json.dumps(
                _planner_output(planner_input, self.server.state),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        self._send_json(
            HTTPStatus.OK,
            {
                "id": f"slice2-{ordinal}",
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
        if method == "notifications/initialized":
            self.server.state.record("mcp_initialized", body)
            self._send_empty(HTTPStatus.ACCEPTED)
            return
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
                        "serverInfo": {"name": "slice2-fixture", "version": "1.0.0"},
                    },
                },
            )
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
                                "description": "Synthetic read-only query boundary.",
                                "inputSchema": {
                                    "type": "object",
                                    "required": [
                                        "query",
                                        "params",
                                        "limit",
                                        "include_schema",
                                    ],
                                    "additionalProperties": False,
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
                                "description": "Synthetic metadata boundary.",
                                "inputSchema": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
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
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if tool_name == "get_metadata":
            self.server.state.record("mcp_get_metadata", body)
            self._tool_result(request_id, {"success": True, "data": {}})
            return
        if tool_name != "execute_query" or not isinstance(arguments, dict):
            self._rpc_error(request_id, -32602, "Unknown tool")
            return

        ordinal = self.server.state.next_query()
        self.server.state.record(
            "mcp_execute_query", body, ordinal=ordinal, arguments=arguments
        )
        self.server.state.wait_at_boundary("mcp")
        _delay(self.server.state.option("mcp_delay_ms", 0))
        if ordinal <= self.server.state.option("mcp_failures", 0):
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "synthetic"})
            return
        envelope = _query_envelope(arguments, ordinal, self.server.state)
        self._tool_result(request_id, envelope)

    def _tool_result(self, request_id: Any, envelope: dict[str, Any]) -> None:
        wrapper = self.server.state.option("mcp_wrapper", "structured_and_text")
        text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        if wrapper == "structured_and_text":
            result: dict[str, Any] = {
                "structuredContent": envelope,
                "content": [{"type": "text", "text": text}],
            }
        elif wrapper == "structured":
            result = {"structuredContent": envelope, "content": []}
        elif wrapper == "text":
            result = {"content": [{"type": "text", "text": text}]}
        elif wrapper == "ambiguous_text":
            result = {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": '{"success":false}'},
                ]
            }
        elif wrapper == "conflicting_text":
            result = {
                "structuredContent": envelope,
                "content": [{"type": "text", "text": '{"success":false}'}],
            }
        elif wrapper == "non_json_text":
            result = {"content": [{"type": "text", "text": "not-json"}]}
        elif wrapper == "nested":
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"result": {"structuredContent": envelope}},
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        else:
            result = {"structuredContent": envelope, "content": []}
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


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: FixtureState) -> None:
        super().__init__(address, FixtureHandler)
        self.state = state


def _delay(milliseconds: int) -> None:
    if milliseconds:
        time.sleep(milliseconds / 1000)


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


def _planner_output(payload: dict[str, Any], state: FixtureState) -> dict[str, Any]:
    echo = payload.get("expected_echo")
    manifest = payload.get("skill_manifest")
    if not isinstance(echo, dict) or not isinstance(manifest, list):
        raise ValueError("fixture expected a production planner request")
    cards = {
        card["skill_id"]: card
        for card in manifest
        if isinstance(card, dict) and isinstance(card.get("skill_id"), str)
    }
    plan_kind = state.option("plan_kind") or state.scenario
    explicit_skill = state.option("skill_id")
    if plan_kind in {"warehouse", "single"}:
        skill_id = explicit_skill or WAREHOUSE_SKILL
        card = _card(cards, skill_id)
        arguments = []
        if state.option("retail_only", False):
            arguments.append(_literal_argument("retail_only", "boolean", True))
        steps = [_skill_call("s1", card, arguments)]
        finals = _finals("s1", card)
    elif plan_kind == "shipment":
        card = _card(cards, explicit_skill or SHIPMENT_SKILL)
        steps = [
            _skill_call(
                "s1",
                card,
                [
                    _literal_argument(
                        "period",
                        "period",
                        {
                            "start": "2024-01-01T00:00:00+03:00",
                            "end_exclusive": "2025-01-01T00:00:00+03:00",
                            "timezone": "Europe/Moscow",
                            "precision": "year",
                        },
                    )
                ],
            )
        ]
        finals = _finals("s1", card)
    elif plan_kind == "stock":
        card = _card(cards, explicit_skill or STOCK_SKILL)
        steps = [
            _skill_call(
                "s1",
                card,
                [
                    {
                        "parameter": "moment",
                        "binding": {"source": "system", "name": "turn_time"},
                    }
                ],
            )
        ]
        finals = _finals("s1", card)
    elif plan_kind == "sp01":
        card = _card(cards, explicit_skill or ORDER_SKILL)
        steps = [
            _skill_call(
                "s1",
                card,
                [_literal_argument("document_number", "string", "S2-000001")],
            )
        ]
        finals = _finals("s1", card)
    elif plan_kind == "barcode":
        card = _card(cards, explicit_skill or BARCODE_SKILL)
        steps = [
            _skill_call(
                "s1",
                card,
                [_literal_argument("barcode", "string", "4600000000001")],
            )
        ]
        finals = _finals("s1", card)
    elif plan_kind == "q054":
        warehouse = _card(cards, WAREHOUSE_SKILL)
        stock = _card(cards, STOCK_SKILL)
        steps = [
            _skill_call(
                "s1",
                warehouse,
                [_literal_argument("retail_only", "boolean", True)],
            ),
            _skill_call(
                "s2",
                stock,
                [
                    {
                        "parameter": "warehouses",
                        "binding": {
                            "source": "step",
                            "step_id": "s1",
                            "fact_id": "warehouse.ref",
                            "cardinality": "many",
                        },
                    },
                    {
                        "parameter": "moment",
                        "binding": {"source": "system", "name": "turn_time"},
                    },
                ],
            ),
        ]
        finals = [
            {"step_id": "s1", "fact_id": "warehouse.type"},
            *_finals("s2", stock),
        ]
    else:
        raise ValueError(f"unknown plan_kind {plan_kind!r}")

    requirements = _requirements(finals, steps, cards)
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
            "goal_ru": "Проверить детерминированный приемочный сценарий.",
            "required_facts": requirements,
            "slots": [],
        },
        "result": {
            "kind": "execute",
            "plan_id": "00000000-0000-4000-8000-000000009901",
            "steps": steps,
            "final_outputs": finals,
        },
    }


def _card(cards: dict[str, dict[str, Any]], skill_id: str) -> dict[str, Any]:
    try:
        return cards[skill_id]
    except KeyError as error:
        raise ValueError(f"skill {skill_id!r} was not shortlisted") from error


def _skill_call(
    step_id: str, card: dict[str, Any], arguments: list[dict[str, Any]]
) -> dict[str, Any]:
    facts = card["output"]["facts"]
    return {
        "step_id": step_id,
        "kind": "skill_call",
        "skill_id": card["skill_id"],
        "skill_version": card["version"],
        "arguments": arguments,
        "required_output_fact_ids": [fact["fact_id"] for fact in facts],
        "on_empty": "stop_not_found",
    }


def _literal_argument(parameter: str, value_type: str, value: Any) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "binding": {"source": "literal", "value_type": value_type, "value": value},
    }


def _finals(step_id: str, card: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"step_id": step_id, "fact_id": fact["fact_id"]}
        for fact in card["output"]["facts"]
        if fact["required"] and not fact["nullable"]
    ]


def _requirements(
    finals: list[dict[str, str]],
    steps: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    card_by_step = {step["step_id"]: cards[step["skill_id"]] for step in steps}
    requirements: list[dict[str, Any]] = []
    for index, final in enumerate(finals, start=1):
        card = card_by_step[final["step_id"]]
        fact = next(
            item
            for item in card["output"]["facts"]
            if item["fact_id"] == final["fact_id"]
        )
        value_type = fact["value_type"]
        unit = {
            "money": "currency",
            "quantity": "quantity_unit",
            "percentage": "percentage",
        }.get(value_type)
        time_semantics = (
            "moment"
            if fact["role"] == "time" and value_type in {"date", "datetime"}
            else "period"
            if fact["role"] == "time" and value_type == "period"
            else None
        )
        requirement: dict[str, Any] = {
            "requirement_id": f"r{index}",
            "semantic_type": fact["semantic_type"],
            "value_type": value_type,
            "cardinality": {
                "exactly_one": "one",
                "zero_or_one": "zero_or_one",
                "many": "many",
                "aggregate": "aggregate",
            }[card["output"]["cardinality"]],
            "required": bool(fact["required"] and not fact["nullable"]),
        }
        if unit is not None:
            requirement["unit_dimension"] = unit
        if time_semantics is not None:
            requirement["time_semantics"] = time_semantics
        requirements.append(requirement)
    return requirements


def _query_envelope(
    arguments: dict[str, Any], ordinal: int, state: FixtureState
) -> dict[str, Any]:
    if ordinal == state.option("query_error_at"):
        return {"success": False, "error": "synthetic query failure"}
    query = arguments.get("query")
    params = arguments.get("params")
    limit = arguments.get("limit")
    if (
        not isinstance(query, str)
        or not isinstance(params, dict)
        or type(limit) is not int
    ):
        return {"success": False, "error": "invalid fixture request"}
    if "РеализацияТоваровУслуг" in query:
        rows, schema = _shipment_rows(params, limit, state), SHIPMENT_SCHEMA
    elif "Справочник.Склады" in query:
        rows, schema = _warehouse_rows(params, limit, state), WAREHOUSE_SCHEMA
    elif "ТоварыНаСкладах" in query:
        if "СУММА(" in query.upper():
            rows = [{"ВНаличииОстаток": float(state.option("stock_balance", 0))}]
            schema = ZERO_AGGREGATE_SCHEMA
        else:
            rows, schema = _stock_rows(params, limit, state), STOCK_SCHEMA
    elif "Документ.ЗаказКлиента" in query:
        rows, schema = _order_rows(limit, state), ORDER_SCHEMA
    elif "РегистрСведений.ШтрихкодыНоменклатуры" in query:
        rows, schema = _barcode_rows(params, query, limit, state), BARCODE_SCHEMA
    else:
        return {"success": False, "error": "unknown synthetic query"}

    if state.option("null_sentinel", False):
        rows = [{column["name"]: None for column in schema["columns"]}]
        if "Справочник.Склады" in query:
            rows[0]["Подразделение"] = _object_ref(
                "СправочникСсылка.СтруктураПредприятия",
                499999,
                "Sentinel proof department",
            )
        elif "Документ.ЗаказКлиента" in query:
            rows[0]["СостояниеИсполнения"] = "Sentinel proof state"
    if rows and ordinal == state.option("null_required_at"):
        rows[0][schema["columns"][0]["name"]] = None
    schema = copy.deepcopy(schema)
    if ordinal == state.option("wrong_type_at"):
        schema["columns"][0]["types"] = ["Строка"]
    if ordinal == state.option("missing_column_at"):
        missing = schema["columns"].pop()["name"]
        rows = [
            {key: value for key, value in row.items() if key != missing} for row in rows
        ]
    return {
        "success": True,
        "data": rows,
        "schema": schema,
        "count": len(rows),
    }


def _row_count(state: FixtureState, specific: str, default: int) -> int:
    value = state.option(specific)
    if value is None:
        value = state.option("row_count", default)
    return int(value)


def _warehouse_rows(
    params: dict[str, Any], limit: int, state: FixtureState
) -> list[dict[str, Any]]:
    count = _row_count(state, "warehouse_rows", 1)
    retail = state.option("retail_only", True)
    rows = []
    for index in range(1, count + 1):
        rows.append(
            {
                "Склад": _object_ref(
                    "СправочникСсылка.Склады", 200000 + index, f"Retail {index:03d}"
                ),
                "Наименование": f"Retail {index:03d}",
                "ТипСклада": ("Розничный магазин" if retail else "Оптовый склад"),
                "Подразделение": (
                    None
                    if state.option("warehouse_department_null", False)
                    or index % 2 == 0
                    else _object_ref(
                        "СправочникСсылка.СтруктураПредприятия",
                        400000 + index,
                        f"Department {index:03d}",
                    )
                ),
            }
        )
    if params.get("ЕстьКурсор") is True:
        cursor_name = params.get("НаименованиеКурсора", params.get("ИмяКурсора"))
        cursor_ref = params.get("СсылкаКурсора")
        cursor_id = (
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
                and row["Склад"]["УникальныйИдентификатор"] > cursor_id
            )
        ]
    return rows[:limit]


def _shipment_rows(
    params: dict[str, Any], limit: int, state: FixtureState
) -> list[dict[str, Any]]:
    count = _row_count(state, "shipment_rows", 1)
    customer = _object_ref("СправочникСсылка.Партнеры", 410001, "Customer A")
    organization = _object_ref("СправочникСсылка.Организации", 420001, "Organization A")
    warehouse = _object_ref("СправочникСсылка.Склады", 200001, "Retail 001")
    rows: list[dict[str, Any]] = []
    first_day = datetime(2024, 12, 31, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    for index in range(1, count + 1):
        moment = first_day - timedelta(days=(index - 1) // 3)
        rows.append(
            {
                "Реализация": _object_ref(
                    "ДокументСсылка.РеализацияТоваровУслуг",
                    100000 + index,
                    f"Shipment {index:03d}",
                ),
                "Номер": f"SHIP-{index:03d}",
                "Дата": moment.isoformat(),
                "Проведен": True,
                "Клиент": customer,
                "Организация": organization,
                "Склад": warehouse,
                "Статус": "Реализовано",
                "СуммаДокумента": float(index * 100),
                "Валюта": "RUB",
                "ЗаказКлиента": None,
            }
        )
    if params.get("ЕстьКурсор") is True:
        cursor_date = params.get("ДатаКурсора")
        cursor_ref = params.get("СсылкаКурсора")
        cursor_id = (
            cursor_ref.get("УникальныйИдентификатор")
            if isinstance(cursor_ref, dict)
            else None
        )
        rows = [
            row
            for row in rows
            if row["Дата"] < cursor_date
            or (
                row["Дата"] == cursor_date
                and row["Реализация"]["УникальныйИдентификатор"] > cursor_id
            )
        ]
    return rows[:limit]


def _stock_rows(
    params: dict[str, Any], limit: int, state: FixtureState
) -> list[dict[str, Any]]:
    bound = params.get("Склады")
    if isinstance(bound, list):
        warehouses = copy.deepcopy(bound)
    else:
        warehouses = [_object_ref("СправочникСсылка.Склады", 200001, "Retail 001")]
    requested_count = state.option("stock_rows")
    if requested_count is None:
        requested_count = state.option("row_count")
    if requested_count is not None:
        count = int(requested_count)
        warehouses = (
            [warehouses[index % len(warehouses)] for index in range(count)]
            if warehouses and count
            else []
        )
    moment = params.get("Момент") or "2026-07-21T12:00:00+03:00"
    balance = state.option("stock_balance", 7)
    rows = []
    for index, warehouse in enumerate(warehouses, start=1):
        rows.append(
            {
                "Номенклатура": _object_ref(
                    "СправочникСсылка.Номенклатура",
                    500000 + index,
                    f"Item {index:03d}",
                ),
                "Склад": warehouse,
                "Помещение": _object_ref(
                    "СправочникСсылка.СкладскиеПомещения",
                    510000 + index,
                    f"Bin {index:03d}",
                ),
                "Характеристика": _object_ref(
                    "СправочникСсылка.ХарактеристикиНоменклатуры",
                    520000 + index,
                    f"Characteristic {index:03d}",
                ),
                "Назначение": _object_ref(
                    "СправочникСсылка.Назначения",
                    530000 + index,
                    f"Assignment {index:03d}",
                ),
                "Единица": "pcs",
                "ВНаличииОстаток": float(balance),
                "Момент": moment,
            }
        )
    return rows[:limit]


def _order_rows(limit: int, state: FixtureState) -> list[dict[str, Any]]:
    count = _row_count(state, "order_rows", 1)
    rows = []
    for index in range(1, count + 1):
        rows.append(
            {
                "Заказ": _object_ref(
                    "ДокументСсылка.ЗаказКлиента", 300000 + index, f"Order {index:03d}"
                ),
                "Номер": "S2-000001",
                "Дата": "2025-02-12T10:00:00+03:00",
                "Проведен": True,
                "Клиент": _object_ref(
                    "СправочникСсылка.Партнеры", 410001, "Customer A"
                ),
                "Организация": _object_ref(
                    "СправочникСсылка.Организации", 420001, "Organization A"
                ),
                "Склад": _object_ref("СправочникСсылка.Склады", 200001, "Retail 001"),
                "Статус": "К выполнению",
                "СуммаДокумента": 3000.0,
                "Валюта": "RUB",
                "СостояниеИсполнения": "Ожидается обеспечение",
                "ПроцентОплаты": 100.0,
                "ПроцентОтгрузки": 0.0,
                "ПроцентДолга": 0.0,
                "ДатаСобытия": "2025-02-12T10:05:00+03:00",
            }
        )
    return rows[:limit]


def _barcode_rows(
    params: dict[str, Any],
    query: str,
    limit: int,
    state: FixtureState,
) -> list[dict[str, Any]]:
    count = _row_count(state, "barcode_items", 1)
    register_rows: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        projected = {
            "Номенклатура": _object_ref(
                "СправочникСсылка.Номенклатура",
                600000 + index,
                f"Barcode item {index:03d}",
            ),
            "Код": f"ITEM-{index:06d}",
            "Артикул": f"ARTICLE-{index:06d}",
            "Наименование": f"Item group {(index - 1) // 3:03d}",
        }
        for _hidden_characteristic_series in range(2):
            register_rows.append(copy.deepcopy(projected))

    register_rows.sort(
        key=lambda row: (
            row["Наименование"],
            row["Номенклатура"]["УникальныйИдентификатор"],
        )
    )
    if "ВЫБРАТЬ РАЗЛИЧНЫЕ" in query.upper():
        distinct: dict[str, dict[str, Any]] = {}
        for row in register_rows:
            item_id = row["Номенклатура"]["УникальныйИдентификатор"]
            distinct.setdefault(item_id, row)
        register_rows = list(distinct.values())

    if params.get("ЕстьКурсор") is True:
        cursor_name = params.get("ИмяКурсора")
        cursor_ref = params.get("СсылкаКурсора")
        cursor_id = (
            cursor_ref.get("УникальныйИдентификатор")
            if isinstance(cursor_ref, dict)
            else None
        )
        register_rows = [
            row
            for row in register_rows
            if row["Наименование"] > cursor_name
            or (
                row["Наименование"] == cursor_name
                and row["Номенклатура"]["УникальныйИдентификатор"] > cursor_id
            )
        ]
    return register_rows[:limit]


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
