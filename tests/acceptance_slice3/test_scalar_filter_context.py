from __future__ import annotations

from typing import Any

from .support import AppClient, FixtureClient, RunningApp, context_slots, slot_by_key
from .synthetic_package import FIXED_MOMENT, invalid_policy_package


def _seed_entity_and_moment(
    api: AppClient, fixture: FixtureClient
) -> tuple[str, dict[str, Any]]:
    fixture.configure("resolve_one", asset_count=1, snapshot_moment=FIXED_MOMENT)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Выбери лазурный актив и зафиксируй снимок",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    return session["session_id"], api.session(session["session_id"])


def test_confirmed_scalar_is_retained_but_raw_value_is_not_public(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, session = _seed_entity_and_moment(api, fixture)
    scalar = slot_by_key(session, "filter.synthetic_snapshot")
    assert scalar["semantic_type"] == "synthetic.snapshot"
    assert scalar["value_type"] == "datetime"
    assert scalar["policy_mode"] == "confirmed_filter"
    assert scalar["member_count"] == 1
    public = str(api.session(session_id)["context"])
    assert FIXED_MOMENT not in public
    assert "_objectRef" not in public


def test_snapshot_moment_is_byte_stable_across_two_followups(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, session = _seed_entity_and_moment(api, fixture)
    original = slot_by_key(session, "filter.synthetic_snapshot")
    observed: list[str] = []
    observed_bytes: list[str] = []
    for question in (
        "Покажи повторно этот снимок",
        "Поменяй только детализацию этого снимка",
    ):
        fixture.configure("followup_scalar", clear_requests=False)
        turn = api.ask(
            session_id,
            question,
            context_version=api.session(session_id)["context_version"],
        )
        assert turn["outcome"] == "success_with_rows"
        call = fixture.requests("mcp_execute_query")[-1]
        observed.append(call["arguments"]["params"]["Момент"])
        observed_bytes.append(call["parameter_bytes"]["Момент"])
    assert observed == [FIXED_MOMENT, FIXED_MOMENT]
    exact = ('"' + FIXED_MOMENT + '"').encode("utf-8").hex()
    assert observed_bytes == [exact, exact]
    active = slot_by_key(api.session(session_id), "filter.synthetic_snapshot")
    assert active["handle"] != original["handle"]
    assert active["presentation"] == original["presentation"]


def test_scalar_context_survives_restart_without_recomputation(
    app: RunningApp, fixture: FixtureClient
) -> None:
    assert app.api is not None
    session_id, _ = _seed_entity_and_moment(app.api, fixture)
    app.restart(now="2028-01-01T00:00:00Z")
    assert app.api is not None
    fixture.configure("followup_scalar")
    turn = app.api.ask(
        session_id,
        "Покажи тот же сохраненный снимок спустя время",
        context_version=app.api.session(session_id)["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    params = fixture.requests("mcp_execute_query")[-1]["arguments"]["params"]
    assert params["Момент"] == FIXED_MOMENT


def test_entity_fact_cannot_be_imported_under_confirmed_filter_policy(
    app_factory,
) -> None:
    clean = app_factory(name="invalid-policy", import_package=False)
    assert clean.api is not None
    response = clean.api.import_package(invalid_policy_package("entity_as_scalar"))
    assert response.status == 422
    codes = {error["code"] for error in response.json()["errors"]}
    assert "CONTEXT_EXPORT_MODE_INVALID" in codes


def test_unknown_policy_fact_is_rejected_at_import(app_factory) -> None:
    clean = app_factory(name="unknown-policy", import_package=False)
    assert clean.api is not None
    response = clean.api.import_package(invalid_policy_package("unknown_fact"))
    assert response.status == 422
    codes = {error["code"] for error in response.json()["errors"]}
    assert "CONTEXT_EXPORT_POLICY_INVALID" in codes


def test_only_declared_slots_are_active(api: AppClient, fixture: FixtureClient) -> None:
    _, session = _seed_entity_and_moment(api, fixture)
    assert {slot["slot_key"] for slot in context_slots(session)} == {
        "selection.synthetic_asset",
        "filter.synthetic_snapshot",
    }
