"""Session-aware HandleMessage use case with snapshot pinning."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from chatbot1c.application.catalog import CatalogManager
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ExecutionContext,
    ExecutionResult,
    PlanExecutor,
)
from chatbot1c.application.models import PinnedCatalog, PlannerRequest, TurnRecord
from chatbot1c.application.outcome_machine import classify_failure
from chatbot1c.application.ports import (
    ContinuationRepository,
    DatabaseStateMarkerPort,
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
from chatbot1c.contracts.semantic import build_plan_coverage_proof
from chatbot1c.contracts.serialization import wire_bytes, wire_dict, wire_json
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.plan import ExecuteResult, SkillCall

logger = logging.getLogger(__name__)


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
        continuations: ContinuationRepository,
        marker: DatabaseStateMarkerPort,
        default_list_limit: int = 20,
        request_deadline_seconds: int = 90,
    ) -> None:
        self._sessions = sessions
        self._traces = traces
        self._catalog = catalog
        self._planner = planner
        self._executor = executor
        self._harness = harness
        self._shortlist = shortlist
        self._continuations = continuations
        self._marker = marker
        self._default_list_limit = default_list_limit
        self._request_deadline_seconds = request_deadline_seconds
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

    def submit_continuation(
        self, *, session_id: UUID, continuation_handle: str
    ) -> TurnRecord:
        catalog = self._catalog.pin()
        marker = self._marker.capture(catalog)
        continuation, turn = self._continuations.claim_continuation(
            continuation_handle,
            session_id=session_id,
            active_catalog=catalog,
            database_marker=marker.digest,
        )
        self._sessions.append_event(
            turn.turn_id,
            "continuation.accepted",
            "ok",
            {
                "source_turn_id": str(continuation.origin_turn_id),
                "catalog_snapshot_id": str(catalog.snapshot_id),
            },
        )
        return turn

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
                continuation = self._continuations.continuation_for_turn(turn_id)
                if continuation is not None:
                    return await self._run_continuation(current, continuation)
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
                outcome = classify_failure(error.code, stage="planning")
                return self._fail(current, outcome, error.code, error.message_ru)
            except Exception:
                logger.exception(
                    "Unhandled turn processing failure",
                    extra={"turn_id": str(turn_id)},
                )
                return self._fail(
                    current,
                    Outcome.CONTRACT_ERROR,
                    "INTERNAL_ERROR",
                    "Внутренняя ошибка сохранена в trace без раскрытия деталей.",
                )

    async def _run(self, turn: TurnRecord) -> TurnRecord:
        catalog = self._catalog.pin()
        marker = self._marker.capture(catalog)
        deadline_at = turn.created_at + timedelta(
            seconds=self._request_deadline_seconds
        )
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
            deadline_at=deadline_at,
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
            canonicalize(_coverage_pre(plan, catalog)),
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
                database_state_marker=marker,
                deadline_at=deadline_at,
            ),
        )
        return self._complete_execution(turn, plan, catalog, execution)

    async def _run_continuation(
        self, turn: TurnRecord, continuation: object
    ) -> TurnRecord:
        from chatbot1c.application.models import PageContinuation
        from chatbot1c.domain.plan import PlannerOutput

        if not isinstance(continuation, PageContinuation):
            raise TypeError("expected PageContinuation")
        catalog = self._catalog.load_revision(continuation.catalog_revision)
        if catalog.snapshot_id != continuation.catalog_snapshot_id:
            raise ApplicationError(
                "CONTINUATION_CATALOG_CHANGED",
                "Pinned catalog continuation не найден.",
                409,
            )
        self._sessions.pin_turn(turn.turn_id, catalog)
        marker = self._marker.capture(catalog)
        if marker.digest != continuation.database_marker:
            raise ApplicationError(
                "CONTINUATION_MARKER_CHANGED",
                "Состояние базы изменилось; выполните исходный запрос заново.",
                409,
            )
        try:
            plan = PlannerOutput.model_validate_json(continuation.plan_json)
        except ValueError as error:
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Сохраненный plan continuation поврежден.",
                409,
            ) from error
        self._traces.put_artifact(
            turn.trace_id,
            "continuation/request.json",
            canonicalize(
                {
                    "continuation_handle": continuation.handle,
                    "source_turn_id": str(continuation.origin_turn_id),
                    "skill_id": continuation.skill_id,
                    "skill_version": continuation.skill_version,
                }
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "plan.json",
            canonicalize(plan.model_dump(mode="json", by_alias=True)),
        )
        context_facts = self._sessions.context_facts(turn.session_id)
        execution = await self._executor.execute_continuation(
            plan,
            ExecutionContext(
                trace_id=turn.trace_id,
                request_id=turn.request_id,
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                turn_time=datetime.now(UTC),
                default_list_limit=continuation.page_size,
                catalog=catalog,
                context_facts=context_facts,
                database_state_marker=marker,
                deadline_at=turn.created_at
                + timedelta(seconds=self._request_deadline_seconds),
            ),
            continuation,
        )
        return self._complete_execution(turn, plan, catalog, execution)

    def _complete_execution(
        self,
        turn: TurnRecord,
        plan: object,
        catalog: PinnedCatalog,
        execution: ExecutionResult,
    ) -> TurnRecord:
        from chatbot1c.domain.plan import PlannerOutput

        typed_plan = cast(PlannerOutput, plan)
        if execution.outcome is Outcome.CONTRACT_ERROR:
            transport_error = next(
                (
                    error
                    for error in execution.evidence.errors
                    if error.code
                    in {
                        "MCP_ENVELOPE_INVALID",
                        "MCP_ENVELOPE_LIMIT",
                        "MCP_RESPONSE_TOO_LARGE",
                        "MCP_TOOL_RESULT_INVALID",
                    }
                ),
                None,
            )
            if transport_error is not None:
                raise ApplicationError(
                    transport_error.code,
                    transport_error.public_message_ru,
                    502,
                )
        if execution.continuation is not None:
            draft = execution.continuation
            stored = self._continuations.create_continuation(
                session_id=turn.session_id,
                origin_turn_id=turn.turn_id,
                step_id=draft.step_id,
                skill_id=draft.skill_id,
                skill_version=draft.skill_version,
                skill_digest=draft.skill_digest,
                catalog_snapshot_id=catalog.snapshot_id,
                catalog_revision=catalog.revision,
                arguments=draft.arguments,
                plan_json=typed_plan.model_dump_json(by_alias=True),
                strategy=draft.strategy,
                page_size=draft.page_size,
                shown=draft.cumulative_shown,
                database_marker=execution.evidence.database_state_marker.digest,
                sort_tuple=draft.sort_tuple,
                cursor_values=draft.cursor_values,
            )
            pagination = execution.evidence.pagination
            if pagination is None or not pagination.has_more:
                raise ApplicationError(
                    "RESULT_PAGINATION_INVALID",
                    "Continuation draft не совпадает с evidence pagination.",
                    500,
                )
            evidence = execution.evidence.model_copy(
                update={
                    "pagination": pagination.model_copy(
                        update={"continuation_handle": stored.handle}
                    )
                }
            )
            execution = replace(execution, evidence=evidence)
        elif (
            execution.evidence.pagination is not None
            and execution.evidence.pagination.has_more
        ):
            raise ApplicationError(
                "RESULT_PAGINATION_INVALID",
                "Evidence has_more не имеет continuation draft.",
                500,
            )
        self._harness.semantics.validate(
            execution.evidence, available_skills=tuple(catalog.skills.values())
        )
        evidence_wire = wire_dict(execution.evidence)
        self._harness.validate_document(
            evidence_wire,
            available_skills=tuple(catalog.skills.values()),
            verify_integrity=False,
        )
        self._traces.put_artifact(
            turn.trace_id,
            "evidence.json",
            wire_bytes(execution.evidence),
        )
        for step in execution.evidence.steps:
            step_facts = [
                wire_dict(fact)
                for fact in execution.evidence.facts
                if fact.fact_instance_id in step.produced_fact_instance_ids
            ]
            step_errors = [
                wire_dict(error)
                for error in execution.evidence.errors
                if error.error_id in step.error_ids
            ]
            self._traces.put_artifact(
                turn.trace_id,
                f"{step_trace_prefix(step.step_id)}/evidence.json",
                canonicalize(
                    {
                        "step": wire_dict(step),
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
        answer = render_execution(typed_plan, execution)
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
            plan_json=typed_plan.model_dump_json(by_alias=True),
            evidence_json=wire_json(execution.evidence),
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
        public_message = (
            f"{message_ru} Идентификатор операции: {turn.trace_id}."
            if outcome
            in {
                Outcome.LLM_UNAVAILABLE,
                Outcome.MCP_UNAVAILABLE,
                Outcome.CONTRACT_ERROR,
            }
            else message_ru
        )
        self._sessions.append_event(
            turn.turn_id,
            "request.failed",
            "error",
            cast(dict[str, JsonValue], {"code": code}),
        )
        completed = self._sessions.complete_turn(
            turn_id=turn.turn_id,
            assistant_text=public_message,
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
            (
                {
                    "code": code,
                    "message_ru": public_message,
                    "outcome": outcome.value,
                },
            ),
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


def _coverage_pre(plan: object, catalog: PinnedCatalog) -> dict[str, object]:
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
    proof = build_plan_coverage_proof(
        typed_plan, tuple(catalog.skills.values())
    )
    return {
        "status": "validated",
        "requirements": [
            requirement.model_dump(mode="json", by_alias=True)
            for requirement in typed_plan.interpretation.required_facts
        ],
        "final_outputs": final_outputs,
        "steps": [
            {
                "step_id": item.step_id,
                "criticality": item.criticality,
                "predecessors": list(item.predecessors),
                "required_by_requirement_ids": list(
                    item.required_by_requirement_ids
                ),
            }
            for item in proof.steps
        ],
        "fact_collection_scopes": [
            {
                "step_id": item.step_id,
                "fact_id": item.fact_id,
                "collection_scope": item.collection_scope,
            }
            for item in proof.fact_collection_scopes
        ],
        "requirement_proofs": [
            {
                "requirement_id": item.requirement_id,
                "semantic_type": item.semantic_type,
                "required": item.required,
                "final_step_id": item.final_step_id,
                "final_fact_id": item.final_fact_id,
                "collection_obligation": item.collection_obligation,
                "collection_step_ids": list(item.collection_step_ids),
            }
            for item in proof.requirements
        ],
    }
