from __future__ import annotations

from .support import AppClient, FixtureClient, context_slots, slot_by_key


def _select(
    api: AppClient,
    fixture: FixtureClient,
    session_id: str,
    version: int,
    *,
    uuid_start: int,
    presentation: str,
    scenario: str = "select_only",
) -> dict:
    fixture.configure(
        scenario,
        asset_count=1,
        uuid_start=uuid_start,
        presentation=presentation,
    )
    return api.ask(
        session_id,
        f"Выбери exact объект {presentation}",
        context_version=version,
    )


def test_replacement_changes_only_named_slot_and_old_handle_becomes_stale(
    api: AppClient, fixture: FixtureClient
) -> None:
    created = api.create_session()
    first = _select(
        api,
        fixture,
        created["session_id"],
        created["context_version"],
        uuid_start=11,
        presentation="Общее отображение",
    )
    assert first["outcome"] == "success_with_rows", first
    session = api.session(created["session_id"])
    old = slot_by_key(session, "selection.synthetic_asset")

    second = _select(
        api,
        fixture,
        created["session_id"],
        session["context_version"],
        uuid_start=12,
        presentation="Общее отображение",
    )
    assert second["outcome"] == "success_with_rows"
    updated = api.session(created["session_id"])
    new = slot_by_key(updated, "selection.synthetic_asset")
    assert old["handle"] != new["handle"]
    assert len(context_slots(updated)) == 1

    fixture.configure("followup", forced_handle=old["handle"])
    before = len(fixture.requests("mcp_execute_query"))
    rejected = api.ask(
        created["session_id"],
        "Используй старое поколение",
        context_version=updated["context_version"],
    )
    assert rejected["outcome"] == "contract_error"
    assert rejected["error"]["code"] == "CONTEXT_HANDLE_REPLACED"
    assert len(fixture.requests("mcp_execute_query")) == before


def test_same_uuid_new_presentation_refreshes_handle_not_identity(
    api: AppClient, fixture: FixtureClient
) -> None:
    created = api.create_session()
    _select(
        api,
        fixture,
        created["session_id"],
        created["context_version"],
        uuid_start=21,
        presentation="Старое имя",
    )
    first = api.session(created["session_id"])
    old = slot_by_key(first, "selection.synthetic_asset")
    _select(
        api,
        fixture,
        created["session_id"],
        first["context_version"],
        uuid_start=21,
        presentation="Новое имя",
    )
    second = api.session(created["session_id"])
    new = slot_by_key(second, "selection.synthetic_asset")
    assert new["handle"] != old["handle"]
    assert new["presentation"] == "Новое имя"


def test_remove_invalidates_exact_handle_without_deepseek_or_mcp(
    api: AppClient, fixture: FixtureClient
) -> None:
    created = api.create_session()
    _select(
        api,
        fixture,
        created["session_id"],
        created["context_version"],
        uuid_start=31,
        presentation="Удаляемый актив",
        scenario="resolve_one",
    )
    session = api.session(created["session_id"])
    handle = slot_by_key(session, "selection.synthetic_asset")["handle"]
    calls = len(fixture.requests())
    response = api.submit_context_action(
        created["session_id"], handle, context_version=session["context_version"]
    )
    assert response.status == 202
    accepted = response.json()
    assert "turn_id" in accepted, accepted
    api.events(accepted["turn_id"])
    assert {
        slot["slot_key"] for slot in context_slots(api.session(created["session_id"]))
    } == {"filter.synthetic_snapshot"}
    assert len(fixture.requests()) == calls


def test_wrong_semantic_context_binding_is_rejected_pre_mcp(
    api: AppClient, fixture: FixtureClient
) -> None:
    created = api.create_session()
    _select(
        api,
        fixture,
        created["session_id"],
        created["context_version"],
        uuid_start=41,
        presentation="Типизированный актив",
        scenario="resolve_one",
    )
    session = api.session(created["session_id"])
    fixture.configure("wrong_semantic")
    before = len(fixture.requests("mcp_execute_query"))
    turn = api.ask(
        created["session_id"],
        "Подмени semantic type",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "contract_error"
    assert turn["error"]["code"] == "ENTITY_REF_CONTRACT_MISMATCH"
    assert len(fixture.requests("mcp_execute_query")) == before


def test_unknown_handle_without_compatible_context_requires_clarification_pre_mcp(
    api: AppClient, fixture: FixtureClient
) -> None:
    created = api.create_session()
    fixture.configure("forged", forced_handle="ctx_AAAAAAAAAAAAAAAA")
    before = len(fixture.requests("mcp_execute_query"))
    turn = api.ask(
        created["session_id"],
        "Используй поддельный opaque handle",
        context_version=created["context_version"],
    )
    assert turn["outcome"] == "clarification_required"
    assert "clarification" not in turn
    assert "нет подходящего подтвержденного выбора" in turn["assistant_message"]["text"]
    assert len(fixture.requests("mcp_execute_query")) == before


def test_wrong_physical_type_fails_after_resolver_before_consumer(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure(
        "resolve_one",
        asset_count=1,
        physical_type="СправочникСсылка.ДругойФизическийТип",
    )
    created = api.create_session()
    turn = api.ask(
        created["session_id"],
        "Найди актив с неверным физическим типом",
        context_version=created["context_version"],
    )
    assert turn["outcome"] == "contract_error"
    assert turn["error"]["code"] == "ENTITY_REF_CONTRACT_MISMATCH"
    assert len(fixture.requests("mcp_execute_query")) == 1
    assert context_slots(api.session(created["session_id"])) == []


def test_active_context_survives_restart_and_exact_ref_reaches_mcp(
    app, fixture: FixtureClient
) -> None:
    assert app.api is not None
    created = app.api.create_session()
    _select(
        app.api,
        fixture,
        created["session_id"],
        created["context_version"],
        uuid_start=51,
        presentation="До перезапуска",
        scenario="resolve_one",
    )
    app.restart()
    assert app.api is not None
    session = app.api.session(created["session_id"])
    fixture.configure("followup")
    turn = app.api.ask(
        created["session_id"],
        "Покажи снимок этого объекта после restart",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    call = fixture.requests("mcp_execute_query")[-1]
    ref = call["arguments"]["params"]["Актив"]
    assert ref["УникальныйИдентификатор"].endswith("000000000051")
    assert ref["ТипОбъекта"] == "СправочникСсылка.СинтетическийАктив"
