from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .support import (
    AppClient,
    FixtureClient,
    RunningApp,
    context_slots,
    error_code,
)


def _ambiguous(
    api: AppClient, fixture: FixtureClient, *, count: int = 2
) -> tuple[str, dict[str, Any]]:
    fixture.configure("resolve_one", asset_count=count)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Выбери один лазурный артефакт и покажи снимок",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "clarification_required"
    return session["session_id"], turn["clarification"]


def _choice_payload(clarification: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return {
        "handle": clarification["handle"],
        "action": "choose",
        "choice_id": clarification["choices"][index]["choice_id"],
    }


def test_choose_resumes_stored_dag_without_deepseek_or_resolver_repeat(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    before_llm = len(fixture.requests("deepseek"))
    before_mcp = len(fixture.requests("mcp_execute_query"))
    session = api.session(session_id)
    resumed = api.ask(
        session_id,
        "Второй вариант",
        context_version=session["context_version"],
        clarification=_choice_payload(clarification, 1),
    )
    assert resumed["outcome"] == "success_with_rows"
    assert len(fixture.requests("deepseek")) == before_llm
    assert len(fixture.requests("mcp_execute_query")) == before_mcp + 1
    slot = next(
        slot
        for slot in context_slots(api.session(session_id))
        if slot["slot_key"] == "selection.synthetic_asset"
    )
    assert slot["member_count"] == 1


def test_pending_survives_process_restart(
    app: RunningApp, fixture: FixtureClient
) -> None:
    assert app.api is not None
    session_id, clarification = _ambiguous(app.api, fixture)
    app.restart()
    assert app.api is not None
    restored = app.api.session(session_id)
    assert (
        restored["context"]["pending_clarification"]["handle"]
        == (clarification["handle"])
    )
    resumed = app.api.ask(
        session_id,
        "Первый",
        context_version=restored["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert resumed["outcome"] == "success_with_rows"


def test_pending_is_one_use_and_reuse_has_no_external_calls(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    current = api.session(session_id)
    first = api.submit(
        session_id,
        "Первый",
        context_version=current["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert first.status == 202
    api.events(first.json()["turn_id"])
    calls = len(fixture.requests())
    reused = api.submit(
        session_id,
        "Снова первый",
        context_version=api.session(session_id)["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert reused.status == 409
    assert error_code(reused) == "CLARIFICATION_CONSUMED"
    assert len(fixture.requests()) == calls


def test_wrong_session_is_rejected_without_external_calls(
    api: AppClient, fixture: FixtureClient
) -> None:
    _, clarification = _ambiguous(api, fixture)
    other = api.create_session("Other")
    calls = len(fixture.requests())
    response = api.submit(
        other["session_id"],
        "Первый",
        context_version=other["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert response.status == 409
    assert error_code(response) == "CLARIFICATION_SESSION_MISMATCH"
    assert len(fixture.requests()) == calls


def test_expiry_at_exact_thirty_minute_boundary_uses_controlled_clock(
    app: RunningApp, fixture: FixtureClient
) -> None:
    assert app.api is not None
    session_id, clarification = _ambiguous(app.api, fixture)
    app.restart(now="2026-07-21T12:30:00Z")
    assert app.api is not None
    current = app.api.session(session_id)
    calls = len(fixture.requests())
    response = app.api.submit(
        session_id,
        "Первый",
        context_version=current["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert response.status == 410
    assert error_code(response) == "CLARIFICATION_EXPIRED"
    assert len(fixture.requests()) == calls


def test_context_version_mismatch_is_deterministic(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    calls = len(fixture.requests())
    response = api.submit(
        session_id,
        "Первый",
        context_version=999,
        clarification=_choice_payload(clarification),
    )
    assert response.status == 409
    assert error_code(response) == "CONTEXT_VERSION_CONFLICT"
    assert len(fixture.requests()) == calls


def test_catalog_change_invalidates_pending_before_external_calls(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    catalog = api.http.request("GET", "/api/v1/skills").json()
    batch = next(
        skill
        for skill in catalog["skills"]
        if skill["skill_id"] == "qa.synthetic.asset.batch"
    )
    response = api.http.request(
        "DELETE",
        "/api/v1/skills/qa.synthetic.asset.batch",
        headers={"If-Match": batch["digest"]},
    )
    assert response.status == 200, response.body.decode("utf-8", "replace")
    calls = len(fixture.requests())
    current = api.session(session_id)
    rejected = api.submit(
        session_id,
        "Первый",
        context_version=current["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert rejected.status == 409
    assert error_code(rejected) == "CLARIFICATION_CATALOG_CHANGED"
    assert len(fixture.requests()) == calls


def test_database_marker_change_invalidates_pending(
    app: RunningApp, fixture: FixtureClient
) -> None:
    assert app.api is not None
    session_id, clarification = _ambiguous(app.api, fixture)
    app.restart(marker="slice3-marker-b")
    assert app.api is not None
    current = app.api.session(session_id)
    calls = len(fixture.requests())
    response = app.api.submit(
        session_id,
        "Первый",
        context_version=current["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert response.status == 409
    assert error_code(response) == "CLARIFICATION_MARKER_CHANGED"
    assert len(fixture.requests()) == calls


def test_two_concurrent_claims_have_exactly_one_winner(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    current = api.session(session_id)

    def claim(ordinal: int) -> tuple[int, str | None, dict[str, Any] | None]:
        response = api.submit(
            session_id,
            f"Первый {ordinal}",
            context_version=current["context_version"],
            clarification=_choice_payload(clarification),
            client_message_id=f"slice3-concurrent-{ordinal:02d}",
        )
        return (
            response.status,
            None if response.status == 202 else error_code(response),
            response.json() if response.status == 202 else None,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))
    assert sorted(status for status, _, _ in results) == [202, 409]
    assert [code for status, code, _ in results if status == 409] == [
        "CLARIFICATION_CONSUMED"
    ]
    winner = next(payload for status, _, payload in results if status == 202)
    assert winner is not None
    api.events(winner["turn_id"])


def test_cancel_consumes_pending_without_external_calls(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    current = api.session(session_id)
    calls = len(fixture.requests())
    response = api.submit(
        session_id,
        "Отмена",
        context_version=current["context_version"],
        clarification={"handle": clarification["handle"], "action": "cancel"},
    )
    assert response.status == 202
    turn = api.turn(response.json()["turn_id"])
    if turn["status"] not in {"completed", "failed"}:
        api.events(turn["turn_id"])
    assert len(fixture.requests()) == calls
    assert api.session(session_id)["context"]["pending_clarification"] is None


def test_truncated_pending_accepts_narrow_and_reruns_only_pinned_resolver(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture, count=6)
    assert clarification["choices"] == []
    fixture.configure("resolve_one", asset_count=1, clear_requests=False)
    before_llm = len(fixture.requests("deepseek"))
    before_mcp = len(fixture.requests("mcp_execute_query"))
    current = api.session(session_id)
    turn = api.ask(
        session_id,
        "точный код SYN-001",
        context_version=current["context_version"],
        clarification={
            "handle": clarification["handle"],
            "action": "narrow",
        },
    )
    assert turn["outcome"] == "success_with_rows"
    assert len(fixture.requests("deepseek")) == before_llm
    assert len(fixture.requests("mcp_execute_query")) == before_mcp + 2


def test_choice_is_forbidden_for_truncated_candidate_universe(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture, count=6)
    calls = len(fixture.requests())
    current = api.session(session_id)
    response = api.submit(
        session_id,
        "Первый видимый",
        context_version=current["context_version"],
        clarification={
            "handle": clarification["handle"],
            "action": "choose",
            "choice_id": "c1",
        },
    )
    assert response.status == 422
    assert error_code(response) in {
        "CLARIFICATION_ACTION_INVALID",
        "CLARIFICATION_CHOICE_INVALID",
    }
    assert len(fixture.requests()) == calls


def test_unknown_choice_is_rejected_without_consuming_pending(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    current = api.session(session_id)
    calls = len(fixture.requests())
    response = api.submit(
        session_id,
        "Несуществующий",
        context_version=current["context_version"],
        clarification={
            "handle": clarification["handle"],
            "action": "choose",
            "choice_id": "missing-choice",
        },
    )
    assert response.status == 422
    assert error_code(response) == "CLARIFICATION_CHOICE_INVALID"
    assert len(fixture.requests()) == calls
    assert (
        api.session(session_id)["context"]["pending_clarification"]["handle"]
        == (clarification["handle"])
    )


def test_interpretation_choice_uses_one_typed_resume_planner_call(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("interpretation_resume", asset_count=1)
    session = api.create_session()
    original_question = "Покажи лазурные активы с выбранным оттенком"
    first = api.ask(
        session["session_id"],
        original_question,
        context_version=session["context_version"],
    )
    assert first["outcome"] == "clarification_required"
    before_mcp = len(fixture.requests("mcp_execute_query"))
    current = api.session(session["session_id"])
    resumed = api.ask(
        session["session_id"],
        "Выбираю бирюзовый",
        context_version=current["context_version"],
        clarification=_choice_payload(first["clarification"], 1),
    )
    assert resumed["outcome"] == "success_with_rows"
    deepseek = fixture.requests("deepseek")
    assert len(deepseek) == 2
    assert len(fixture.requests("mcp_execute_query")) == before_mcp + 1
    resume_input = deepseek[1]["planner_input"]
    assert resume_input["question_ru"] == original_question
    frozen = resume_input["interpretation_resume"]
    assert frozen["original_question"] == original_question
    assert frozen["selected_slot_id"] == "name_filter"
    assert frozen["selected_binding"] == {
        "source": "literal",
        "value_type": "normalized_text",
        "value": "бирюз",
    }


def test_interpretation_choice_in_optional_step_is_rejected_before_mcp(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("interpretation_optional_bypass", asset_count=1)
    session = api.create_session()
    first = api.ask(
        session["session_id"],
        "Покажи лазурные активы с выбранным оттенком",
        context_version=session["context_version"],
    )
    before_mcp = len(fixture.requests("mcp_execute_query"))
    current = api.session(session["session_id"])
    resumed = api.ask(
        session["session_id"],
        "Выбираю лазурный",
        context_version=current["context_version"],
        clarification=_choice_payload(first["clarification"], 0),
    )
    assert resumed["outcome"] == "contract_error"
    assert resumed["error"]["code"] == "CLARIFICATION_RESUME_INVALID"
    assert len(fixture.requests("deepseek")) == 2
    assert len(fixture.requests("mcp_execute_query")) == before_mcp


def test_ordinary_new_message_supersedes_old_pending(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, clarification = _ambiguous(api, fixture)
    fixture.configure("display", asset_count=1, clear_requests=False)
    current = api.session(session_id)
    api.ask(
        session_id,
        "Покажи независимый список лазурных активов",
        context_version=current["context_version"],
    )
    calls = len(fixture.requests())
    response = api.submit(
        session_id,
        "Первый",
        context_version=api.session(session_id)["context_version"],
        clarification=_choice_payload(clarification),
    )
    assert response.status == 409
    assert error_code(response) == "CLARIFICATION_SUPERSEDED"
    assert len(fixture.requests()) == calls
