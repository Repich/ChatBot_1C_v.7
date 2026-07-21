from __future__ import annotations

import json
import re
from typing import Any

from .conftest import AppFactory, RunningApp
from .support import (
    AppClient,
    FixtureClient,
    deepseek_calls,
    error_code,
    execute_query_calls,
    fact_values,
    mcp_arguments,
)
from .synthetic_skills import (
    SHIPMENT_SKILL,
    STOCK_SKILL,
    WAREHOUSE_SKILL,
    replacement_variant,
)

ORDER_SKILL = "ut115.sales.order-header-status-by-number"

UNBOUNDED_IDENTITY_FACTS = {
    "ut115.ref.item.resolve-article-exact": {"item.ref"},
    "ut115.ref.item.resolve-code-exact": {"item.ref"},
    "ut115.ref.item.resolve-barcode-exact": {"item.ref"},
    "ut115.ref.item.resolve-name-contains": {"item.ref"},
    WAREHOUSE_SKILL: {"warehouse.ref"},
    "ut115.sales.order-lines": {"order.ref", "order.line_number"},
    STOCK_SKILL: {
        "item.ref",
        "warehouse.ref",
        "storage_bin.ref",
        "characteristic.ref",
        "assignment.ref",
    },
}

WAREHOUSE_FACTS = {
    "warehouse.ref",
    "warehouse.name",
    "warehouse.type",
    "warehouse.department",
}
WAREHOUSE_REQUIRED = {
    "warehouse.ref",
    "warehouse.name",
    "warehouse.type",
}
SHIPMENT_REQUIRED = {
    "shipment.ref",
    "shipment.number",
    "shipment.date",
    "shipment.posted",
    "shipment.customer",
    "shipment.organization",
    "shipment.warehouse",
    "shipment.status",
    "shipment.amount",
    "shipment.currency",
}
ORDER_REQUIRED = {
    "order.ref",
    "order.number",
    "order.date",
    "order.posted",
    "order.customer",
    "order.organization",
    "order.warehouse",
    "order.status",
    "order.amount",
    "order.currency",
}
ORDER_OPTIONAL = {
    "order.execution_state",
    "order.payment_percent",
    "order.shipment_percent",
    "order.debt_percent",
    "order.state_event_date",
}


