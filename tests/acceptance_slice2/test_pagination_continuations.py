from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import pytest

from .conftest import RunningApp
from .support import (
    AppClient,
    FixtureClient,
    deepseek_calls,
    execute_query_calls,
    fact_values,
    mcp_arguments,
    rejection,
)
from .synthetic_skills import (
    SHIPMENT_SKILL,
    WAREHOUSE_SKILL,
    catalog_probe_variant,
    replacement_variant,
)


def _shipment_page(
    api: AppClient, fixture: FixtureClient, rows: int
) -> tuple[str, dict[str, Any]]:
    fixture.configure("shipment", plan_kind="shipment", shipment_rows=rows)
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Покажи реализации за 2024 год")
    return session["session_id"], turn


def _handle(turn: dict[str, Any]) -> str:
    continuation = turn["pagination"]["continuation"]
    assert isinstance(continuation, dict), turn
    handle = continuation["handle"]
    assert re.fullmatch(r"page_[A-Za-z0-9_-]{32}", handle)
    return handle


def _import_catalog_probe(api: AppClient, suffix: str) -> dict[str, Any]:
    base = api.export_skill(WAREHOUSE_SKILL)
    response, payload = api.import_package(catalog_probe_variant(base, suffix=suffix))
    assert response.status == 200, payload
    return payload


@pytest.mark.parametrize("row_count", [0, 19, 20, 21])
def test_page_probe_boundaries_and_optional_defaults(
    app: RunningApp,
    fixture_transport: FixtureClient,
    row_count: int,
) -> None:
    session_id, turn = _shipment_page(app.api, fixture_transport, row_count)
    del session_id
    expected_outcome = "success_empty" if row_count == 0 else "success_with_rows"
    assert turn["status"] == "completed", turn
    assert turn["outcome"] == expected_outcome, turn
    assert turn["pagination"] == {
        "shown": min(row_count, 20),
        "page_size": 20,
        "has_more": row_count == 21,
        "continuation": turn["pagination"]["continuation"],
    }
    if row_count == 21:
        _handle(turn)
    else:
        assert turn["pagination"]["continuation"] is None

    calls = execute_query_calls(fixture_transport.state())
    assert len(calls) == 1
    arguments = mcp_arguments(calls[0])
    assert arguments["limit"] == 21
    assert arguments["include_schema"] is True
    assert arguments["params"] == {
        "НачалоПериода": "2024-01-01T00:00:00+03:00",
        "КонецПериода": "2025-01-01T00:00:00+03:00",
        "Клиент": None,
        "Организация": None,
        "Склад": None,
        "ЕстьКурсор": False,
        "ДатаКурсора": None,
        "СсылкаКурсора": None,
    }
    query = arguments["query"]
    assert not re.search(r"\b(?:ПЕРВЫЕ|TOP)\b", query, re.IGNORECASE)


