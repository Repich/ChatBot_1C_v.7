from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import pytest

from .conftest import RunningApp
from .support import AppClient, FixtureClient, rejection


def _completed_turn(
    api: AppClient, fixture: FixtureClient
) -> tuple[str, dict[str, Any]]:
    fixture.configure("maintenance data", plan_kind="warehouse", row_count=1)
    session = api.create_session()
    turn, _ = api.ask(session["session_id"], "Покажи склады")
    assert turn["outcome"] == "success_with_rows", turn
    return session["session_id"], turn


def _preview(api: AppClient, scopes: list[str]) -> dict[str, Any]:
    response, payload = api.maintenance({"mode": "preview", "scopes": scopes})
    assert response.status == 200, payload
    assert payload["status"] == "preview", payload
    assert set(payload["counts"]) == {"sessions", "traces", "raw_payloads"}
    assert all(
        type(value) is int and value >= 0 for value in payload["counts"].values()
    )
    assert re.fullmatch(r"clear_[A-Za-z0-9_-]{32}", payload["confirmation_token"])
    return payload


def _confirm(
    api: AppClient, preview: dict[str, Any], scopes: list[str] | None = None
) -> dict[str, Any]:
    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": preview["scopes"] if scopes is None else scopes,
            "confirmation_token": preview["confirmation_token"],
        }
    )
    assert response.status == 200, payload
    assert payload["status"] == "cleared", payload
    return payload


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"mode": "preview", "scopes": []},
        {"mode": "preview", "scopes": ["sessions", "sessions"]},
        {"mode": "preview", "scopes": ["catalog"]},
        {"mode": "preview", "scopes": ["sessions"], "confirmation_token": "x"},
        {"mode": "confirm", "scopes": ["sessions"]},
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": "clear_bad",
        },
        {"mode": "delete", "scopes": ["sessions"]},
    ],
)
def test_maintenance_rejects_every_invalid_dto_with_exact_code(
    app: RunningApp,
    body: dict[str, Any],
) -> None:
    before_response, before = app.api.list_sessions()
    assert before_response.status == 200
    response, payload = app.api.maintenance(body)
    rejection(response, payload, 422, "CLEAR_SCOPES_INVALID")
    after_response, after = app.api.list_sessions()
    assert after_response.status == 200
    assert after == before


def test_preview_confirm_canonical_union_counts_ttl_replay_and_catalog_survival(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    _, turn = _completed_turn(api, fixture_transport)
    catalog_before = api.list_skills()
    requested_at = datetime.now(timezone.utc)
    preview = _preview(api, ["raw_payloads", "sessions", "traces"])
    assert preview["scopes"] == ["sessions", "traces", "raw_payloads"]
    assert preview["counts"]["sessions"] == 1
    assert preview["counts"]["traces"] >= 1
    assert preview["counts"]["raw_payloads"] >= 1
    expires_at = datetime.fromisoformat(preview["expires_at"].replace("Z", "+00:00"))
    assert 299 <= (expires_at - requested_at).total_seconds() <= 301

    cleared = _confirm(api, preview, ["traces", "sessions", "raw_payloads"])
    assert cleared["scopes"] == ["sessions", "traces", "raw_payloads"]
    assert cleared["deleted"] == preview["counts"]
    response, sessions = api.list_sessions()
    assert response.status == 200
    assert sessions == {"sessions": []}
    assert api.diagnostic(turn["trace_id"]).status == 404
    catalog_after = api.list_skills()
    assert catalog_after == catalog_before

    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": preview["scopes"],
            "confirmation_token": preview["confirmation_token"],
        }
    )
    rejection(response, payload, 409, "CLEAR_CONFIRMATION_CONSUMED")


def test_forged_tampered_and_scope_mismatch_are_atomic(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    session_id, _ = _completed_turn(api, fixture_transport)
    preview = _preview(api, ["sessions"])
    forged = "clear_" + "A" * 32
    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": forged,
        }
    )
    rejection(response, payload, 404, "CLEAR_CONFIRMATION_NOT_FOUND")

    tampered = preview["confirmation_token"][:-1] + (
        "A" if preview["confirmation_token"][-1] != "A" else "B"
    )
    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": tampered,
        }
    )
    rejection(response, payload, 404, "CLEAR_CONFIRMATION_NOT_FOUND")

    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["traces"],
            "confirmation_token": preview["confirmation_token"],
        }
    )
    rejection(response, payload, 409, "CLEAR_SCOPE_MISMATCH")
    session_response, _ = api.get_session(session_id)
    assert session_response.status == 200

    cleared = _confirm(api, preview)
    assert cleared["deleted"] == preview["counts"]


