from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from typing import Any

import pytest
from support import (
    AppClient,
    FixtureDriver,
    HttpResponse,
    ScenarioController,
    nested_bindings,
    nested_object_refs,
)


def _assert_completed(turn: dict[str, Any], outcome: str) -> None:
    assert turn["status"] == "completed", turn
    assert turn["outcome"] == outcome, turn
    assert isinstance(turn["assistant_message"]["text"], str)
    assert turn["assistant_message"]["text"].strip()
    assert isinstance(turn["assistant_message"]["citations"], list)
    assert isinstance(turn["pinned"]["catalog_revision"], int)
    assert turn["pinned"]["catalog_snapshot_id"]


def _execute_query_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        request
        for request in state["requests"]
        if request["boundary"] == "mcp"
        and request["body"].get("method") == "tools/call"
        and request["body"].get("params", {}).get("name") == "execute_query"
    ]


def _deepseek_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        request for request in state["requests"] if request["boundary"] == "deepseek"
    ]


def _turn_error_code(turn: dict[str, Any]) -> str | None:
    error = turn.get("error")
    if isinstance(error, dict):
        return error.get("code")
    errors = turn.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0].get("code")
    return None


def _zip_members(response: HttpResponse) -> dict[str, bytes]:
    assert response.status == 200
    assert "application/zip" in response.headers.get("content-type", "")
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _json_member(members: dict[str, bytes], name: str) -> Any:
    return json.loads(members[name].decode("utf-8"))


def test_q001_documentation_citation_and_session_reload(
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    acceptance_oracles: dict[str, Any],
) -> None:
    scenario_controller.set("q001")
    session = app_client.create_session()
    turn, elapsed = app_client.ask(session["session_id"], corpus_questions["Q001"])

    _assert_completed(turn, "documentation_found")
    assert elapsed <= acceptance_oracles["slo_seconds"]
    citations = json.dumps(turn["assistant_message"]["citations"], ensure_ascii=False)
    assert "ut-help://" in citations

    reloaded = app_client.get_session(session["session_id"])
    messages = reloaded["messages"]
    assert any(
        message["role"] == "user" and message["text"] == corpus_questions["Q001"]
        for message in messages
    )
    assert any(
        message["role"] == "assistant" and message["turn_id"] == turn["turn_id"]
        for message in messages
    )


def test_q011_executes_data_query_without_exposing_query_to_llm(
    app_client: AppClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    acceptance_oracles: dict[str, Any],
) -> None:
    scenario_controller.set("q011")
    session = app_client.create_session()
    turn, elapsed = app_client.ask(session["session_id"], corpus_questions["Q011"])

    _assert_completed(turn, "success_with_rows")
    assert elapsed <= acceptance_oracles["slo_seconds"]
    state = fixture_driver.state()
    mcp_calls = _execute_query_calls(state)
    deepseek_calls = _deepseek_calls(state)
    assert mcp_calls
    assert deepseek_calls

    queries = [call["body"]["params"]["arguments"]["query"] for call in mcp_calls]
    llm_payload = json.dumps(deepseek_calls, ensure_ascii=False)
    assert all(query and query not in llm_payload for query in queries)


def test_q036_q037_uses_exact_context_ref_without_second_number_lookup(
    app_client: AppClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    acceptance_oracles: dict[str, Any],
) -> None:
    scenario_controller.set("q036")
    session = app_client.create_session()
    q036, q036_elapsed = app_client.ask(
        session["session_id"], corpus_questions["Q036"]
    )
    _assert_completed(q036, "success_with_rows")
    assert q036_elapsed <= acceptance_oracles["slo_seconds"]
    before_calls = _execute_query_calls(fixture_driver.state())
    assert len(before_calls) == 1

    scenario_controller.set("q037", clear_requests=False)
    q037, q037_elapsed = app_client.ask(
        session["session_id"], corpus_questions["Q037"]
    )
    _assert_completed(q037, "success_with_rows")
    assert q037_elapsed <= acceptance_oracles["slo_seconds"]

    state = fixture_driver.state()
    expected_ref = state["object_refs"]["sales_order"]
    after_calls = _execute_query_calls(state)
    follow_up_calls = after_calls[len(before_calls) :]
    assert len(follow_up_calls) == 1
    arguments = follow_up_calls[0]["body"]["params"]["arguments"]
    assert nested_object_refs(arguments) == [expected_ref]
    assert "0000-000005" not in json.dumps(arguments, ensure_ascii=False)
    assert "number" not in arguments.get("params", {})

    members = _zip_members(app_client.diagnostic_zip(q037["trace_id"]))
    plan = _json_member(members, "plan.json")
    context_bindings = nested_bindings(plan, "context")
    assert context_bindings
    assert all("_objectRef" not in binding for binding in context_bindings)
    assert all(
        re.fullmatch(r"ctx_[A-Za-z0-9_-]{16,80}", binding["context_handle"])
        for binding in context_bindings
    )
    assert any(
        binding.get("expected_semantic_type") == "document.sales_order"
        for binding in context_bindings
    )


def test_q036_ambiguous_does_not_export_arbitrary_order(
    app_client: AppClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
) -> None:
    scenario_controller.set("q036_ambiguous")
    session = app_client.create_session()
    turn, _ = app_client.ask(session["session_id"], corpus_questions["Q036"])

    _assert_completed(turn, "clarification_required")
    assert len(_execute_query_calls(fixture_driver.state())) == 1
    members = _zip_members(app_client.diagnostic_zip(turn["trace_id"]))
    context = json.dumps(_json_member(members, "context.json"), ensure_ascii=False)
    for ref in fixture_driver.state()["object_refs"].values():
        assert ref["УникальныйИдентификатор"] not in context


