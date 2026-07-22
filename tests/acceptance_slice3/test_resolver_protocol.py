from __future__ import annotations

from typing import Any

from .support import AppClient, FixtureClient, context_slots, slot_by_key


def _session(api: AppClient) -> tuple[str, int]:
    created = api.create_session()
    return created["session_id"], created["context_version"]


def test_exact_zero_is_not_found_without_context_or_pending(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=0)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Найди точный ультрамариновый артефакт и покажи его снимок",
        context_version=version,
    )
    assert turn["outcome"] == "success_empty"
    assert turn["reason"] == "not_found"
    session = api.session(session_id)
    assert context_slots(session) == []
    assert session["context"]["pending_clarification"] is None


def test_exact_one_selects_and_continues_downstream(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=1)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Найди один лазурный актив и покажи его контрольный снимок",
        context_version=version,
    )
    assert turn["outcome"] == "success_with_rows", turn
    session = api.session(session_id)
    slot = slot_by_key(session, "selection.synthetic_asset")
    assert slot["semantic_type"] == "synthetic.asset"
    assert slot["policy_mode"] == "selected_only"
    assert slot["cardinality"] == "one"
    assert slot["member_count"] == 1
    assert len(fixture.requests("mcp_execute_query")) == 2


def test_two_distinct_candidates_create_typed_choice_and_block_descendant(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=2, same_presentation=True)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Выбери лазурный актив и покажи значение",
        context_version=version,
    )
    assert turn["outcome"] == "clarification_required"
    clarification = turn["clarification"]
    assert clarification["handle"].startswith("clar_")
    assert clarification["has_more_candidates"] is False
    assert len(clarification["choices"]) == 2
    assert len({choice["choice_id"] for choice in clarification["choices"]}) == 2
    assert "00000000-" not in str(clarification)
    assert "СправочникСсылка" not in str(clarification)
    assert len(fixture.requests("mcp_execute_query")) == 1
    assert context_slots(api.session(session_id)) == []


def test_six_candidates_offer_only_narrow_not_truncated_choice(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=6)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Выбери один ультрамариновый артефакт",
        context_version=version,
    )
    assert turn["outcome"] == "clarification_required"
    clarification = turn["clarification"]
    assert clarification["has_more_candidates"] is True
    assert clarification["choices"] == []
    assert context_slots(api.session(session_id)) == []


def test_has_more_page_never_exposes_visible_rows_as_selectable_choices(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=3, has_more=True, truncated=True)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Выбери один лазурный объект из неполного результата",
        context_version=version,
    )
    clarification = turn["clarification"]
    assert clarification["has_more_candidates"] is True
    assert clarification["choices"] == []
    assert context_slots(api.session(session_id)) == []


def test_incomplete_page_with_one_deduplicated_identity_is_not_selected(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure(
        "resolve_one",
        asset_count=2,
        duplicate_identity=True,
        has_more=True,
        truncated=True,
    )
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Выбери один объект из неполной страницы с повторяющимися строками",
        context_version=version,
    )
    assert turn["outcome"] == "clarification_required"
    assert turn["clarification"]["has_more_candidates"] is True
    assert turn["clarification"]["choices"] == []
    assert "pagination" not in turn
    assert context_slots(api.session(session_id)) == []


def test_duplicate_rows_with_same_exact_identity_are_one_candidate(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=2, duplicate_identity=True)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Получи снимок лазурного объекта с повторяющимися строками",
        context_version=version,
    )
    assert turn["outcome"] == "success_with_rows"
    assert (
        slot_by_key(api.session(session_id), "selection.synthetic_asset")[
            "member_count"
        ]
        == 1
    )


def test_complete_set_selects_all_members_as_one_slot(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_set", asset_count=3)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Посчитай полный набор лазурных артефактов",
        context_version=version,
    )
    assert turn["outcome"] == "success_with_rows"
    slot = slot_by_key(api.session(session_id), "selection.synthetic_asset")
    assert slot["cardinality"] == "many"
    assert slot["member_count"] == 3
    assert len(fixture.requests("mcp_execute_query")) == 2
    assets = fixture.requests("mcp_execute_query")[-1]["arguments"]["params"]["Активы"]
    assert {item["УникальныйИдентификатор"] for item in assets} == {
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
    }


def test_incomplete_or_oversized_set_is_not_selected(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_set", asset_count=101, has_more=True, truncated=True)
    session_id, version = _session(api)
    turn = api.ask(
        session_id,
        "Посчитай слишком большой набор ультрамариновых артефактов",
        context_version=version,
    )
    assert turn["outcome"] in {"partial", "clarification_required"}
    assert context_slots(api.session(session_id)) == []
    assert len(fixture.requests("mcp_execute_query")) == 1


def test_resolver_proof_is_publicly_observable_without_raw_refs(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=1)
    session_id, version = _session(api)
    turn = api.ask(session_id, "Найди лазурный актив", context_version=version)
    details: dict[str, Any] = api.details(turn["turn_id"])
    proofs = details["resolver_proofs"]
    assert proofs[0]["mode"] == "select_one"
    assert proofs[0]["member_count"] == 1
    serialized = str(proofs)
    assert "УникальныйИдентификатор" not in serialized
    assert "СправочникСсылка" not in serialized