def test_preview_becomes_stale_when_exact_target_set_changes(
    app: RunningApp,
) -> None:
    api = app.api
    first = api.create_session()
    preview = _preview(api, ["sessions"])
    second = api.create_session()
    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": preview["confirmation_token"],
        }
    )
    rejection(response, payload, 409, "CLEAR_PREVIEW_STALE")
    for session_id in (first["session_id"], second["session_id"]):
        session_response, _ = api.get_session(session_id)
        assert session_response.status == 200


def test_concurrent_confirmation_commits_exactly_once(
    app: RunningApp,
) -> None:
    api = app.api
    api.create_session()
    preview = _preview(api, ["sessions"])

    def confirm() -> tuple[Any, dict[str, Any]]:
        return api.maintenance(
            {
                "mode": "confirm",
                "scopes": ["sessions"],
                "confirmation_token": preview["confirmation_token"],
            }
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))

    assert sorted(response.status for response, _ in results) == [200, 409]
    committed = next(payload for response, payload in results if response.status == 200)
    assert committed == {
        "status": "cleared",
        "scopes": ["sessions"],
        "deleted": preview["counts"],
    }
    rejected_response, rejected = next(
        item for item in results if item[0].status == 409
    )
    rejection(
        rejected_response,
        rejected,
        409,
        "CLEAR_CONFIRMATION_CONSUMED",
    )
    response, sessions = api.list_sessions()
    assert response.status == 200
    assert sessions == {"sessions": []}


def test_preview_and_confirm_reject_active_target_without_deleting(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    api = app.api
    fixture_transport.configure(
        "active preview", plan_kind="warehouse", block_boundary="deepseek"
    )
    active_session = api.create_session()
    active = api.send_message(active_session["session_id"], "Покажи склады")
    fixture_transport.wait_for(
        lambda state: state["blocked_boundaries"] == ["deepseek"]
    )
    response, payload = api.maintenance({"mode": "preview", "scopes": ["sessions"]})
    rejection(response, payload, 409, "CLEAR_TARGET_ACTIVE")
    fixture_transport.release()
    api.wait_turn(active["turn_id"])
    session_response, _ = api.get_session(active_session["session_id"])
    assert session_response.status == 200

    preview = _preview(api, ["sessions"])
    fixture_transport.configure(
        "active confirm", plan_kind="warehouse", block_boundary="deepseek"
    )
    second_session = api.create_session()
    second = api.send_message(second_session["session_id"], "Покажи склады снова")
    fixture_transport.wait_for(
        lambda state: state["blocked_boundaries"] == ["deepseek"]
    )
    response, payload = api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": preview["confirmation_token"],
        }
    )
    rejection(response, payload, 409, "CLEAR_TARGET_ACTIVE")
    fixture_transport.release()
    api.wait_turn(second["turn_id"])
    for session_id in (active_session["session_id"], second_session["session_id"]):
        session_response, _ = api.get_session(session_id)
        assert session_response.status == 200


@pytest.mark.parametrize("scope", ["traces", "raw_payloads"])
def test_trace_and_raw_payload_scope_closure_preserves_session_summary(
    app: RunningApp,
    fixture_transport: FixtureClient,
    scope: str,
) -> None:
    api = app.api
    session_id, turn = _completed_turn(api, fixture_transport)
    preview = _preview(api, [scope])
    assert preview["counts"]["sessions"] == 0
    assert preview["counts"][scope] >= 1
    cleared = _confirm(api, preview)
    assert cleared["deleted"] == preview["counts"]

    session_response, session = api.get_session(session_id)
    assert session_response.status == 200
    assert any(message["turn_id"] == turn["turn_id"] for message in session["messages"])
    turn_response, persisted_turn = api.get_turn(turn["turn_id"])
    assert turn_response.status == 200
    assert persisted_turn["outcome"] == "success_with_rows"
    if scope == "traces":
        assert api.diagnostic(turn["trace_id"]).status == 404
    else:
        assert api.diagnostic(turn["trace_id"]).status == 200


@pytest.mark.skipif(
    os.getenv("SLICE2_RUN_WALL_CLOCK_TTL") != "1",
    reason="5-minute black-box TTL boundary requires SLICE2_RUN_WALL_CLOCK_TTL=1",
)
def test_maintenance_confirmation_expires_at_exact_public_boundary(
    app: RunningApp,
) -> None:
    app.api.create_session()
    preview = _preview(app.api, ["sessions"])
    expires_at = datetime.fromisoformat(preview["expires_at"].replace("Z", "+00:00"))
    delay = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
    time.sleep(delay + 0.05)
    response, payload = app.api.maintenance(
        {
            "mode": "confirm",
            "scopes": ["sessions"],
            "confirmation_token": preview["confirmation_token"],
        }
    )
    rejection(response, payload, 410, "CLEAR_CONFIRMATION_EXPIRED")
