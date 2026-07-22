from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from chatbot1c.application.models import PlannerRequest
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.domain.plan import PlannerOutput


class _ClarifyingPlanner:
    def __init__(self) -> None:
        self.requests: list[PlannerRequest] = []

    def outbound_http_request(self, request: PlannerRequest) -> bytes | None:
        del request
        return None

    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        self.requests.append(request)
        return PlannerOutput.model_validate(
            {
                "schema_version": "1.0.0",
                "document_type": "planner_output",
                "request_id": str(request.request_id),
                "session_context_version": request.context_version,
                "catalog_snapshot_id": str(request.catalog_snapshot_id),
                "catalog_revision": request.catalog_revision,
                "decision": "clarify",
                "interpretation": {
                    "intent_kind": "data",
                    "goal_ru": "Уточнить искомую номенклатуру.",
                    "required_facts": [
                        {
                            "requirement_id": "r1",
                            "semantic_type": "catalog.item",
                            "value_type": "entity_ref",
                            "cardinality": "one",
                            "required": True,
                        }
                    ],
                    "slots": [
                        {
                            "slot_id": "item",
                            "semantic_type": "catalog.item",
                            "value_type": "entity_ref",
                            "status": "missing",
                            "mentions": [],
                        }
                    ],
                },
                "result": {
                    "kind": "clarify",
                    "question_ru": "Какую номенклатуру требуется выбрать?",
                    "missing_requirement_ids": ["r1"],
                    "choices": [],
                },
            }
        )


class _WrongEchoPlanner(_ClarifyingPlanner):
    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        plan = await super().plan(request)
        return plan.model_copy(update={"request_id": uuid4()})


class _McpMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_query(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("MCP must not run after planner echo mismatch")

    async def get_metadata(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("MCP must not run after planner echo mismatch")


def test_planner_echo_mismatch_fails_before_mcp(tmp_path: Path) -> None:
    planner = _WrongEchoPlanner()
    one_c = _McpMustNotRun()
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        planner=planner,
        one_c=one_c,  # type: ignore[arg-type]
        auto_import=True,
    )
    session = runtime.store.create_session()
    turn, _ = runtime.chat.submit_message(
        session_id=session.session_id,
        text="Проверка чужого echo",
        client_message_id=str(uuid4()),
        expected_context_version=1,
    )
    completed = asyncio.run(runtime.chat.process_turn(turn.turn_id))
    assert completed.status == "failed"
    assert completed.error_code == "PLAN_ECHO_MISMATCH"
    assert one_c.calls == 0
    artifacts = runtime.store.artifacts(turn.trace_id)
    assert "planner/response.json" in artifacts
    rejected = json.loads(artifacts["planner/response.json"])
    assert rejected["request_id"] != str(turn.request_id)
    asyncio.run(runtime.close())


def test_two_accepted_turns_reject_stale_queued_turn_before_planner(
    tmp_path: Path,
) -> None:
    planner = _ClarifyingPlanner()
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        planner=planner,
        auto_import=True,
    )
    session = runtime.store.create_session()
    first, _ = runtime.chat.submit_message(
        session_id=session.session_id,
        text="Первый вопрос",
        client_message_id=str(uuid4()),
        expected_context_version=1,
    )
    queued, _ = runtime.chat.submit_message(
        session_id=session.session_id,
        text="Второй уже принятый вопрос",
        client_message_id=str(uuid4()),
        expected_context_version=1,
    )

    completed_first = asyncio.run(runtime.chat.process_turn(first.turn_id))
    completed_queued = asyncio.run(runtime.chat.process_turn(queued.turn_id))

    assert completed_first.status == "completed"
    assert completed_queued.status == "failed"
    assert completed_queued.error_code == "CONTEXT_VERSION_CONFLICT"
    assert len(planner.requests) == 1
    asyncio.run(runtime.close())


def test_planner_history_excludes_current_turn(tmp_path: Path) -> None:
    planner = _ClarifyingPlanner()
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        planner=planner,
        auto_import=True,
    )
    session = runtime.store.create_session()
    first, _ = runtime.chat.submit_message(
        session_id=session.session_id,
        text="Первое сообщение",
        client_message_id=str(uuid4()),
        expected_context_version=1,
    )
    asyncio.run(runtime.chat.process_turn(first.turn_id))
    current_session = runtime.store.get_session(session.session_id)
    assert current_session is not None
    second, _ = runtime.chat.submit_message(
        session_id=session.session_id,
        text="Второе сообщение",
        client_message_id=str(uuid4()),
        expected_context_version=current_session.context_version,
    )
    asyncio.run(runtime.chat.process_turn(second.turn_id))

    assert planner.requests[0].recent_user_messages == ()
    assert planner.requests[1].recent_user_messages == ("Первое сообщение",)
    assert planner.requests[1].message == "Второе сообщение"
    asyncio.run(runtime.close())
