from __future__ import annotations

import json

from .support import AppClient, FixtureClient, json_member
from .synthetic_package import FIXED_MOMENT, PHYSICAL_TYPE


def test_actual_deepseek_http_body_contains_only_opaque_context(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=1, snapshot_moment=FIXED_MOMENT)
    session = api.create_session()
    api.ask(
        session["session_id"],
        "Выбери лазурный актив и сохрани снимок",
        context_version=session["context_version"],
    )
    fixture.configure("followup_scalar")
    followup = api.ask(
        session["session_id"],
        "Покажи этот актив в том же снимке",
        context_version=api.session(session["session_id"])["context_version"],
    )
    request = fixture.requests("deepseek")[-1]["body"]
    serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)
    assert "_objectRef" not in serialized
    assert "УникальныйИдентификатор" not in serialized
    assert "ТипОбъекта" not in serialized
    assert PHYSICAL_TYPE not in serialized
    assert "00000000-0000-4000-8000-000000000001" not in serialized
    assert FIXED_MOMENT not in serialized

    planner_input = fixture.requests("deepseek")[-1]["planner_input"]
    facts = planner_input["context"]["confirmed_facts"]
    assert all("handle" in fact and fact["handle"].startswith("ctx_") for fact in facts)
    assert all("value" not in fact and "members" not in fact for fact in facts)

    members = api.diagnostic_members(followup["trace_id"])
    captured = json_member(members, "planner/http-request.json")
    assert captured == request
    replay = json_member(members, "planner/request.json")
    replay_text = json.dumps(replay, ensure_ascii=False)
    assert "УникальныйИдентификатор" in replay_text
    assert FIXED_MOMENT in replay_text


def test_public_sse_and_details_never_expose_raw_context_values(
    api: AppClient, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=1, snapshot_moment=FIXED_MOMENT)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Зафиксируй synthetic snapshot",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"
    details = api.details(turn["turn_id"])
    public = json.dumps(
        {
            "context": api.session(session["session_id"])["context"],
            "resolver_proofs": details.get("resolver_proofs", []),
            "context_mutations": details.get("context_mutations", []),
        },
        ensure_ascii=False,
    )
    assert "_objectRef" not in public
    assert "УникальныйИдентификатор" not in public
    assert PHYSICAL_TYPE not in public
    assert FIXED_MOMENT not in public