def test_keyset_continuation_is_lossless_stable_and_never_replans(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session_id, turn = _shipment_page(api, fixture_transport, 43)
    turns = [turn]
    pages: list[list[str]] = []
    expiries: list[str] = []

    while True:
        evidence = api.evidence(turn["trace_id"])
        pages.append(fact_values(evidence, "shipment.number"))
        continuation = turn["pagination"]["continuation"]
        if continuation is None:
            break
        expiries.append(continuation["expires_at"])
        response, accepted = api.continue_list(
            session_id, {"continuation_handle": continuation["handle"]}
        )
        assert response.status == 202, accepted
        assert set(accepted) == {"status", "turn_id", "trace_id"}
        assert accepted["status"] == "accepted"
        turn = api.wait_turn(accepted["turn_id"])
        assert turn["outcome"] == "success_with_rows", turn
        turns.append(turn)

    assert [len(page) for page in pages] == [20, 20, 3]
    numbers = [number for page in pages for number in page]
    assert numbers == [f"SHIP-{index:03d}" for index in range(1, 44)]
    assert len(numbers) == len(set(numbers)) == 43
    assert [item["pagination"]["shown"] for item in turns] == [20, 20, 3]
    assert [item["pagination"]["has_more"] for item in turns] == [True, True, False]

    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    calls = execute_query_calls(state)
    assert len(calls) == 3
    assert [mcp_arguments(call)["limit"] for call in calls] == [21, 21, 21]
    params = [mcp_arguments(call)["params"] for call in calls]
    assert params[0]["ЕстьКурсор"] is False
    assert params[1]["ЕстьКурсор"] is True
    assert params[2]["ЕстьКурсор"] is True
    assert params[1]["ДатаКурсора"] == "2024-12-25T12:00:00+03:00"
    assert params[1]["СсылкаКурсора"]["Представление"] == "Shipment 020"
    assert params[2]["СсылкаКурсора"]["Представление"] == "Shipment 040"

    first_completed = datetime.fromisoformat(turns[0]["completed_at"])
    first_expiry = datetime.fromisoformat(expiries[0].replace("Z", "+00:00"))
    assert 1798 <= (first_expiry - first_completed).total_seconds() <= 1801
    response, original = api.get_turn(turns[0]["turn_id"])
    assert response.status == 200
    assert original["pagination"]["continuation"]["expires_at"] == expiries[0]


def test_r06_keyset_continuation_replays_stable_boundary_without_duplicates(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    fixture_transport.configure(
        "warehouse prefix", plan_kind="warehouse", warehouse_rows=43
    )
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Покажи склады")
    pages: list[list[str]] = []
    while True:
        evidence = api.evidence(turn["trace_id"])
        pages.append(fact_values(evidence, "warehouse.name"))
        continuation = turn["pagination"]["continuation"]
        if continuation is None:
            break
        response, accepted = api.continue_list(
            session["session_id"],
            {"continuation_handle": continuation["handle"]},
        )
        assert response.status == 202, accepted
        turn = api.wait_turn(accepted["turn_id"])

    names = [name for page in pages for name in page]
    assert [len(page) for page in pages] == [20, 20, 3]
    assert names == [f"Retail {index:03d}" for index in range(1, 44)]
    assert len(names) == len(set(names))
    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    calls = execute_query_calls(state)
    assert [mcp_arguments(call)["limit"] for call in calls] == [21, 21, 21]
    params = [mcp_arguments(call)["params"] for call in calls]
    assert params[0]["ЕстьКурсор"] is False
    assert params[1]["ЕстьКурсор"] is True
    assert params[2]["ЕстьКурсор"] is True
    assert params[1]["СсылкаКурсора"]["Представление"] == "Retail 020"
    assert params[2]["СсылкаКурсора"]["Представление"] == "Retail 040"


def test_opaque_handle_rejects_tamper_cross_session_extra_skill_and_arguments(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session_id, turn = _shipment_page(api, fixture_transport, 21)
    handle = _handle(turn)
    other_session = api.create_session()["session_id"]

    cases = [
        (session_id, {}, 422, "CONTINUATION_HANDLE_INVALID"),
        (
            session_id,
            {"continuation_handle": "page_bad"},
            422,
            "CONTINUATION_HANDLE_INVALID",
        ),
        (
            session_id,
            {"continuation_handle": "page_" + "A" * 32},
            404,
            "CONTINUATION_NOT_FOUND",
        ),
        (
            session_id,
            {
                "continuation_handle": handle,
                "skill_id": SHIPMENT_SKILL,
            },
            422,
            "CONTINUATION_HANDLE_INVALID",
        ),
        (
            session_id,
            {"continuation_handle": handle, "arguments": {"period": "tampered"}},
            422,
            "CONTINUATION_HANDLE_INVALID",
        ),
        (
            session_id,
            {"continuation_handle": handle, "cursor": {"offset": 20}},
            422,
            "CONTINUATION_HANDLE_INVALID",
        ),
        (
            other_session,
            {"continuation_handle": handle},
            409,
            "CONTINUATION_SESSION_MISMATCH",
        ),
    ]
    for target_session, body, status, code in cases:
        before_requests = fixture_transport.state()["requests"]
        before_response, before_session = api.get_session(target_session)
        assert before_response.status == 200
        response, payload = api.continue_list(target_session, body)
        rejection(response, payload, status, code)
        assert fixture_transport.state()["requests"] == before_requests
        after_response, after_session = api.get_session(target_session)
        assert after_response.status == 200
        assert after_session["messages"] == before_session["messages"]

    tampered = handle[:-1] + ("A" if handle[-1] != "A" else "B")
    response, payload = api.continue_list(session_id, {"continuation_handle": tampered})
    rejection(response, payload, 404, "CONTINUATION_NOT_FOUND")

    response, accepted = api.continue_list(session_id, {"continuation_handle": handle})
    assert response.status == 202, accepted
    api.wait_turn(accepted["turn_id"])
    before = fixture_transport.state()["requests"]
    response, payload = api.continue_list(session_id, {"continuation_handle": handle})
    rejection(response, payload, 409, "CONTINUATION_CONSUMED")
    assert fixture_transport.state()["requests"] == before


def test_concurrent_claim_has_exactly_one_winner(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session_id, turn = _shipment_page(api, fixture_transport, 21)
    handle = _handle(turn)

    def claim() -> tuple[Any, dict[str, Any]]:
        return api.continue_list(session_id, {"continuation_handle": handle})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    statuses = sorted(response.status for response, _ in results)
    assert statuses == [202, 409]
    winner = next(payload for response, payload in results if response.status == 202)
    loser_response, loser = next(item for item in results if item[0].status == 409)
    rejection(loser_response, loser, 409, "CONTINUATION_CONSUMED")
    continued = api.wait_turn(winner["turn_id"])
    assert continued["outcome"] == "success_with_rows", continued
    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    assert len(execute_query_calls(state)) == 2


def test_accepted_continuation_stays_consumed_after_query_failure(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session_id, turn = _shipment_page(api, fixture_transport, 21)
    handle = _handle(turn)
    fixture_transport.configure(
        "continued page query failure",
        plan_kind="shipment",
        shipment_rows=21,
        query_error_at=1,
    )

    response, accepted = api.continue_list(session_id, {"continuation_handle": handle})
    assert response.status == 202, accepted
    failed = api.wait_turn(accepted["turn_id"])
    assert failed["outcome"] == "query_error", failed
    assert len(deepseek_calls(fixture_transport.state())) == 0
    assert len(execute_query_calls(fixture_transport.state())) == 1

    before = fixture_transport.state()["requests"]
    response, payload = api.continue_list(session_id, {"continuation_handle": handle})
    rejection(response, payload, 409, "CONTINUATION_CONSUMED")
    assert fixture_transport.state()["requests"] == before


@pytest.mark.parametrize("drift", ["snapshot", "skill"])
def test_continuation_is_bound_to_exact_catalog_snapshot_and_skill_revision(
    app: RunningApp,
    fixture_transport: FixtureClient,
    drift: str,
) -> None:
    api = app.api
    session_id, turn = _shipment_page(api, fixture_transport, 21)
    handle = _handle(turn)
    before_calls = fixture_transport.state()["requests"]
    if drift == "snapshot":
        _import_catalog_probe(api, "snapshot")
    else:
        exported = api.export_skill(SHIPMENT_SKILL)
        response, payload = api.import_package(
            replacement_variant(exported),
            mode="replace",
            if_match=exported["integrity"]["digest"],
        )
        assert response.status == 200, payload

    response, payload = api.continue_list(session_id, {"continuation_handle": handle})
    rejection(response, payload, 409, "CONTINUATION_CATALOG_CHANGED")
    assert fixture_transport.state()["requests"] == before_calls


def test_continuation_is_bound_to_database_marker_across_restart(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    session_id, turn = _shipment_page(app.api, fixture_transport, 21)
    handle = _handle(turn)
    catalog_before = app.api.list_skills()
    app.restart(marker="slice2-marker-v2")
    catalog_after = app.api.list_skills()
    assert catalog_after["catalog_snapshot_id"] == catalog_before["catalog_snapshot_id"]
    before = fixture_transport.state()["requests"]

    response, payload = app.api.continue_list(
        session_id, {"continuation_handle": handle}
    )
    rejection(response, payload, 409, "CONTINUATION_MARKER_CHANGED")
    assert fixture_transport.state()["requests"] == before


@pytest.mark.skipif(
    os.getenv("SLICE2_RUN_WALL_CLOCK_TTL") != "1",
    reason="30-minute black-box TTL boundary requires SLICE2_RUN_WALL_CLOCK_TTL=1",
)
def test_continuation_expires_at_exact_public_boundary(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    session_id, turn = _shipment_page(app.api, fixture_transport, 21)
    continuation = turn["pagination"]["continuation"]
    expires_at = datetime.fromisoformat(
        continuation["expires_at"].replace("Z", "+00:00")
    )
    delay = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
    time.sleep(delay + 0.05)
    before = fixture_transport.state()["requests"]
    response, payload = app.api.continue_list(
        session_id, {"continuation_handle": continuation["handle"]}
    )
    rejection(response, payload, 410, "CONTINUATION_EXPIRED")
    assert fixture_transport.state()["requests"] == before
