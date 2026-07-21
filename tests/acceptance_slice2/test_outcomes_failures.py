from __future__ import annotations

from typing import Any

import pytest

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
    EMPTY_SKILLS,
    ORDER_SKILL,
    PARTIAL_SKILL,
    SHIPMENT_SKILL,
    STOCK_SKILL,
    TRUNCATION_ERROR_SKILL,
    ZERO_SKILL,
    empty_variant,
    truncation_variant,
    zero_aggregate_variant,
)


def _import(api: AppClient, document: bytes) -> dict[str, Any]:
    response, payload = api.import_package(document)
    assert response.status == 200, payload
    assert payload["status"] == "accepted", payload
    return payload


def _ask(
    api: AppClient, scenario: str, fixture: FixtureClient, **options: Any
) -> dict[str, Any]:
    fixture.configure(scenario, **options)
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], f"slice2 acceptance {scenario}")
    assert turn["trace_id"]
    assert turn["outcome"] is not None
    return turn


def test_all_eight_execution_outcomes_are_observably_distinct(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    stock = api.export_skill(STOCK_SKILL)
    _import(api, zero_aggregate_variant(stock))

    turns = {
        "success_with_rows": _ask(
            api,
            "warehouse",
            fixture_transport,
            plan_kind="warehouse",
            row_count=1,
        ),
        "success_empty": _ask(
            api,
            "warehouse",
            fixture_transport,
            plan_kind="warehouse",
            row_count=0,
        ),
        "zero_aggregate": _ask(
            api,
            "zero aggregate",
            fixture_transport,
            plan_kind="stock",
            skill_id=ZERO_SKILL,
            row_count=1,
            stock_balance=0,
        ),
        "partial": _ask(
            api,
            "q054",
            fixture_transport,
            plan_kind="q054",
            warehouse_rows=2,
            query_error_at=2,
        ),
        "query_error": _ask(
            api,
            "warehouse query error",
            fixture_transport,
            plan_kind="warehouse",
            query_error_at=1,
        ),
        "mcp_unavailable": _ask(
            api,
            "warehouse mcp outage",
            fixture_transport,
            plan_kind="warehouse",
            mcp_failures=2,
        ),
        "llm_unavailable": _ask(
            api,
            "warehouse llm outage",
            fixture_transport,
            plan_kind="warehouse",
            deepseek_failures=2,
        ),
        "contract_error": _ask(
            api,
            "warehouse schema mismatch",
            fixture_transport,
            plan_kind="warehouse",
            missing_column_at=1,
        ),
    }

    assert turns["partial"]["outcome"] == "partial", turns["partial"]
    assert {turn["outcome"] for turn in turns.values()} == set(turns)
    assert all(turn["status"] in {"completed", "failed"} for turn in turns.values())
    assert error_code(turns["query_error"]) == "QUERY_ERROR"
    assert error_code(turns["mcp_unavailable"]) == "MCP_UNAVAILABLE"
    assert error_code(turns["llm_unavailable"]) == "LLM_UNAVAILABLE"
    assert error_code(turns["contract_error"]) == "MCP_COLUMN_CONTRACT_MISMATCH"

    zero_evidence = api.evidence(turns["zero_aggregate"]["trace_id"])
    assert fact_values(zero_evidence, "stock.balance") == [0.0]
    assert zero_evidence["facts"], zero_evidence

    partial_evidence = api.evidence(turns["partial"]["trace_id"])
    assert fact_values(partial_evidence, "warehouse.ref")
    assert partial_evidence["errors"][0]["code"] == "QUERY_ERROR"
    assert partial_evidence["coverage"]["sufficient"] is False

    for outcome in (
        "query_error",
        "mcp_unavailable",
        "llm_unavailable",
        "contract_error",
    ):
        text = turns[outcome]["assistant_message"]["text"]
        assert turns[outcome]["trace_id"] in text
        assert "данные не найдены" not in text.casefold()
    assert "DeepSeek" in turns["llm_unavailable"]["assistant_message"]["text"]
    assert "MCP" in turns["mcp_unavailable"]["assistant_message"]["text"]


@pytest.mark.parametrize(
    ("wrapper", "expected"),
    [
        ("structured_and_text", "success_with_rows"),
        ("structured", "success_with_rows"),
        ("text", "success_with_rows"),
        ("ambiguous_text", "contract_error"),
        ("conflicting_text", "contract_error"),
        ("non_json_text", "contract_error"),
        ("nested", "contract_error"),
    ],
)
def test_valid_and_malformed_mcp_wrappers_fail_closed(
    app: RunningApp,
    fixture_transport: FixtureClient,
    wrapper: str,
    expected: str,
) -> None:
    turn = _ask(
        app.api,
        f"mcp wrapper {wrapper}",
        fixture_transport,
        plan_kind="warehouse",
        mcp_wrapper=wrapper,
        row_count=1,
    )
    assert turn["outcome"] == expected, turn
    if expected == "contract_error":
        assert error_code(turn) == "MCP_ENVELOPE_INVALID"
        assert "Retail 001" not in turn["assistant_message"]["text"]


def test_retry_budget_and_dependency_outages(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session = api.create_session()

    fixture_transport.configure(
        "deepseek retry", plan_kind="warehouse", deepseek_failures=1
    )
    llm_retry, _ = api.ask(session["session_id"], "покажи склады")
    assert llm_retry["outcome"] == "success_with_rows", llm_retry
    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 2
    assert len(execute_query_calls(state)) == 1

    fixture_transport.configure("mcp retry", plan_kind="warehouse", mcp_failures=1)
    mcp_retry, _ = api.ask(session["session_id"], "покажи склады снова")
    assert mcp_retry["outcome"] == "success_with_rows", mcp_retry
    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    assert len(execute_query_calls(state)) == 2
    evidence = api.evidence(mcp_retry["trace_id"])
    assert evidence["steps"][0]["attempts"] == 2

    fixture_transport.configure(
        "query failures are not retried", plan_kind="warehouse", query_error_at=1
    )
    query_error, _ = api.ask(session["session_id"], "покажи склады еще раз")
    assert query_error["outcome"] == "query_error", query_error
    assert len(execute_query_calls(fixture_transport.state())) == 1

    fixture_transport.configure("recovery", plan_kind="warehouse", row_count=1)
    recovered, _ = api.ask(session["session_id"], "проверь восстановление")
    assert recovered["outcome"] == "success_with_rows", recovered
    response, persisted = api.get_session(session["session_id"])
    assert response.status == 200
    assert len([item for item in persisted["messages"] if item["role"] == "user"]) == 4


@pytest.mark.parametrize(
    ("semantics", "expected_outcome", "expected_code", "expected_reason"),
    [
        ("confirmed_not_found", "success_empty", None, "not_found"),
        ("confirmed_no_rows", "success_empty", None, "no_rows"),
        (
            "not_applicable",
            "contract_error",
            "RESULT_EMPTY_SEMANTICS_NOT_APPLICABLE",
            None,
        ),
        ("error_if_empty", "contract_error", "RESULT_EMPTY_FORBIDDEN", None),
    ],
)
@pytest.mark.parametrize("empty_shape", ["zero_rows", "null_sentinel"])
def test_zero_rows_and_null_sentinel_follow_exact_empty_semantics(
    app: RunningApp,
    fixture_transport: FixtureClient,
    semantics: str,
    expected_outcome: str,
    expected_code: str | None,
    expected_reason: str | None,
    empty_shape: str,
) -> None:
    api = app.api
    order = api.export_skill(ORDER_SKILL)
    _import(api, empty_variant(order, semantics))
    options: dict[str, Any] = {
        "plan_kind": "sp01",
        "skill_id": EMPTY_SKILLS[semantics],
        "row_count": 0,
    }
    if empty_shape == "null_sentinel":
        options.update(row_count=1, null_sentinel=True)
    turn = _ask(
        api,
        f"empty {semantics} {empty_shape}",
        fixture_transport,
        **options,
    )

    assert turn["outcome"] == expected_outcome, turn
    assert error_code(turn) == expected_code
    assert "Retail" not in turn["assistant_message"]["text"]
    evidence = api.evidence(turn["trace_id"])
    assert evidence["facts"] == []
    assert evidence["context_exports"] == []
    if expected_reason is not None:
        assert turn.get("reason") == expected_reason, (
            "terminal success_empty must expose the stable reason from SB-01"
        )


def test_optional_null_is_a_row_required_null_is_contract_error_and_zero_is_not_empty(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    fixture_transport.configure(
        "optional department null",
        plan_kind="warehouse",
        row_count=1,
        warehouse_department_null=True,
    )
    session = api.create_session()
    optional, _ = api.ask(session["session_id"], "покажи склады")
    assert optional["outcome"] == "success_with_rows", optional
    optional_evidence = api.evidence(optional["trace_id"])
    assert fact_values(optional_evidence, "warehouse.ref")
    assert fact_values(optional_evidence, "warehouse.department") == []

    fixture_transport.configure(
        "required null", plan_kind="warehouse", row_count=1, null_required_at=1
    )
    required, _ = api.ask(session["session_id"], "покажи склады повторно")
    assert required["outcome"] == "contract_error", required
    assert error_code(required) == "RESULT_REQUIRED_FACT_NULL"
    assert "Retail 001" not in required["assistant_message"]["text"]
    required_evidence = api.evidence(required["trace_id"])
    assert required_evidence["facts"] == []
    assert required_evidence["context_exports"] == []

    stock = api.export_skill(STOCK_SKILL)
    _import(api, zero_aggregate_variant(stock))
    exported_zero = api.export_skill(ZERO_SKILL)
    assert exported_zero["operation"]["pagination"] == {"strategy": "none"}
    assert exported_zero["output_contract"]["cardinality"] == "aggregate"
    assert "row_identity_fact_ids" not in exported_zero["output_contract"]
    assert exported_zero["operation"]["query_template"]["mcp_limit"] == {
        "default": 2,
        "maximum": 2,
    }
    aggregate_query = exported_zero["operation"]["query_template"]["text"]
    assert "СУММА(" in aggregate_query.upper()
    assert "ПЕРВЫЕ" not in aggregate_query.upper()
    assert "TOP" not in aggregate_query.upper()
    assert "КУРСОР" not in aggregate_query.upper()
    fixture_transport.configure(
        "typed zero",
        plan_kind="stock",
        skill_id=ZERO_SKILL,
        row_count=1,
        stock_balance=0,
    )
    zero, _ = api.ask(session["session_id"], "проверь нулевой агрегат")
    assert zero["outcome"] == "zero_aggregate", zero
    zero_evidence = api.evidence(zero["trace_id"])
    assert fact_values(zero_evidence, "stock.balance") == [0.0]
    assert zero_evidence["steps"][0]["collection_scope"] == "complete_set"
    assert zero_evidence["coverage"]["sufficient"] is True
    calls = execute_query_calls(fixture_transport.state())
    assert len(calls) == 1
    arguments = mcp_arguments(calls[0])
    assert arguments["limit"] == 2
    assert set(arguments["params"]) == {"Момент"}


def test_partial_and_contract_error_boundary_discards_untrusted_rendering(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session = api.create_session()
    fixture_transport.configure(
        "required second branch query failure",
        plan_kind="q054",
        warehouse_rows=2,
        query_error_at=2,
    )
    partial, _ = api.ask(session["session_id"], "остатки на розничных складах")
    assert partial["outcome"] == "partial", partial
    partial_evidence = api.evidence(partial["trace_id"])
    assert fact_values(partial_evidence, "warehouse.type") == [
        "Розничный магазин",
        "Розничный магазин",
    ]
    assert partial_evidence["errors"][0]["code"] == "QUERY_ERROR"
    assert partial_evidence["coverage"]["sufficient"] is False

    fixture_transport.configure(
        "required second branch schema failure",
        plan_kind="q054",
        warehouse_rows=2,
        missing_column_at=2,
    )
    contract, _ = api.ask(session["session_id"], "остатки на розничных складах снова")
    assert contract["outcome"] == "contract_error", contract
    assert error_code(contract) == "MCP_COLUMN_CONTRACT_MISMATCH"
    text = contract["assistant_message"]["text"]
    assert "Retail 001" not in text
    assert "Розничный магазин" not in text
    contract_evidence = api.evidence(contract["trace_id"])
    assert contract_evidence["context_exports"] == []

    fixture_transport.configure(
        "single required branch query failure", plan_kind="warehouse", query_error_at=1
    )
    query_error, _ = api.ask(session["session_id"], "покажи склады")
    assert query_error["outcome"] == "query_error", query_error
    assert api.evidence(query_error["trace_id"])["facts"] == []


def test_declared_truncation_policies_separate_partial_from_contract_error(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    shipment = api.export_skill(SHIPMENT_SKILL)
    _import(
        api,
        truncation_variant(shipment, policy="partial_until_all_pages"),
    )
    _import(
        api,
        truncation_variant(shipment, policy="error_if_truncated"),
    )
    session = api.create_session()

    fixture_transport.configure(
        "partial truncation",
        plan_kind="shipment",
        skill_id=PARTIAL_SKILL,
        shipment_rows=21,
    )
    partial, _ = api.ask(session["session_id"], "Покажи частичную страницу")
    assert partial["outcome"] == "partial", partial
    assert partial["pagination"]["shown"] == 20
    assert partial["pagination"]["has_more"] is True
    assert partial["pagination"]["continuation"] is not None
    partial_evidence = api.evidence(partial["trace_id"])
    assert len(fact_values(partial_evidence, "shipment.ref")) == 20
    assert partial_evidence["coverage"]["sufficient"] is False

    fixture_transport.configure(
        "forbidden truncation",
        plan_kind="shipment",
        skill_id=TRUNCATION_ERROR_SKILL,
        shipment_rows=21,
    )
    forbidden, _ = api.ask(session["session_id"], "Покажи запрещенную страницу")
    assert forbidden["outcome"] == "contract_error", forbidden
    assert error_code(forbidden) == "RESULT_TRUNCATED_FORBIDDEN"
    pagination = forbidden.get("pagination")
    if pagination is not None:
        assert pagination == {
            "shown": 0,
            "page_size": 20,
            "has_more": False,
            "continuation": None,
        }
    forbidden_evidence = api.evidence(forbidden["trace_id"])
    assert forbidden_evidence["facts"] == []
    assert forbidden_evidence["context_exports"] == []


def test_overall_deadline_stops_retries_and_same_session_recovers_next_turn(
    app_factory: AppFactory,
    fixture_transport: FixtureClient,
) -> None:
    runtime = app_factory.start(deadline=1)
    api = runtime.api
    session = api.create_session()

    fixture_transport.configure(
        "llm deadline", plan_kind="warehouse", deepseek_delay_ms=1400
    )
    llm_timeout, llm_elapsed = api.ask(
        session["session_id"], "покажи склады при задержке", timeout=5
    )
    assert llm_timeout["outcome"] == "llm_unavailable", llm_timeout
    assert error_code(llm_timeout) == "LLM_UNAVAILABLE"
    assert llm_elapsed < 2.2
    assert len(deepseek_calls(fixture_transport.state())) == 1

    fixture_transport.configure("llm recovery", plan_kind="warehouse", row_count=1)
    llm_recovered, _ = api.ask(session["session_id"], "покажи склады после ошибки")
    assert llm_recovered["outcome"] == "success_with_rows", llm_recovered

    fixture_transport.configure(
        "mcp deadline", plan_kind="warehouse", mcp_delay_ms=1400
    )
    mcp_timeout, mcp_elapsed = api.ask(
        session["session_id"], "повтори с задержкой MCP", timeout=5
    )
    assert mcp_timeout["outcome"] == "mcp_unavailable", mcp_timeout
    assert error_code(mcp_timeout) == "MCP_UNAVAILABLE"
    assert mcp_elapsed < 2.2
    assert len(execute_query_calls(fixture_transport.state())) == 1

    fixture_transport.configure("mcp recovery", plan_kind="warehouse", row_count=1)
    mcp_recovered, _ = api.ask(session["session_id"], "повтори после ошибки MCP")
    assert mcp_recovered["outcome"] == "success_with_rows", mcp_recovered
    response, persisted = api.get_session(session["session_id"])
    assert response.status == 200
    assert len([item for item in persisted["messages"] if item["role"] == "user"]) == 4
