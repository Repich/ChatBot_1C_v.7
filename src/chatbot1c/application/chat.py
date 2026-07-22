"""Session-aware HandleMessage use case with snapshot pinning."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Mapping, cast
from uuid import UUID

from pydantic import JsonValue

from chatbot1c.application.catalog import CatalogManager
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ExecutionContext,
    ExecutionResult,
    PlanExecutor,
)
from chatbot1c.application.models import (
    ClarificationResponse,
    ContextFact,
    InterpretationResumeContext,
    PendingChoice,
    PendingClarification,
    PendingClarificationDraft,
    PinnedCatalog,
    PlannerRequest,
    TurnRecord,
)
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
from chatbot1c.domain.plan import (
    ClarifyResult,
    ContextBinding,
    ExecuteResult,
    LiteralBinding,
    PlannerOutput,
    SkillArgument,
    SkillCall,
)
from chatbot1c.domain.skill import Parameter, ParameterValueType

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
            raise ApplicationError(
                "MESSAGE_EMPTY", "Сообщение не должно быть пустым.", 422
            )
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

    def submit_clarification(
        self,
        *,
        session_id: UUID,
        text: str,
        client_message_id: str,
        expected_context_version: int,
        response: ClarificationResponse,
    ) -> TurnRecord:
        catalog = self._catalog.pin()
        marker = self._marker.capture(catalog)
        pending, turn = self._sessions.claim_clarification(
            session_id=session_id,
            text=text,
            client_message_id=client_message_id,
            expected_context_version=expected_context_version,
            response=response,
            active_catalog=catalog,
            database_marker=marker.digest,
        )
        self._sessions.append_event(
            turn.turn_id,
            "clarification.accepted",
            "ok",
            {
                "source_turn_id": str(pending.origin_turn_id),
                "kind": pending.kind,
                "action": response.action,
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
                pending = self._sessions.pending_for_claim_turn(turn_id)
                if pending is not None:
                    return await self._run_clarification(current, pending)
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
        outbound_request = self._planner.outbound_http_request(planner_request)
        if outbound_request is not None:
            self._traces.put_artifact(
                turn.trace_id,
                "planner/http-request.json",
                outbound_request,
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
            pending = _pending_from_planner_decision(plan, turn, catalog, marker.digest)
            self._put_answer_trace(turn, outcome, text_value, fact_count=0)
            completed = self._sessions.complete_turn(
                turn_id=turn.turn_id,
                assistant_text=text_value,
                status="completed",
                outcome=outcome.value,
                plan_json=plan.model_dump_json(by_alias=True),
                evidence_json=None,
                context_exports=(),
                pending_clarification=pending,
            )
            self._sessions.append_event(turn.turn_id, "request.completed", "ok")
            self._put_terminal_trace(turn, ())
            return completed

        plan, context_decision = _prepare_context_bindings(
            plan,
            catalog,
            context_facts,
            context_handle_states=self._sessions.context_handle_states(
                turn.session_id, _context_binding_handles(plan)
            ),
        )
        if context_decision is not None:
            return self._complete_context_binding_clarification(
                turn,
                plan,
                catalog,
                marker.digest,
                context_decision,
            )

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
                context_handle_states=self._sessions.context_handle_states(
                    turn.session_id, _context_binding_handles(plan)
                ),
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
                context_handle_states=self._sessions.context_handle_states(
                    turn.session_id
                ),
                deadline_at=turn.created_at
                + timedelta(seconds=self._request_deadline_seconds),
            ),
            continuation,
        )
        return self._complete_execution(turn, plan, catalog, execution)

    async def _run_clarification(
        self, turn: TurnRecord, pending: PendingClarification
    ) -> TurnRecord:
        catalog = self._catalog.load_revision(pending.catalog_revision)
        if catalog.snapshot_id != pending.catalog_snapshot_id:
            raise ApplicationError(
                "CLARIFICATION_CATALOG_CHANGED",
                "Pinned catalog уточнения недоступен.",
                409,
            )
        marker = self._marker.capture(catalog)
        if marker.digest != pending.database_marker:
            raise ApplicationError(
                "CLARIFICATION_MARKER_CHANGED",
                "Состояние базы изменилось; повторите исходный вопрос.",
                409,
            )
        self._sessions.pin_turn(turn.turn_id, catalog)
        if pending.claimed_action == "cancel":
            completed = self._sessions.complete_turn(
                turn_id=turn.turn_id,
                assistant_text="Уточнение отменено.",
                status="completed",
                outcome=Outcome.REFUSED.value,
                plan_json=pending.plan_json,
                evidence_json=None,
                context_exports=(),
            )
            self._sessions.append_event(turn.turn_id, "request.completed", "ok")
            return completed

        try:
            saved_plan = PlannerOutput.model_validate_json(pending.plan_json)
        except ValueError as error:
            raise ApplicationError(
                "CLARIFICATION_RESUME_INVALID",
                "Сохраненный checkpoint уточнения поврежден.",
                409,
            ) from error
        deadline_at = turn.created_at + timedelta(
            seconds=self._request_deadline_seconds
        )
        context_facts = self._sessions.context_facts(turn.session_id)
        execution_context = ExecutionContext(
            trace_id=turn.trace_id,
            request_id=turn.request_id,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            turn_time=datetime.now(UTC),
            default_list_limit=self._default_list_limit,
            catalog=catalog,
            context_facts=context_facts,
            database_state_marker=marker,
            context_handle_states=self._sessions.context_handle_states(
                turn.session_id, _context_binding_handles(saved_plan)
            ),
            deadline_at=deadline_at,
        )
        if pending.kind == "resolver_choice":
            if pending.resolver_step_id is None:
                raise ApplicationError(
                    "CLARIFICATION_RESUME_INVALID",
                    "Resolver checkpoint не содержит step_id.",
                    409,
                )
            if pending.claimed_action == "narrow":
                narrowed = _narrow_resolver_plan(
                    saved_plan,
                    pending.resolver_step_id,
                    turn.user_text,
                    catalog,
                )
                execution = await self._executor.execute(narrowed, execution_context)
                return self._complete_execution(turn, narrowed, catalog, execution)
            choice = _pending_choice(pending)
            execution = await self._executor.execute(
                saved_plan,
                execution_context,
                resolver_resume=(pending.resolver_step_id, choice.facts),
            )
            self._sessions.append_event(
                turn.turn_id, "clarification.resumed", "ok", {"planner_calls": 0}
            )
            return self._complete_execution(turn, saved_plan, catalog, execution)

        if pending.kind == "context_binding":
            choice = _pending_choice(pending)
            resumed_plan, pinned_target = _apply_context_binding_choice(
                saved_plan, choice
            )
            self._harness.semantics.validate(
                resumed_plan, available_skills=tuple(catalog.skills.values())
            )
            resumed_plan, context_decision = _prepare_context_bindings(
                resumed_plan,
                catalog,
                context_facts,
                pinned={pinned_target},
                context_handle_states=self._sessions.context_handle_states(
                    turn.session_id, _context_binding_handles(resumed_plan)
                ),
            )
            if context_decision is not None:
                return self._complete_context_binding_clarification(
                    turn,
                    resumed_plan,
                    catalog,
                    marker.digest,
                    context_decision,
                )
            self._sessions.append_event(
                turn.turn_id, "clarification.resumed", "ok", {"planner_calls": 0}
            )
            execution = await self._executor.execute(resumed_plan, execution_context)
            return self._complete_execution(turn, resumed_plan, catalog, execution)

        choice = _pending_choice(pending)
        resume_context = _interpretation_resume_context(saved_plan, pending, choice)
        selected_skills = self._shortlist.select(
            question=pending.original_question,
            context=context_facts,
            catalog=catalog,
            limit=16,
        )
        planner_request = PlannerRequest(
            request_id=turn.request_id,
            session_id=turn.session_id,
            message=pending.original_question,
            turn_time=execution_context.turn_time,
            context_version=turn.context_version,
            catalog_snapshot_id=catalog.snapshot_id,
            catalog_revision=catalog.revision,
            confirmed_facts=context_facts,
            recent_user_messages=(),
            skill_cards=PinnedCatalog.create(
                catalog.snapshot_id,
                catalog.revision,
                {skill.skill_id: skill for skill in selected_skills},
            ).cards(limit=16),
            interpretation_resume=resume_context,
            deadline_at=deadline_at,
        )
        resumed_plan = await self._planner.plan(planner_request)
        _validate_plan_echo(resumed_plan, planner_request)
        _validate_shortlist(resumed_plan, planner_request)
        self._harness.semantics.validate(
            resumed_plan, available_skills=tuple(catalog.skills.values())
        )
        if not isinstance(resumed_plan.result, ExecuteResult):
            raise ApplicationError(
                "CLARIFICATION_RESUME_INVALID",
                "Подтвержденная интерпретация не привела к executable plan.",
                422,
            )
        pinned_targets = _validate_interpretation_resume(
            saved_plan,
            resumed_plan,
            resume_context,
            catalog,
        )
        resumed_plan, context_decision = _prepare_context_bindings(
            resumed_plan,
            catalog,
            context_facts,
            pinned=pinned_targets,
            context_handle_states=self._sessions.context_handle_states(
                turn.session_id, _context_binding_handles(resumed_plan)
            ),
        )
        if context_decision is not None:
            return self._complete_context_binding_clarification(
                turn,
                resumed_plan,
                catalog,
                marker.digest,
                context_decision,
            )
        self._sessions.append_event(
            turn.turn_id, "clarification.resumed", "ok", {"planner_calls": 1}
        )
        execution = await self._executor.execute(resumed_plan, execution_context)
        return self._complete_execution(turn, resumed_plan, catalog, execution)

    def _complete_context_binding_clarification(
        self,
        turn: TurnRecord,
        plan: PlannerOutput,
        catalog: PinnedCatalog,
        database_marker: str,
        decision: _ContextBindingDecision,
    ) -> TurnRecord:
        pending = (
            PendingClarificationDraft(
                kind="context_binding",
                question_ru=decision.question_ru,
                original_question=turn.user_text,
                plan_json=plan.model_dump_json(by_alias=True),
                resolver_step_id=None,
                choices=decision.choices,
                has_more_candidates=False,
                catalog_snapshot_id=catalog.snapshot_id,
                catalog_revision=catalog.revision,
                database_marker=database_marker,
            )
            if decision.choices
            else None
        )
        self._sessions.append_event(
            turn.turn_id,
            "context.binding_clarification",
            "ok",
            {
                "step_id": decision.step_id,
                "parameter": decision.parameter,
                "choice_count": len(decision.choices),
            },
        )
        self._traces.put_artifact(
            turn.trace_id,
            "plan.json",
            canonicalize(plan.model_dump(mode="json", by_alias=True)),
        )
        self._put_answer_trace(
            turn,
            Outcome.CLARIFICATION_REQUIRED,
            decision.question_ru,
            fact_count=0,
        )
        completed = self._sessions.complete_turn(
            turn_id=turn.turn_id,
            assistant_text=decision.question_ru,
            status="completed",
            outcome=Outcome.CLARIFICATION_REQUIRED.value,
            plan_json=plan.model_dump_json(by_alias=True),
            evidence_json=None,
            context_exports=(),
            pending_clarification=pending,
        )
        self._sessions.append_event(turn.turn_id, "request.completed", "ok")
        self._put_terminal_trace(turn, ())
        return completed

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
        self._traces.put_artifact(
            turn.trace_id,
            "resolver-proofs.json",
            canonicalize(
                {
                    "selection_proofs": [
                        item.model_dump(mode="json")
                        for item in execution.selection_proofs
                    ],
                    "filter_retention_proofs": [
                        item.model_dump(mode="json")
                        for item in execution.filter_retention_proofs
                    ],
                }
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "filter-retention-proofs.json",
            canonicalize(
                {
                    "filter_retention_proofs": [
                        item.model_dump(mode="json")
                        for item in execution.filter_retention_proofs
                    ]
                }
            ),
        )
        self._traces.put_artifact(
            turn.trace_id,
            "context-mutations.json",
            canonicalize(
                {
                    "exports": [
                        {
                            "handle": item.handle,
                            "slot_key": item.slot_key,
                            "semantic_type": item.semantic_type,
                            "value_type": item.value_type.value,
                            "policy_mode": item.policy_mode,
                            "cardinality": item.cardinality,
                            "member_index": item.member_index,
                        }
                        for item in execution.context_facts
                    ]
                }
            ),
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
        answer = (
            execution.pending_clarification.question_ru
            if execution.pending_clarification is not None
            else render_execution(typed_plan, execution)
        )
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
            pending_clarification=execution.pending_clarification,
            error_code=(
                execution.evidence.errors[0].code if execution.evidence.errors else None
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

    def _put_terminal_trace(self, turn: TurnRecord, errors: tuple[object, ...]) -> None:
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
        if (
            isinstance(step, SkillCall)
            and (step.skill_id, step.skill_version) not in allowed
        ):
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
    proof = build_plan_coverage_proof(typed_plan, tuple(catalog.skills.values()))
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
                "required_by_requirement_ids": list(item.required_by_requirement_ids),
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


def _pending_choice(pending: PendingClarification) -> PendingChoice:
    choice = next(
        (
            item
            for item in pending.choices
            if item.choice_id == pending.claimed_choice_id
        ),
        None,
    )
    if choice is None:
        raise ApplicationError(
            "CLARIFICATION_CHOICE_INVALID", "Выбранный вариант не найден.", 422
        )
    return choice


def _pending_from_planner_decision(
    plan: PlannerOutput,
    turn: TurnRecord,
    catalog: PinnedCatalog,
    database_marker: str,
) -> PendingClarificationDraft | None:
    if not isinstance(plan.result, ClarifyResult) or not plan.result.choices:
        return None
    return PendingClarificationDraft(
        kind="interpretation_choice",
        question_ru=plan.result.question_ru,
        original_question=turn.user_text,
        plan_json=plan.model_dump_json(by_alias=True),
        resolver_step_id=None,
        choices=tuple(
            PendingChoice(
                choice_id=choice.choice_id,
                label_ru=choice.label_ru,
                binding=cast(
                    dict[str, JsonValue],
                    choice.binding.model_dump(mode="json", by_alias=True),
                ),
            )
            for choice in plan.result.choices
        ),
        has_more_candidates=False,
        catalog_snapshot_id=catalog.snapshot_id,
        catalog_revision=catalog.revision,
        database_marker=database_marker,
    )


def _narrow_resolver_plan(
    plan: PlannerOutput,
    resolver_step_id: str,
    criterion: str,
    catalog: PinnedCatalog,
) -> PlannerOutput:
    if not isinstance(plan.result, ExecuteResult):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID", "Resolver plan не является execute.", 409
        )
    target = next(
        (
            step
            for step in plan.result.steps
            if isinstance(step, SkillCall) and step.step_id == resolver_step_id
        ),
        None,
    )
    skill = None if target is None else catalog.skills.get(target.skill_id)
    if target is None or skill is None or skill.output_contract.resolution is None:
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID", "Resolver step больше недоступен.", 409
        )
    parameters = {item.name: item for item in skill.parameters}
    candidates = [
        argument
        for argument in target.arguments
        if (
            (parameter := parameters.get(argument.parameter)) is not None
            and parameter.value_type
            in {ParameterValueType.STRING, ParameterValueType.NORMALIZED_TEXT}
            and isinstance(argument.binding, LiteralBinding)
        )
    ]
    if len(candidates) != 1:
        raise ApplicationError(
            "CLARIFICATION_ACTION_INVALID",
            "Resolver не объявляет один изменяемый критерий поиска.",
            422,
        )
    selected = candidates[0]
    parameter = parameters[selected.parameter]
    new_binding = LiteralBinding(
        source="literal",
        value_type=cast(
            Literal["string", "normalized_text"], parameter.value_type.value
        ),
        value=criterion.strip(),
    )
    new_arguments = tuple(
        (
            SkillArgument(parameter=item.parameter, binding=new_binding)
            if item.parameter == selected.parameter
            else item
        )
        for item in target.arguments
    )
    new_target = target.model_copy(update={"arguments": new_arguments})
    new_steps = tuple(
        new_target if step.step_id == resolver_step_id else step
        for step in plan.result.steps
    )
    return plan.model_copy(
        update={"result": plan.result.model_copy(update={"steps": new_steps})}
    )


def _interpretation_resume_context(
    saved: PlannerOutput,
    pending: PendingClarification,
    stored_choice: PendingChoice,
) -> InterpretationResumeContext:
    if not isinstance(saved.result, ClarifyResult):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Interpretation checkpoint не является clarify plan.",
            409,
        )
    original_choice = next(
        (
            item
            for item in saved.result.choices
            if item.choice_id == stored_choice.choice_id
        ),
        None,
    )
    if original_choice is None or canonicalize(
        original_choice.binding.model_dump(mode="json", by_alias=True)
    ) != canonicalize(stored_choice.binding):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Сохраненный typed choice не совпадает с frozen interpretation.",
            409,
        )
    return InterpretationResumeContext(
        original_question=pending.original_question,
        frozen_interpretation=saved.interpretation,
        selected_choice_id=original_choice.choice_id,
        selected_slot_id=original_choice.slot_id,
        selected_binding=original_choice.binding,
    )


def _validate_interpretation_resume(
    saved: PlannerOutput,
    resumed: PlannerOutput,
    resume: InterpretationResumeContext,
    catalog: PinnedCatalog,
) -> frozenset[tuple[str, str]]:
    if not isinstance(saved.result, ClarifyResult) or not isinstance(
        resumed.result, ExecuteResult
    ):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Interpretation resume требует frozen clarify и executable plan.",
            409,
        )
    if canonicalize(
        resume.frozen_interpretation.model_dump(mode="json", by_alias=True)
    ) != canonicalize(saved.interpretation.model_dump(mode="json", by_alias=True)):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Переданный planner frozen interpretation поврежден.",
            409,
        )
    frozen = saved.interpretation
    current = resumed.interpretation
    if (
        current.intent_kind != frozen.intent_kind
        or current.goal_ru != frozen.goal_ru
        or canonicalize(
            [item.model_dump(mode="json") for item in current.required_facts]
        )
        != canonicalize(
            [item.model_dump(mode="json") for item in frozen.required_facts]
        )
    ):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Planner изменил frozen intent, goal или requirements.",
            422,
        )

    frozen_slots = {slot.slot_id: slot for slot in frozen.slots}
    resumed_slots = {slot.slot_id: slot for slot in current.slots}
    if set(frozen_slots) != set(resumed_slots):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Planner изменил набор frozen slots.",
            422,
        )
    for slot_id, frozen_slot in frozen_slots.items():
        resumed_slot = resumed_slots[slot_id]
        if slot_id != resume.selected_slot_id:
            if canonicalize(
                resumed_slot.model_dump(mode="json", by_alias=True)
            ) != canonicalize(frozen_slot.model_dump(mode="json", by_alias=True)):
                raise ApplicationError(
                    "CLARIFICATION_RESUME_INVALID",
                    "Planner изменил slot, которого не касался выбор пользователя.",
                    422,
                )
            continue
        if (
            resumed_slot.semantic_type != frozen_slot.semantic_type
            or resumed_slot.value_type != frozen_slot.value_type
            or resumed_slot.mentions != frozen_slot.mentions
            or resumed_slot.binding != resume.selected_binding
            or resumed_slot.status
            != (
                "resolved_context"
                if isinstance(resume.selected_binding, ContextBinding)
                else "resolved_literal"
            )
        ):
            raise ApplicationError(
                "CLARIFICATION_RESUME_INVALID",
                "Planner не установил exact selected binding в frozen slot.",
                422,
            )

    proof = build_plan_coverage_proof(resumed, tuple(catalog.skills.values()))
    expected = resume.selected_binding.model_dump(mode="json", by_alias=True)
    used_in_required_closure = False
    pinned_targets: set[tuple[str, str]] = set()
    for step in resumed.result.steps:
        if step.step_id not in proof.required_steps:
            continue
        if _contains_exact_binding(
            step.model_dump(mode="json", by_alias=True), expected
        ):
            used_in_required_closure = True
        if isinstance(step, SkillCall):
            for argument in step.arguments:
                if argument.binding == resume.selected_binding:
                    pinned_targets.add((step.step_id, argument.parameter))
    if not used_in_required_closure:
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Executable plan не использует selected binding в required closure.",
            422,
        )
    return frozenset(pinned_targets)


def _contains_exact_binding(value: object, expected: dict[str, JsonValue]) -> bool:
    if isinstance(value, dict):
        if value == expected:
            return True
        return any(_contains_exact_binding(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_binding(item, expected) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class _ContextBindingDecision:
    step_id: str
    parameter: str
    question_ru: str
    choices: tuple[PendingChoice, ...]


@dataclass(frozen=True, slots=True)
class _CompatibleContext:
    handle: str
    label_ru: str
    binding: ContextBinding


def _prepare_context_bindings(
    plan: PlannerOutput,
    catalog: PinnedCatalog,
    context_facts: tuple[ContextFact, ...],
    *,
    pinned: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
    context_handle_states: Mapping[str, str] | None = None,
) -> tuple[PlannerOutput, _ContextBindingDecision | None]:
    if not isinstance(plan.result, ExecuteResult):
        return plan, None
    changed_steps = list(plan.result.steps)
    for step_index, step in enumerate(plan.result.steps):
        if not isinstance(step, SkillCall):
            continue
        skill = catalog.skills.get(step.skill_id)
        if skill is None:
            continue
        parameters = {item.name: item for item in skill.parameters}
        arguments = list(step.arguments)
        changed = False
        for argument_index, argument in enumerate(step.arguments):
            if not isinstance(argument.binding, ContextBinding):
                continue
            parameter = parameters.get(argument.parameter)
            if parameter is None:
                continue
            target = (step.step_id, argument.parameter)
            compatible = _compatible_contexts(parameter, context_facts)
            state = (context_handle_states or {}).get(
                argument.binding.context_handle
            )
            if state in {"replaced", "expired", "invalidated"}:
                code = {
                    "replaced": "CONTEXT_HANDLE_REPLACED",
                    "expired": "CONTEXT_HANDLE_EXPIRED",
                    "invalidated": "CONTEXT_HANDLE_INVALIDATED",
                }[state]
                raise ApplicationError(
                    code,
                    "Context handle больше не является активным.",
                    409,
                )
            if state == "foreign":
                raise ApplicationError(
                    "CONTEXT_HANDLE_SESSION_MISMATCH",
                    "Context handle принадлежит другой сессии.",
                    409,
                )
            active_handle = any(
                fact.handle == argument.binding.context_handle
                for fact in context_facts
            )
            selected_is_compatible = any(
                item.binding == argument.binding for item in compatible
            )
            if active_handle and (
                argument.binding.expected_semantic_type != parameter.semantic_type
                or not selected_is_compatible
            ):
                raise ApplicationError(
                    "ENTITY_REF_CONTRACT_MISMATCH",
                    "Active context handle несовместим с consumer parameter.",
                    409,
                )
            if target in pinned:
                if not selected_is_compatible:
                    raise ApplicationError(
                        "CLARIFICATION_RESUME_INVALID",
                        "Выбранный context binding больше не совместим с consumer.",
                        409,
                    )
                continue
            if not compatible:
                return plan, _ContextBindingDecision(
                    step_id=step.step_id,
                    parameter=argument.parameter,
                    question_ru=(
                        f"Уточните значение «{parameter.title_ru}»: в активном "
                        "контексте нет подходящего подтвержденного выбора."
                    ),
                    choices=(),
                )
            if len(compatible) > 1:
                choices = tuple(
                    PendingChoice(
                        choice_id=f"c{index}",
                        label_ru=item.label_ru,
                        binding=cast(
                            dict[str, JsonValue],
                            item.binding.model_dump(mode="json", by_alias=True),
                        ),
                        target_step_id=step.step_id,
                        target_parameter=argument.parameter,
                    )
                    for index, item in enumerate(compatible, start=1)
                )
                return plan, _ContextBindingDecision(
                    step_id=step.step_id,
                    parameter=argument.parameter,
                    question_ru=f"Уточните вариант для «{parameter.title_ru}».",
                    choices=choices,
                )
            selected = compatible[0].binding
            if argument.binding != selected:
                arguments[argument_index] = SkillArgument(
                    parameter=argument.parameter,
                    binding=selected,
                )
                changed = True
        if changed:
            changed_steps[step_index] = step.model_copy(
                update={"arguments": tuple(arguments)}
            )
    if tuple(changed_steps) == plan.result.steps:
        return plan, None
    return (
        plan.model_copy(
            update={
                "result": plan.result.model_copy(update={"steps": tuple(changed_steps)})
            }
        ),
        None,
    )


def _compatible_contexts(
    parameter: Parameter, context_facts: tuple[ContextFact, ...]
) -> tuple[_CompatibleContext, ...]:
    slot_keys = set(parameter.context_slot_keys or ())
    if (
        "session_context" not in parameter.allowed_sources
        or not slot_keys
        or parameter.semantic_type is None
    ):
        return ()
    grouped: defaultdict[str, list[ContextFact]] = defaultdict(list)
    for fact in context_facts:
        grouped[fact.handle].append(fact)
    compatible: list[_CompatibleContext] = []
    for handle, raw_items in grouped.items():
        items = tuple(sorted(raw_items, key=lambda item: item.member_index))
        if (
            any(item.slot_key not in slot_keys for item in items)
            or any(item.semantic_type != parameter.semantic_type for item in items)
            or not _context_cardinality_matches(parameter, items)
            or not _context_value_type_matches(parameter, items)
        ):
            continue
        binding = ContextBinding(
            source="context",
            context_handle=handle,
            expected_semantic_type=parameter.semantic_type,
        )
        label = (
            items[0].presentation
            if len(items) == 1
            else f"{items[0].presentation} и еще {len(items) - 1}"
        )
        compatible.append(_CompatibleContext(handle, label[:160], binding))
    return tuple(
        sorted(compatible, key=lambda item: (item.label_ru.casefold(), item.handle))
    )


def _context_binding_handles(plan: PlannerOutput) -> tuple[str, ...]:
    if not isinstance(plan.result, ExecuteResult):
        return ()
    return tuple(
        dict.fromkeys(
            argument.binding.context_handle
            for step in plan.result.steps
            if isinstance(step, SkillCall)
            for argument in step.arguments
            if isinstance(argument.binding, ContextBinding)
        )
    )


def _context_cardinality_matches(
    parameter: Parameter, items: tuple[ContextFact, ...]
) -> bool:
    if parameter.value_type is ParameterValueType.ENTITY_REF_LIST:
        return bool(items) and all(item.cardinality == "many" for item in items)
    return len(items) == 1 and items[0].cardinality == "one"


def _context_value_type_matches(
    parameter: Parameter, items: tuple[ContextFact, ...]
) -> bool:
    if parameter.value_type in {
        ParameterValueType.ENTITY_REF,
        ParameterValueType.ENTITY_REF_LIST,
    }:
        return all(
            item.policy_mode == "selected_only"
            and item.value_type.value == "entity_ref"
            for item in items
        )
    if not all(
        item.policy_mode == "confirmed_filter"
        and item.value_type.value == parameter.value_type.value
        for item in items
    ):
        return False
    if parameter.value_type is ParameterValueType.ENUM:
        return all(str(item.value) in (parameter.allowed_values or ()) for item in items)
    return True


def _apply_context_binding_choice(
    plan: PlannerOutput, choice: PendingChoice
) -> tuple[PlannerOutput, tuple[str, str]]:
    if (
        not isinstance(plan.result, ExecuteResult)
        or choice.target_step_id is None
        or choice.target_parameter is None
    ):
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Context binding checkpoint не содержит exact target.",
            409,
        )
    try:
        binding = ContextBinding.model_validate(choice.binding)
    except ValueError as error:
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Context binding choice поврежден.",
            409,
        ) from error
    target_found = False
    steps = []
    for step in plan.result.steps:
        if not isinstance(step, SkillCall) or step.step_id != choice.target_step_id:
            steps.append(step)
            continue
        arguments = []
        for argument in step.arguments:
            if argument.parameter == choice.target_parameter:
                arguments.append(
                    SkillArgument(parameter=argument.parameter, binding=binding)
                )
                target_found = True
            else:
                arguments.append(argument)
        steps.append(step.model_copy(update={"arguments": tuple(arguments)}))
    if not target_found:
        raise ApplicationError(
            "CLARIFICATION_RESUME_INVALID",
            "Context binding target отсутствует в frozen plan.",
            409,
        )
    return (
        plan.model_copy(
            update={"result": plan.result.model_copy(update={"steps": tuple(steps)})}
        ),
        (choice.target_step_id, choice.target_parameter),
    )
