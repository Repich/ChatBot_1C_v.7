from __future__ import annotations

from .support import AppClient, FixtureClient, canonical_ref, context_slots, slot_by_key


def _selected(
    api: AppClient, fixture: FixtureClient, *, uuid_start: int = 71
) -> tuple[str, dict]:
    fixture.configure(
        "resolve_one",
        asset_count=1,
        uuid_start=uuid_start,
        presentation="Exact asset",
    )
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Выбери exact ультрамариновый актив",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    return session["session_id"], api.session(session["session_id"])


def test_exact_server_side_ref_reaches_mcp_without_reconstruction(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, session = _selected(api, fixture)
    fixture.configure("followup")
    turn = api.ask(
        session_id,
        "Покажи снимок этого exact объекта",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    sent = fixture.requests("mcp_execute_query")[-1]["arguments"]["params"]["Актив"]
    expected = {
        "_objectRef": True,
        "УникальныйИдентификатор": "00000000-0000-4000-8000-000000000071",
        "ТипОбъекта": "СправочникСсылка.СинтетическийАктив",
        "Представление": "Exact asset",
    }
    assert canonical_ref(sent) == canonical_ref(expected)


def test_same_uuid_different_physical_type_is_not_same_entity(
    api: AppClient, fixture: FixtureClient
) -> None:
    session = api.create_session()
    fixture.configure(
        "resolve_one",
        asset_count=1,
        uuid_start=72,
        physical_type="СправочникСсылка.ПоддельныйАктив",
    )
    turn = api.ask(
        session["session_id"],
        "Выбери объект с поддельным физическим типом",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "contract_error"
    assert context_slots(api.session(session["session_id"])) == []
    assert len(fixture.requests("mcp_execute_query")) == 1


def test_consumer_result_with_another_uuid_fails_exact_equality_constraint(
    api: AppClient, fixture: FixtureClient
) -> None:
    session_id, session = _selected(api, fixture, uuid_start=73)
    original = slot_by_key(session, "selection.synthetic_asset")
    fixture.configure("followup", detail_wrong_uuid=True)
    turn = api.ask(
        session_id,
        "Верни несовпадающий объект из consumer",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "contract_error"
    current = slot_by_key(api.session(session_id), "selection.synthetic_asset")
    assert current["handle"] == original["handle"]


def test_cross_session_handle_is_rejected_before_mcp(
    api: AppClient, fixture: FixtureClient
) -> None:
    _, first = _selected(api, fixture, uuid_start=74)
    foreign_handle = slot_by_key(first, "selection.synthetic_asset")["handle"]
    second = api.create_session("Second")
    fixture.configure("followup", forced_handle=foreign_handle)
    before = len(fixture.requests("mcp_execute_query"))
    turn = api.ask(
        second["session_id"],
        "Попробуй использовать объект другой сессии",
        context_version=second["context_version"],
    )
    assert turn["outcome"] == "contract_error"
    assert len(fixture.requests("mcp_execute_query")) == before
