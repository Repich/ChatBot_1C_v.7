from __future__ import annotations

from .support import AppClient, FixtureClient, context_slots


def test_evidence_11_collection_scope_and_required_flags_remain_closed(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("display", asset_count=3)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Покажи список лазурных активов",
        context_version=session["context_version"],
    )
    evidence = api.evidence(turn["trace_id"])
    assert evidence["schema_version"] == "1.1.0"
    assert evidence["context_exports"] == []
    assert "selection_proofs" not in evidence
    assert "filter_retention_proofs" not in evidence
    assert all("collection_scope" in step for step in evidence["steps"])
    assert all("criticality" not in step for step in evidence["steps"])
    requirements = evidence["coverage"]["requirements"]
    assert requirements and all(type(item["required"]) is bool for item in requirements)


def test_display_only_keyset_continuation_remains_exact_and_context_free(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("display", asset_count=25)
    session = api.create_session()
    first = api.ask(
        session["session_id"],
        "Покажи длинный список ультрамариновых артефактов",
        context_version=session["context_version"],
    )
    assert first["outcome"] == "success_with_rows"
    assert first["pagination"]["shown"] == 20
    assert first["pagination"]["has_more"] is True
    continuation = first["pagination"]["continuation"]
    assert continuation["handle"].startswith("page_")
    before_llm = len(fixture.requests("deepseek"))
    response = api.http.json(
        "POST",
        f"/api/v1/sessions/{session['session_id']}/continuations",
        {"continuation_handle": continuation["handle"]},
    )
    assert response.status == 202
    second_id = response.json()["turn_id"]
    api.events(second_id)
    second = api.turn(second_id)
    assert second["outcome"] == "success_with_rows"
    assert second["pagination"]["shown"] == 5
    assert second["pagination"]["has_more"] is False
    assert len(fixture.requests("deepseek")) == before_llm
    assert context_slots(api.session(session["session_id"])) == []


def test_continuation_evidence_keeps_keyset_scope(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("display", asset_count=21)
    session = api.create_session()
    first = api.ask(
        session["session_id"],
        "Покажи все лазурные активы по страницам",
        context_version=session["context_version"],
    )
    handle = first["pagination"]["continuation"]["handle"]
    accepted = api.http.json(
        "POST",
        f"/api/v1/sessions/{session['session_id']}/continuations",
        {"continuation_handle": handle},
    )
    assert accepted.status == 202
    api.events(accepted.json()["turn_id"])
    turn = api.turn(accepted.json()["turn_id"])
    evidence = api.evidence(turn["trace_id"])
    assert evidence["schema_version"] == "1.1.0"
    assert evidence["steps"][0]["collection_scope"] in {
        "visible_page",
        "complete_set",
    }
    assert evidence["context_exports"] == []