def test_q037_without_context_clarifies_without_mcp(
    app_client: AppClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
) -> None:
    scenario_controller.set("q037_missing_context")
    session = app_client.create_session()
    turn, _ = app_client.ask(session["session_id"], corpus_questions["Q037"])

    _assert_completed(turn, "clarification_required")
    assert _execute_query_calls(fixture_driver.state()) == []


def test_q037_rejects_row_with_different_order_ref(
    app_client: AppClient,
    fixture_driver: FixtureDriver,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
) -> None:
    scenario_controller.set("q036")
    session = app_client.create_session()
    q036, _ = app_client.ask(session["session_id"], corpus_questions["Q036"])
    _assert_completed(q036, "success_with_rows")

    scenario_controller.set("q037_ref_mismatch")
    q037, _ = app_client.ask(session["session_id"], corpus_questions["Q037"])
    assert q037["outcome"] == "contract_error", q037
    assert q037["status"] in {"completed", "failed"}
    assert "SYNTHETIC-ORDER-OTHER" not in q037["assistant_message"]["text"]
    assert len(_execute_query_calls(fixture_driver.state())) == 1


def test_q102_success_empty_is_observably_different_from_query_error(
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    acceptance_oracles: dict[str, Any],
) -> None:
    scenario_controller.set("q102")
    empty_session = app_client.create_session()
    empty, elapsed = app_client.ask(
        empty_session["session_id"], corpus_questions["Q102"]
    )
    _assert_completed(empty, "success_empty")
    assert elapsed <= acceptance_oracles["slo_seconds"]
    assert "TEST-NOT-EXISTS-999999" in empty["assistant_message"]["text"]

    scenario_controller.set("mcp_query_error")
    error_session = app_client.create_session()
    error, _ = app_client.ask(
        error_session["session_id"], corpus_questions["Q102"]
    )
    assert error["outcome"] == "query_error"
    assert error["assistant_message"]["text"] != empty["assistant_message"]["text"]


def test_real_mcp_minimal_envelopes_are_normalized_without_rejection(
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
) -> None:
    scenario_controller.set("q011_real_mcp_minimal")
    success_session = app_client.create_session()
    success, _ = app_client.ask(
        success_session["session_id"], corpus_questions["Q011"]
    )
    _assert_completed(success, "success_with_rows")
    evidence = _json_member(
        _zip_members(app_client.diagnostic_zip(success["trace_id"])), "evidence.json"
    )
    mcp_steps = [step for step in evidence["steps"] if step["source_kind"] == "mcp_data"]
    assert len(mcp_steps) == 1
    assert mcp_steps[0]["row_count"] == 1

    scenario_controller.set("mcp_real_error_only")
    error_session = app_client.create_session()
    error, _ = app_client.ask(error_session["session_id"], corpus_questions["Q011"])
    assert error["outcome"] == "query_error", error
    assert error["outcome"] != "success_empty"


@pytest.mark.parametrize(
    ("fixture_scenario", "expected_code"),
    [
        ("deepseek_malformed", "DEEPSEEK_STRUCTURED_OUTPUT_INVALID"),
        ("deepseek_schema_invalid", "DEEPSEEK_STRUCTURED_OUTPUT_INVALID"),
        ("mcp_malformed", "MCP_ENVELOPE_INVALID"),
    ],
)
def test_malformed_dependency_payloads_fail_closed(
    fixture_scenario: str,
    expected_code: str,
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
) -> None:
    scenario_controller.set(fixture_scenario)
    session = app_client.create_session()
    turn, _ = app_client.ask(session["session_id"], corpus_questions["Q011"])

    assert turn["status"] == "failed", turn
    assert turn["outcome"] == "contract_error", turn
    assert _turn_error_code(turn) == expected_code


def test_sse_has_ordered_progress_before_terminal_event(
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    public_contract: dict[str, Any],
) -> None:
    scenario_controller.set("q011", delay_ms=500)
    session = app_client.create_session()
    accepted = app_client.send_message(session["session_id"], corpus_questions["Q011"])
    events = app_client.sse_events(accepted["turn_id"])
    turn = app_client.wait_turn(accepted["turn_id"])

    assert events
    required = set(public_contract["sse"]["event_fields"])
    assert all(required <= event.keys() for event in events)
    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(set(sequences))
    assert events[-1]["stage"] in public_contract["sse"]["terminal_stages"]
    assert any(
        event["stage"] not in public_contract["sse"]["terminal_stages"]
        for event in events[:-1]
    )
    assert turn["status"] == "completed"


def test_diagnostic_zip_is_complete_checksummed_and_secret_free(
    app_client: AppClient,
    scenario_controller: ScenarioController,
    corpus_questions: dict[str, str],
    public_contract: dict[str, Any],
) -> None:
    canary = os.getenv("SLICE1_SECRET_CANARY")
    if not canary:
        pytest.skip(
            "blocked_implementation: SLICE1_SECRET_CANARY must be injected at app start"
        )

    scenario_controller.set("q011")
    session = app_client.create_session()
    turn, _ = app_client.ask(session["session_id"], corpus_questions["Q011"])
    members = _zip_members(app_client.diagnostic_zip(turn["trace_id"]))

    assert set(public_contract["diagnostic_zip"]["required_files"]) <= members.keys()
    combined = b"\n".join(members.values())
    assert canary.encode() not in combined
    assert b"Authorization: Bearer" not in combined
    assert b"/Users/" not in combined
    assert b"C:\\Users\\" not in combined

    checksum_lines = members["checksums.sha256"].decode("ascii").splitlines()
    assert checksum_lines
    for line in checksum_lines:
        expected, name = line.split(maxsplit=1)
        name = name.lstrip("* ")
        assert name in members
        assert hashlib.sha256(members[name]).hexdigest() == expected
