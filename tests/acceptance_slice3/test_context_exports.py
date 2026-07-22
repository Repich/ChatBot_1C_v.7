from __future__ import annotations

from .support import AppClient, FixtureClient, context_slots


def _run(
    api: AppClient,
    fixture: FixtureClient,
    *,
    scenario: str,
    asset_count: int,
    question: str,
) -> tuple[dict, dict]:
    fixture.configure(scenario, asset_count=asset_count)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        question,
        context_version=session["context_version"],
    )
    return turn, api.session(session["session_id"])


def test_g01_display_list_rows_never_enter_context(
    api: AppClient, fixture: FixtureClient
) -> None:
    turn, session = _run(
        api,
        fixture,
        scenario="display",
        asset_count=3,
        question="Покажи список ультрамариновых артефактов",
    )
    assert turn["outcome"] == "success_with_rows"
    assert context_slots(session) == []
    evidence = api.evidence(turn["trace_id"])
    assert evidence["context_exports"] == []


def test_g01_ambiguous_candidates_never_enter_context(
    api: AppClient, fixture: FixtureClient
) -> None:
    turn, session = _run(
        api,
        fixture,
        scenario="resolve_one",
        asset_count=3,
        question="Выбери один лазурный артефакт и покажи снимок",
    )
    assert turn["outcome"] == "clarification_required"
    assert context_slots(session) == []
    evidence = api.evidence(turn["trace_id"])
    assert evidence["context_exports"] == []


def test_g01_downstream_entity_dimensions_do_not_create_extra_slots(
    api: AppClient, fixture: FixtureClient
) -> None:
    turn, session = _run(
        api,
        fixture,
        scenario="resolve_one",
        asset_count=1,
        question="Выбери лазурный актив и покажи снимок",
    )
    assert turn["outcome"] == "success_with_rows"
    slots = context_slots(session)
    assert {slot["slot_key"] for slot in slots} == {
        "selection.synthetic_asset",
        "filter.synthetic_snapshot",
    }
    evidence = api.evidence(turn["trace_id"])
    exports = evidence["context_exports"]
    assert {item["semantic_type"] for item in exports} == {
        "synthetic.asset",
        "synthetic.snapshot",
    }


def test_selected_set_exports_one_handle_with_exact_member_count(
    api: AppClient, fixture: FixtureClient
) -> None:
    turn, session = _run(
        api,
        fixture,
        scenario="resolve_set",
        asset_count=3,
        question="Посчитай все лазурные артефакты",
    )
    evidence = api.evidence(turn["trace_id"])
    entity_exports = [
        item
        for item in evidence["context_exports"]
        if item["semantic_type"] == "synthetic.asset"
    ]
    assert len(entity_exports) == 3
    assert len({item["context_handle"] for item in entity_exports}) == 1
    slots = context_slots(session)
    assert (
        next(slot for slot in slots if slot["semantic_type"] == "synthetic.asset")[
            "member_count"
        ]
        == 3
    )


def test_trace_contains_selection_retention_and_mutation_proofs(
    api: AppClient, fixture: FixtureClient
) -> None:
    turn, _ = _run(
        api,
        fixture,
        scenario="resolve_one",
        asset_count=1,
        question="Докажи выбор и retention лазурного актива",
    )
    members = api.diagnostic_members(turn["trace_id"])
    required = {
        "resolver-proofs.json",
        "filter-retention-proofs.json",
        "context-mutations.json",
        "planner/http-request.json",
        "evidence.json",
    }
    assert required <= set(members)
    details = api.details(turn["turn_id"])
    assert details["resolver_proofs"]
    assert details["context_mutations"]
    public = str(
        {
            "resolver_proofs": details["resolver_proofs"],
            "context_mutations": details["context_mutations"],
        }
    )
    assert "УникальныйИдентификатор" not in public
    assert "СправочникСсылка" not in public