def _parameters(skill: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {parameter["name"]: parameter for parameter in skill["parameters"]}


def _facts(skill: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {fact["fact_id"]: fact for fact in skill["output_contract"]["facts"]}


def _planner_document(api: AppClient, trace_id: str) -> dict[str, Any]:
    envelope = json.loads(api.diagnostic_members(trace_id)["planner/response.json"])
    if envelope.get("document_type") == "planner_output":
        value = envelope
    else:
        content = envelope["choices"][0]["message"]["content"]
        value = json.loads(content)
    assert isinstance(value, dict)
    assert value["document_type"] == "planner_output"
    return value


def _snapshot_skill(evidence: dict[str, Any], skill_id: str) -> dict[str, Any]:
    return next(
        skill
        for skill in evidence["catalog_snapshot"]["skills"]
        if skill["skill_id"] == skill_id
    )


def test_slice_two_package_has_no_unproved_prefix_pagination(
    app: RunningApp,
) -> None:
    api = app.api
    catalog = api.list_skills()
    active_ids = {item["skill_id"] for item in catalog["skills"]}
    assert set(UNBOUNDED_IDENTITY_FACTS) <= active_ids

    issues: list[str] = []
    for skill_id, expected_identity in UNBOUNDED_IDENTITY_FACTS.items():
        skill = api.export_skill(skill_id)
        operation = skill["operation"]
        pagination = operation["pagination"]
        query = operation["query_template"]["text"]
        if re.search(r"\b(?:ПЕРВЫЕ|TOP)\b", query, re.IGNORECASE):
            issues.append(f"{skill_id}: paged query contains a static TOP/ПЕРВЫЕ limit")
        strategy = pagination.get("strategy")
        if strategy != "keyset":
            issues.append(
                f"{skill_id}: pagination.strategy={strategy!r}, expected 'keyset'"
            )
            continue

        sort_ids = [item["fact_id"] for item in pagination.get("sort", [])]
        cursor_ids = [item["fact_id"] for item in pagination.get("cursor_bindings", [])]
        if not sort_ids or cursor_ids != sort_ids:
            issues.append(
                f"{skill_id}: cursor bindings {cursor_ids!r} do not match sort {sort_ids!r}"
            )

        row_identity = set(skill["output_contract"]["row_identity_fact_ids"])
        if row_identity != expected_identity:
            issues.append(
                f"{skill_id}: row identity {sorted(row_identity)!r}, "
                f"expected {sorted(expected_identity)!r}"
            )
        tail = sort_ids[-len(expected_identity) :]
        if set(tail) != expected_identity:
            issues.append(
                f"{skill_id}: sort does not end in immutable unique identity; "
                f"tail={tail!r}"
            )

        facts = _facts(skill)
        if any(
            facts[fact_id]["nullable"] is not False
            or facts[fact_id]["value_type"] not in {"entity_ref", "integer"}
            for fact_id in expected_identity
        ):
            issues.append(
                f"{skill_id}: identity facts must be non-null entity/integer values"
            )

        has_cursor = pagination.get("has_cursor_query_parameter")
        if not isinstance(has_cursor, str) or f"&{has_cursor}" not in query:
            issues.append(f"{skill_id}: has-cursor parameter is not bound in query")
        for binding in pagination.get("cursor_bindings", []):
            query_parameter = binding.get("query_parameter")
            if (
                not isinstance(query_parameter, str)
                or f"&{query_parameter}" not in query
            ):
                issues.append(
                    f"{skill_id}: cursor parameter {query_parameter!r} is not bound in query"
                )

    for item in catalog["skills"]:
        skill_id = item["skill_id"]
        operation = api.export_skill(skill_id)["operation"]
        pagination = operation.get("pagination")
        if not isinstance(pagination, dict):
            continue
        strategy = pagination.get("strategy")
        if strategy == "prefix" and skill_id not in UNBOUNDED_IDENTITY_FACTS:
            issues.append(
                f"{skill_id}: unproved prefix remains in active slice-two package"
            )

    assert not issues, "Slice-two package pagination gate failed:\n" + "\n".join(issues)


def test_r06_portable_contract_uses_only_metadata_proven_fields_and_keyset(
    app: RunningApp,
) -> None:
    skill = app.api.export_skill(WAREHOUSE_SKILL)
    parameters = _parameters(skill)
    assert set(parameters) == {"name_fragment", "retail_only", "department"}
    assert parameters["name_fragment"]["required"] is False
    assert parameters["name_fragment"]["default"] == ""
    assert parameters["retail_only"]["required"] is False
    assert parameters["retail_only"]["default"] is False
    assert parameters["department"]["required"] is False
    assert parameters["department"]["semantic_type"] == "catalog.department"

    operation = skill["operation"]
    query = operation["query_template"]["text"]
    assert "Перечисление.ТипыСкладов.РозничныйМагазин" in query
    assert "Организаци" not in query
    assert "Назначени" not in query
    assert not re.search(r"\b(?:ПЕРВЫЕ|TOP)\b", query, re.IGNORECASE)
    assert {binding["parameter"] for binding in operation["parameter_bindings"]} == {
        "name_fragment",
        "retail_only",
        "department",
    }
    assert {binding["fact_id"] for binding in operation["column_bindings"]} == (
        WAREHOUSE_FACTS
    )

    facts = _facts(skill)
    assert set(facts) == WAREHOUSE_FACTS
    assert {fact_id for fact_id, fact in facts.items() if fact["required"]} == (
        WAREHOUSE_REQUIRED
    )
    assert facts["warehouse.department"]["nullable"] is True
    assert skill["output_contract"]["row_identity_fact_ids"] == ["warehouse.ref"]
    assert (
        set(skill["output_contract"]["sufficiency"]["required_fact_sets"][0])
        == WAREHOUSE_REQUIRED
    )

    pagination = operation["pagination"]
    assert pagination["strategy"] == "keyset"
    assert pagination["sort"] == [
        {"fact_id": "warehouse.name", "direction": "asc"},
        {"fact_id": "warehouse.ref", "direction": "asc"},
    ]
    assert [item["fact_id"] for item in pagination["cursor_bindings"]] == [
        "warehouse.name",
        "warehouse.ref",
    ]
    assert "maximum_total" not in pagination


def test_r06_bare_export_is_portable_to_clean_catalog_and_executes_positive_empty(
    app: RunningApp,
    app_factory: AppFactory,
    fixture_transport: FixtureClient,
) -> None:
    exported = app.api.export_skill(WAREHOUSE_SKILL)
    clean = app_factory.start(auto_import=False)
    initial = clean.api.list_skills()
    assert initial["skills"] == []

    response, imported = clean.api.import_package(
        json.dumps(exported, ensure_ascii=False).encode("utf-8")
    )
    assert response.status == 200, imported
    assert imported["status"] == "accepted"
    transferred = clean.api.export_skill(WAREHOUSE_SKILL)
    assert transferred == exported

    fixture_transport.configure(
        "portable r06 positive", plan_kind="warehouse", warehouse_rows=2
    )
    session = clean.api.create_session()
    positive, _ = clean.api.ask(session["session_id"], "Покажи склады")
    assert positive["outcome"] == "success_with_rows", positive
    assert fact_values(clean.api.evidence(positive["trace_id"]), "warehouse.name") == [
        "Retail 001",
        "Retail 002",
    ]
    call = execute_query_calls(fixture_transport.state())[0]
    arguments = mcp_arguments(call)
    assert arguments["limit"] == 21
    assert arguments["include_schema"] is True
    assert arguments["params"]["Шаблон"] == "%%"
    assert arguments["params"]["ТолькоРозничные"] is False
    assert arguments["params"]["Подразделение"] is None

    fixture_transport.configure(
        "portable r06 empty", plan_kind="warehouse", warehouse_rows=0
    )
    empty, _ = clean.api.ask(session["session_id"], "Покажи склады еще раз")
    assert empty["outcome"] == "success_empty", empty
    assert clean.api.evidence(empty["trace_id"])["facts"] == []


def test_sp04_contract_is_page_scoped_keyset_with_optional_null_defaults(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    skill = api.export_skill(SHIPMENT_SKILL)
    parameters = _parameters(skill)
    assert set(parameters) == {"period", "customer", "organization", "warehouse"}
    assert parameters["period"]["required"] is True
    for name in ("customer", "organization", "warehouse"):
        assert parameters[name]["required"] is False
        assert "default" not in parameters[name]

    operation = skill["operation"]
    query = operation["query_template"]["text"]
    assert not re.search(r"\b(?:ПЕРВЫЕ|TOP)\b", query, re.IGNORECASE)
    assert operation["pagination"] == {
        "strategy": "keyset",
        "has_cursor_query_parameter": "ЕстьКурсор",
        "sort": [
            {"fact_id": "shipment.date", "direction": "desc"},
            {"fact_id": "shipment.ref", "direction": "asc"},
        ],
        "cursor_bindings": [
            {
                "fact_id": "shipment.date",
                "query_parameter": "ДатаКурсора",
                "encoding": "datetime",
            },
            {
                "fact_id": "shipment.ref",
                "query_parameter": "СсылкаКурсора",
                "encoding": "object_ref",
            },
        ],
    }
    facts = _facts(skill)
    assert {fact_id for fact_id, fact in facts.items() if fact["required"]} == (
        SHIPMENT_REQUIRED
    )
    assert facts["shipment.order"]["required"] is False
    assert facts["shipment.order"]["nullable"] is True
    assert skill["output_contract"]["sufficiency"]["truncation_policy"] == (
        "page_is_complete"
    )
    assert not any("total" in fact_id for fact_id in facts)

    fixture_transport.configure(
        "sp04 visible page", plan_kind="shipment", shipment_rows=22
    )
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Покажи реализации за 2024 год")
    assert turn["outcome"] == "success_with_rows", turn
    assert turn["pagination"]["shown"] == 20
    assert turn["pagination"]["has_more"] is True
    assert "Показано 20" in turn["assistant_message"]["text"]
    assert "Всего 20" not in turn["assistant_message"]["text"]
    evidence = api.evidence(turn["trace_id"])
    assert len(fact_values(evidence, "shipment.ref")) == 20
    assert not any(
        fact["semantic_type"].endswith(".total") for fact in evidence["facts"]
    )


def test_sp01_full_header_contract_and_runtime_projection(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    skill = api.export_skill(ORDER_SKILL)
    assert set(_parameters(skill)) == {"document_number"}
    assert _parameters(skill)["document_number"]["required"] is True
    assert set(skill["provides"]["capability_ids"]) == {
        "CAP-COMMON-ENTITY",
        "CAP-SALES-ORDER-HEADER",
        "CAP-SALES-ORDER-STATUS",
    }

    facts = _facts(skill)
    assert set(facts) == ORDER_REQUIRED | ORDER_OPTIONAL
    assert {fact_id for fact_id, fact in facts.items() if fact["required"]} == (
        ORDER_REQUIRED
    )
    assert (
        set(skill["output_contract"]["sufficiency"]["required_fact_sets"][0])
        == ORDER_REQUIRED
    )
    assert skill["output_contract"]["cardinality"] == "zero_or_one"
    assert skill["operation"]["pagination"] == {"strategy": "none"}
    query = skill["operation"]["query_template"]["text"]
    assert re.search(r"ВЫБРАТЬ\s+ПЕРВЫЕ\s+2", query)
    assert skill["operation"]["query_template"]["mcp_limit"]["default"] == 2
    assert len(skill["operation"]["column_bindings"]) == 15

    fixture_transport.configure("sp01 full header", plan_kind="sp01", order_rows=1)
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Статус заказа S2-000001")
    assert turn["outcome"] == "success_with_rows", turn
    call = execute_query_calls(fixture_transport.state())[0]
    arguments = mcp_arguments(call)
    assert arguments["params"] == {"Номер": "S2-000001"}
    assert arguments["limit"] == 2
    assert arguments["include_schema"] is True

    evidence = api.evidence(turn["trace_id"])
    assert {fact["fact_id"] for fact in evidence["facts"]} == (
        ORDER_REQUIRED | ORDER_OPTIONAL
    )
    assert fact_values(evidence, "order.amount") == [3000.0]
    assert fact_values(evidence, "order.currency") == ["RUB"]
    assert fact_values(evidence, "order.payment_percent") == [100.0]
    assert evidence["coverage"]["sufficient"] is True
    assert all(
        requirement["required"] is True
        for requirement in evidence["coverage"]["requirements"]
    )


def test_sp01_zero_and_multiple_rows_keep_not_found_and_ambiguity_distinct(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session = api.create_session()
    fixture_transport.configure("sp01 empty", plan_kind="sp01", order_rows=0)
    empty, _ = api.ask(session["session_id"], "Статус заказа S2-000001")
    assert empty["outcome"] == "success_empty", empty
    assert empty.get("reason") == "not_found"

    fixture_transport.configure("sp01 ambiguous", plan_kind="sp01", order_rows=2)
    ambiguous, _ = api.ask(session["session_id"], "Статус заказа S2-000001 еще раз")
    assert ambiguous["outcome"] == "clarification_required", ambiguous
    assert error_code(ambiguous) == "ENTITY_RESULT_AMBIGUOUS"
    assert api.evidence(ambiguous["trace_id"])["facts"] == []


def test_q054_executes_real_r06_to_stock_binding_without_replanning(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    fixture_transport.configure(
        "q054 composition",
        plan_kind="q054",
        warehouse_rows=2,
        stock_balance=7,
    )
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Какие остатки на розничных складах?")
    assert turn["outcome"] == "success_with_rows", turn

    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    calls = execute_query_calls(state)
    assert len(calls) == 2
    warehouse_call, stock_call = calls
    warehouse_arguments = mcp_arguments(warehouse_call)
    stock_arguments = mcp_arguments(stock_call)
    assert "Справочник.Склады" in warehouse_arguments["query"]
    assert "ТоварыНаСкладах" in stock_arguments["query"]
    assert "Перечисление.ТипыСкладов.РозничныйМагазин" in warehouse_arguments["query"]
    assert "Организаци" not in warehouse_arguments["query"]
    assert "Назначени" not in warehouse_arguments["query"]
    assert warehouse_arguments["params"]["ТолькоРозничные"] is True
    assert warehouse_arguments["params"]["Шаблон"] == "%%"
    assert warehouse_arguments["params"]["Подразделение"] is None

    warehouses = stock_arguments["params"]["Склады"]
    assert [item["УникальныйИдентификатор"] for item in warehouses] == [
        "00000000-0000-4000-8000-000000200001",
        "00000000-0000-4000-8000-000000200002",
    ]
    assert stock_arguments["params"]["Номенклатура"] is None
    assert isinstance(stock_arguments["params"]["Момент"], str)

    evidence = api.evidence(turn["trace_id"])
    assert fact_values(evidence, "warehouse.type") == [
        "Розничный магазин",
        "Розничный магазин",
    ]
    assert fact_values(evidence, "stock.balance") == [7.0, 7.0]
    assert [
        fact["value"]
        for fact in evidence["facts"]
        if fact["step_id"] == "s2" and fact["fact_id"] == "warehouse.ref"
    ] == warehouses

    plan = _planner_document(api, turn["trace_id"])
    assert [step["skill_id"] for step in plan["result"]["steps"]] == [
        WAREHOUSE_SKILL,
        STOCK_SKILL,
    ]
    binding = plan["result"]["steps"][1]["arguments"][0]["binding"]
    assert binding == {
        "source": "step",
        "step_id": "s1",
        "fact_id": "warehouse.ref",
        "cardinality": "many",
    }


def test_inflight_turn_keeps_old_catalog_snapshot_after_hot_reload(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    old_skill = api.export_skill(SHIPMENT_SKILL)
    old_catalog = api.list_skills()
    fixture_transport.configure(
        "hot reload pinned request",
        plan_kind="shipment",
        shipment_rows=1,
        block_boundary="mcp",
    )
    session = api.create_session()
    accepted = api.send_message(session["session_id"], "Покажи реализации за 2024 год")
    fixture_transport.wait_for(lambda state: state["blocked_boundaries"] == ["mcp"])

    try:
        response, replaced = api.import_package(
            replacement_variant(old_skill),
            mode="replace",
            if_match=old_skill["integrity"]["digest"],
        )
        assert response.status == 200, replaced
        assert replaced["status"] == "accepted"
        new_catalog = api.list_skills()
        assert new_catalog["catalog_revision"] == replaced["catalog_revision"]
        assert new_catalog["catalog_revision"] == old_catalog["catalog_revision"] + 1
        assert new_catalog["catalog_snapshot_id"] != old_catalog["catalog_snapshot_id"]
    finally:
        fixture_transport.release()

    old_turn = api.wait_turn(accepted["turn_id"])
    assert old_turn["outcome"] == "success_with_rows", old_turn
    assert old_turn["pinned"] == {
        "catalog_revision": old_catalog["catalog_revision"],
        "catalog_snapshot_id": old_catalog["catalog_snapshot_id"],
    }
    old_evidence = api.evidence(old_turn["trace_id"])
    assert _snapshot_skill(old_evidence, SHIPMENT_SKILL)["version"] == "1.0.0"

    fixture_transport.configure(
        "hot reload next request", plan_kind="shipment", shipment_rows=1
    )
    next_turn, _ = api.ask(
        session["session_id"], "Покажи реализации за 2024 год еще раз"
    )
    assert next_turn["outcome"] == "success_with_rows", next_turn
    assert next_turn["pinned"] == {
        "catalog_revision": new_catalog["catalog_revision"],
        "catalog_snapshot_id": new_catalog["catalog_snapshot_id"],
    }
    new_evidence = api.evidence(next_turn["trace_id"])
    assert _snapshot_skill(new_evidence, SHIPMENT_SKILL)["version"] == "1.0.1"
    next_deepseek = deepseek_calls(fixture_transport.state())
    assert len(next_deepseek) == 1
    card = next(
        item
        for item in next_deepseek[0]["planner_input"]["skill_manifest"]
        if item["skill_id"] == SHIPMENT_SKILL
    )
    assert card["version"] == "1.0.1"
