"""Session-aware HandleMessage use case with snapshot pinning."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from chatbot1c.application.catalog import CatalogManager
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import ExecutionContext, PlanExecutor
from chatbot1c.application.models import PinnedCatalog, PlannerRequest, TurnRecord
from chatbot1c.application.ports import (
    PlannerPort,
    SessionRepository,
    SkillShortlistPort,
    TraceRepository,
)
from chatbot1c.application.rendering import render_decision, render_execution
from chatbot1c.application.trace_paths import step_trace_prefix
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.plan import ExecuteResult, SkillCall


class ChatService:
    def __init__(
        self,
        *,
        sessions: SessionRepository,
        traces: TraceRepository,
        catalog: CatalogManager,
        planner: PlannerPort,
        executor: PlanExecutor,
        harness: ContractHarness,
        shortlist: SkillShortlistPort,
        default_list_limit: int = 20,
    ) -> None:
        self._sessions = sessions
        self._traces = traces
        self._catalog = catalog
        self._planner = planner
        self._executor = executor
        self._harness = harness
        self._shortlist = shortlist
        self._default_list_limit = default_list_limit
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    def submit_message(
        self,
        *,
        session_id: UUID,
        text: str,
        client_message_id: str,
        expected_context_version: int,
    ) -> tuple[TurnRecord, bool]:
        if not text.strip():
            raise ApplicationError("MESSAGE_EMPTY", "Сообщение не должно быть пустым.", 422)
        if len(text.encode("utf-8")) > 32 * 1024:
            raise ApplicationError(
                "MESSAGE_TOO_LARGE", "Сообщение превышает 32 KiB.", 413
            )
        turn, created = self._sessions.create_turn(
            session_id=session_id,
            text=text.strip(),
            client_message_id=client_message_id,
            expected_context_version=expected_context_version,
        )
        if created:
            self._sessions.append_event(
                turn.turn_id,
                "request.accepted",
                "ok",
                {"request_id": str(turn.request_id)},
            )
        return turn, created

    async def process_turn(self, turn_id: UUID) -> TurnRecord:
        turn = self._sessions.get_turn(turn_id)
        if turn is None:
            raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)
        if turn.status not in {"accepted", "running"}:
            return turn
        async with self._locks[turn.session_id]:
            current = self._sessions.get_turn(turn_id)
            if current is None:
                raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)
            if current.status not in {"accepted", "running"}:
                return current
            session = self._sessions.get_session(current.session_id)
            if session is None:
                raise ApplicationError("SESSION_NOT_FOUND", "Диалог не найден.", 404)
            if session.context_version != current.context_version:
                return self._fail(
                    current,
                    Outcome.CONTRACT_ERROR,
                    "CONTEXT_VERSION_CONFLICT",
                    "Контекст диалога изменился. Обновите диалог и повторите вопрос.",
                )
            try:
                return await self._run(current)
            except ContractValidationError as error:
                issue = error.issues[0]
                return self._fail(
                    current,
                    Outcome.CONTRACT_ERROR,
                    issue.code,
                    f"План или evidence отклонен: {issue.message_ru}",
                )
            except ApplicationError as error:
                outcome = {
                    "MCP_UNAVAILABLE": Outcome.MCP_UNAVAILABLE,
                    "LLM_UNAVAILABLE": Outcome.LLM_UNAVAILABLE,
                }.get(error.code, Outcome.CONTRACT_ERROR)
                return self._fail(current, outcome, error.code, error.message_ru)
            except Exception:
                return self._fail(
                    current,
                    Outcome.CONTRACT_ERROR,
                    "INTERNAL_ERROR",
                    "Внутренняя ошибка сохранена в trace без раскрытия деталей.",
                )

    async def _run(self, turn: TurnRecord) -> TurnRecord:
        catalog = self._catalog.pin()
        self._sessions.pin_turn(turn.turn_id, catalog)
        self._sessions.append_event(
            turn.turn_id,
            "snapshot.pinned",
            "ok",
            {
                "catalog_snapshot_id": str(catalog.snapshot_id),
                "catalog_revision": catalog.revision,
            },
        )
        self._traces.put_artifact(
            turn.trace_id,
            "catalog-snapshot.json",
            canonicalize(
                {
                    "catalog_snapshot_id": str(catalog.snapshot_id),
                    "catalog_revision": catalog.revision,
                    "catalog_digest": catalog.digest,
                    "skills": [
                        {
                            "skill_id": skill.skill_id,
                            "version": skill.version,
                            "digest": skill.integrity.digest,
                        }
                        for skill in sorted(
                            catalog.skills.values(), key=lambda item: item.skill_id
                        )
                    ],
                }
            ),
        )
        context_facts = self._sessions.context_facts(turn.session_id)
        self._sessions.append_event(
            turn.turn_id,
            "context.loaded",
            "ok",
            {"fact_count": len(context_facts)},
        )
        selected_skills = self._shortlist.select(
            question=turn.user_text,
            context=context_facts,
            catalog=catalog,
            limit=16,
        )
        planner_request = PlannerRequest(
            request_id=turn.request_id,
            session_id=turn.session_id,
            message=turn.user_text,
            turn_time=datetime.now(UTC),
            context_version=turn.context_version,
            catalog_snapshot_id=catalog.snapshot_id,
            catalog_revision=catalog.revision,
            confirmed_facts=context_facts,
            recent_user_messages=self._sessions.recent_user_messages(
                turn.session_id, 8, exclude_turn_id=turn.turn_id
            ),
            skill_cards=PinnedCatalog.create(
                catalog.snapshot_id,
                catalog.revision,
                {skill.skill_id: skill for skill in selected_skills},
            ).cards(limit=16),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "request.json",
            canonicalize(
                {
                    "request_id": str(turn.request_id),
                    "session_id": str(turn.session_id),
                    "text": turn.user_text,
                }
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "context.json",
            canonicalize(
                [fact.model_dump(mode="json", by_alias=True) for fact in context_facts]
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "planner/request.json",
            canonicalize(planner_request.model_dump(mode="json", by_alias=True)),
        )
        self._sessions.append_event(turn.turn_id, "planner.requested", "running")
        plan = await self._planner.plan(planner_request)
        serialized_plan = canonicalize(plan.model_dump(mode="json", by_alias=True))
        self._traces.put_artifact(
            turn.trace_id,
            "planner/response.json",
            serialized_plan,
        )
        _validate_plan_echo(plan, planner_request)
        _validate_shortlist(plan, planner_request)
        self._traces.put_artifact(
            turn.trace_id,
            "plan.json",
            serialized_plan,
        )
        self._sessions.append_event(turn.turn_id, "planner.completed", "ok")
        self._harness.semantics.validate(
            plan, available_skills=tuple(catalog.skills.values())
        )
        self._sessions.append_event(turn.turn_id, "plan.schema_validated", "ok")
        self._sessions.append_event(turn.turn_id, "plan.coverage_validated", "ok")
        self._traces.put_artifact(
            turn.trace_id,
            "coverage-pre.json",
            canonicalize(_coverage_pre(plan)),
        )
        if not isinstance(plan.result, ExecuteResult):
            text_value, outcome = render_decision(plan)
            self._put_answer_trace(turn, outcome, text_value, fact_count=0)
            completed = self._sessions.complete_turn(
                turn_id=turn.turn_id,
                assistant_text=text_value,
                status="completed",
                outcome=outcome.value,
                plan_json=plan.model_dump_json(by_alias=True),
                evidence_json=None,
                context_exports=(),
            )
            self._sessions.append_event(turn.turn_id, "request.completed", "ok")
            self._put_terminal_trace(turn, ())
            return completed

        execution = await self._executor.execute(
            plan,
            ExecutionContext(
                trace_id=turn.trace_id,
                request_id=turn.request_id,
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                turn_time=planner_request.turn_time,
                default_list_limit=self._default_list_limit,
                catalog=catalog,
                context_facts=context_facts,
            ),
        )
        self._harness.semantics.validate(
            execution.evidence, available_skills=tuple(catalog.skills.values())
        )
        self._traces.put_artifact(
            turn.trace_id,
            "evidence.json",
            canonicalize(execution.evidence.model_dump(mode="json", by_alias=True)),
        )
        for step in execution.evidence.steps:
            step_facts = [
                fact.model_dump(mode="json", by_alias=True)
                for fact in execution.evidence.facts
                if fact.fact_instance_id in step.produced_fact_instance_ids
            ]
            step_errors = [
                error.model_dump(mode="json", by_alias=True)
                for error in execution.evidence.errors
                if error.error_id in step.error_ids
            ]
            self._traces.put_artifact(
                turn.trace_id,
                f"{step_trace_prefix(step.step_id)}/evidence.json",
                canonicalize(
                    {
                        "step": step.model_dump(mode="json", by_alias=True),
                        "facts": step_facts,
                        "errors": step_errors,
                    }
                ),
            )
        self._sessions.append_event(
            turn.turn_id,
            "evidence.validated",
            "ok",
            {"outcome": execution.outcome.value},
        )
        answer = render_execution(plan, execution)
        self._put_answer_trace(
            turn,
            execution.outcome,
            answer,
            fact_count=len(execution.evidence.facts),
        )
        completed = self._sessions.complete_turn(
            turn_id=turn.turn_id,
            assistant_text=answer,
            status="completed",
            outcome=execution.outcome.value,
            plan_json=plan.model_dump_json(by_alias=True),
            evidence_json=execution.evidence.model_dump_json(by_alias=True),
            context_exports=execution.context_facts,
            error_code=(
                execution.evidence.errors[0].code
                if execution.evidence.errors
                else None
            ),
        )
        self._sessions.append_event(turn.turn_id, "context.committed", "ok")
        self._sessions.append_event(turn.turn_id, "request.completed", "ok")
        self._put_terminal_trace(
            turn,
            tuple(
                error.model_dump(mode="json", by_alias=True)
                for error in execution.evidence.errors
            ),
        )
        return completed

    def _fail(
        self, turn: TurnRecord, outcome: Outcome, code: str, message_ru: str
    ) -> TurnRecord:
        self._sessions.append_event(
            turn.turn_id,
            "request.failed",
            "error",
            cast(dict[str, JsonValue], {"code": code}),
        )
        completed = self._sessions.complete_turn(
            turn_id=turn.turn_id,
            assistant_text=message_ru,
            status="failed",
            outcome=outcome.value,
            plan_json=None,
            evidence_json=None,
            context_exports=(),
            error_code=code,
        )
        self._sessions.append_event(turn.turn_id, "request.completed", "error")
        self._put_terminal_trace(
            turn,
            ({"code": code, "message_ru": message_ru, "outcome": outcome.value},),
        )
        return completed

    def _put_answer_trace(
        self,
        turn: TurnRecord,
        outcome: Outcome,
        answer: str,
        *,
        fact_count: int,
    ) -> None:
        self._traces.put_artifact(
            turn.trace_id,
            "answer/request.json",
            canonicalize(
                {
                    "renderer": "deterministic_generic_ru",
                    "outcome": outcome.value,
                    "fact_count": fact_count,
                }
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "answer/response.json",
            canonicalize({"text": answer, "outcome": outcome.value}),
        )

    def _put_terminal_trace(
        self, turn: TurnRecord, errors: tuple[object, ...]
    ) -> None:
        events = self._sessions.events(turn.turn_id)
        event_lines = [
            canonicalize(
                {
                    "turn_id": str(event.turn_id),
                    "sequence": event.sequence,
                    "event_name": event.event_name,
                    "timestamp": event.timestamp.isoformat(),
                    "status": event.status,
                    "payload": dict(event.payload),
                }
            )
            for event in events
        ]
        self._traces.put_artifact(
            turn.trace_id,
            "events.jsonl",
            b"\n".join(event_lines) + (b"\n" if event_lines else b""),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "errors.json",
            canonicalize({"errors": list(errors)}),
        )


def _validate_plan_echo(plan: object, request: PlannerRequest) -> None:
    from chatbot1c.domain.plan import PlannerOutput

    typed_plan = cast(PlannerOutput, plan)
    mismatches: list[str] = []
    if typed_plan.request_id != request.request_id:
        mismatches.append("request_id")
    if typed_plan.session_context_version != request.context_version:
        mismatches.append("session_context_version")
    if typed_plan.catalog_snapshot_id != request.catalog_snapshot_id:
        mismatches.append("catalog_snapshot_id")
    if typed_plan.catalog_revision != request.catalog_revision:
        mismatches.append("catalog_revision")
    if mismatches:
        raise ApplicationError(
            "PLAN_ECHO_MISMATCH",
            "Planner вернул чужой request/context/catalog echo: "
            + ", ".join(mismatches),
            422,
        )


def _validate_shortlist(plan: object, request: PlannerRequest) -> None:
    from chatbot1c.domain.plan import PlannerOutput

    typed_plan = cast(PlannerOutput, plan)
    if not isinstance(typed_plan.result, ExecuteResult):
        return
    allowed = {(card.skill_id, card.version) for card in request.skill_cards}
    for index, step in enumerate(typed_plan.result.steps):
        if isinstance(step, SkillCall) and (step.skill_id, step.skill_version) not in allowed:
            raise ApplicationError(
                "PLAN_SKILL_NOT_SHORTLISTED",
                (
                    f"Planner выбрал {step.skill_id}@{step.skill_version}, "
                    "который не был передан в shortlist."
                ),
                422,
            )


def _coverage_pre(plan: object) -> dict[str, object]:
    from chatbot1c.domain.plan import PlannerOutput

    typed_plan = cast(PlannerOutput, plan)
    final_outputs = (
        [
            reference.model_dump(mode="json", by_alias=True)
            for reference in typed_plan.result.final_outputs
        ]
        if isinstance(typed_plan.result, ExecuteResult)
        else []
    )
    return {
        "status": "validated",
        "requirements": [
            requirement.model_dump(mode="json", by_alias=True)
            for requirement in typed_plan.interpretation.required_facts
        ],
        "final_outputs": final_outputs,
    }
