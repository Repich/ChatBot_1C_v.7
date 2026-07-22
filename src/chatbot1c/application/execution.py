"""Validated plan execution, fact normalization and evidence construction."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import (
    ContextFact,
    EntityFactOrigin,
    ExecuteQueryRequest,
    HelpChunk,
    HelpSearchRequest,
    PageContinuation,
    PendingChoice,
    PendingClarificationDraft,
    PinnedCatalog,
    ScalarFactOrigin,
)
from chatbot1c.application.operators import normalize_period
from chatbot1c.application.outcome_machine import (
    classify_failure,
    combine_step_outcomes,
)
from chatbot1c.application.ports import (
    DocumentationPort,
    ReadOnly1CPort,
    TraceRepository,
)
from chatbot1c.application.trace_paths import step_trace_prefix
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.semantic import (
    PlanCoverageProof,
    build_plan_coverage_proof,
    collection_obligation_satisfied,
    validate_context_proofs_against_evidence,
    validate_evidence_against_plan,
)
from chatbot1c.domain.evidence import (
    CatalogSkill,
    CatalogSnapshot,
    Citation,
    CitationValue,
    ContextExport,
    Coverage,
    CoverageRequirement,
    DatabaseStateMarker,
    DocumentFragment,
    EntityIdentity,
    EvidenceBundle,
    EvidenceError,
    Fact,
    FactValue,
    FilterRetentionProof,
    Pagination,
    ResolverUseProof,
    SelectionProof,
    SourceLocator,
    StepEvidence,
    UnitNotApplicable,
    UnitResolved,
)
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.plan import (
    Binding,
    ContextBinding,
    CountOperator,
    ExecuteResult,
    LiteralBinding,
    NormalizePeriodOperator,
    PlannerOutput,
    PlanStep,
    SkillCall,
    SlotBinding,
    StepBinding,
    SystemBinding,
)
from chatbot1c.domain.skill import (
    ConfirmedFilterContextPolicy,
    DataQueryOperation,
    DocumentationRetrievalOperation,
    FactDefinition,
    FactEqualsParameterConstraint,
    FactValueType,
    KeysetPagination,
    ParameterValueType,
    PrefixPagination,
    SelectedOnlyContextPolicy,
    Skill,
    UnitFixed,
    UnitFromFact,
    collection_scope_for_skill,
)
from chatbot1c.domain.types import EntityRef, Period


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    trace_id: UUID
    request_id: UUID
    session_id: UUID
    turn_id: UUID
    turn_time: datetime
    default_list_limit: int
    catalog: PinnedCatalog
    context_facts: tuple[ContextFact, ...]
    database_state_marker: DatabaseStateMarker
    context_handle_states: Mapping[str, str] | None = None
    deadline_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StepResult:
    step_id: str
    skill: Skill | None
    outcome: Outcome
    facts: tuple[Fact, ...]
    citations: tuple[Citation, ...]
    row_count: int
    started_at: datetime
    finished_at: datetime
    attempts: int
    error: EvidenceError | None = None
    truncated: bool = False
    has_more: bool = False
    continuation: ContinuationDraft | None = None
    criticality: Literal["required", "optional"] = "required"
    collection_scope: Literal["visible_page", "complete_set"] = "complete_set"
    empty_reason: Literal["not_found", "no_rows"] | None = None
    resolver_use: ResolverUseProof | None = None


@dataclass(frozen=True, slots=True)
class ContinuationDraft:
    step_id: str
    skill_id: str
    skill_version: str
    skill_digest: str
    arguments: dict[str, JsonValue]
    strategy: Literal["prefix", "keyset"]
    page_size: int
    cumulative_shown: int
    sort_tuple: tuple[JsonValue, ...]
    cursor_values: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    outcome: Outcome
    evidence: EvidenceBundle
    context_facts: tuple[ContextFact, ...]
    steps: tuple[StepResult, ...]
    continuation: ContinuationDraft | None = None
    selection_proofs: tuple[SelectionProof, ...] = ()
    filter_retention_proofs: tuple[FilterRetentionProof, ...] = ()
    pending_clarification: PendingClarificationDraft | None = None


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    value: JsonValue
    source: Literal["user_slot", "session_context", "previous_step", "system"]
    origins: tuple[EntityFactOrigin, ...] = ()
    context_items: tuple[ContextFact, ...] = ()


@dataclass(frozen=True, slots=True)
class _PageRequest:
    strategy: Literal["none", "prefix", "keyset"]
    page_size: int
    request_limit: int
    skip: int
    cumulative_before: int
    query_params: dict[str, JsonValue]


class PlanExecutor:
    def __init__(
        self,
        one_c: ReadOnly1CPort,
        documentation: DocumentationPort,
        traces: TraceRepository,
        *,
        documentation_revision: str = "unavailable",
        documentation_digest: str | None = None,
        configuration_profile_digest: str | None = None,
    ) -> None:
        self._one_c = one_c
        self._documentation = documentation
        self._traces = traces
        self._documentation_revision = documentation_revision
        self._documentation_digest = documentation_digest or _sha("unavailable")
        self._configuration_profile_digest = configuration_profile_digest or _sha(
            "ut-11.5.27.56"
        )

    async def execute(
        self,
        plan: PlannerOutput,
        context: ExecutionContext,
        *,
        resolver_resume: tuple[str, tuple[Fact, ...]] | None = None,
    ) -> ExecutionResult:
        if not isinstance(plan.result, ExecuteResult):
            raise ValueError("PlanExecutor accepts execute decisions only")
        coverage_proof = build_plan_coverage_proof(
            plan, tuple(context.catalog.skills.values())
        )
        resolver_uses = _derive_resolver_use_proofs(plan, context.catalog)
        context_index: dict[str, tuple[ContextFact, ...]] = {}
        for item in context.context_facts:
            context_index[item.handle] = (*context_index.get(item.handle, ()), item)
        step_results: dict[str, StepResult] = {}
        ordered: list[StepResult] = []
        if resolver_resume is not None:
            resume_step_id, resume_facts = resolver_resume
            use = resolver_uses.get(resume_step_id)
            call = next(
                (
                    item
                    for item in plan.result.steps
                    if isinstance(item, SkillCall) and item.step_id == resume_step_id
                ),
                None,
            )
            skill = None if call is None else context.catalog.skills.get(call.skill_id)
            if use is None or use.mode != "select_one" or skill is None:
                raise ApplicationError(
                    "CLARIFICATION_RESUME_INVALID",
                    "Сохраненный resolver checkpoint несовместим с plan.",
                    409,
                )
            now = datetime.now(UTC)
            resumed = StepResult(
                step_id=resume_step_id,
                skill=skill,
                outcome=Outcome.SUCCESS_WITH_ROWS,
                facts=resume_facts,
                citations=(),
                row_count=1,
                started_at=now,
                finished_at=now,
                attempts=0,
                collection_scope="complete_set",
                resolver_use=use,
            )
            if len(_resolver_identity_facts(resumed)) != 1:
                raise ApplicationError(
                    "CLARIFICATION_RESUME_INVALID",
                    "Выбранный resolver candidate не имеет exact identity.",
                    409,
                )
            step_results[resume_step_id] = resumed
            ordered.append(resumed)
        required_ids = coverage_proof.required_steps
        scheduled = (
            [step for step in plan.result.steps if step.step_id in required_ids],
            [step for step in plan.result.steps if step.step_id not in required_ids],
        )
        failed_or_blocked: set[str] = set()
        required_failed = False
        stop_all = False
        for phase, steps in enumerate(scheduled):
            if phase == 1 and (required_failed or stop_all):
                break
            for step in steps:
                if step.step_id in step_results:
                    continue
                if _runtime_step_dependencies(step) & failed_or_blocked:
                    failed_or_blocked.add(step.step_id)
                    continue
                criticality: Literal["required", "optional"] = (
                    "required" if phase == 0 else "optional"
                )
                result = await self._execute_plan_step(
                    plan,
                    step,
                    context,
                    context_index,
                    step_results,
                )
                resolver_use = resolver_uses.get(step.step_id)
                if resolver_use is not None:
                    result = _apply_resolver_state(result, resolver_use)
                result = replace(result, criticality=criticality)
                step_results[step.step_id] = result
                ordered.append(result)
                failure = result.outcome in {
                    Outcome.QUERY_ERROR,
                    Outcome.MCP_UNAVAILABLE,
                    Outcome.CONTRACT_ERROR,
                }
                if failure:
                    failed_or_blocked.add(step.step_id)
                    if criticality == "required":
                        required_failed = True
                        if result.outcome is Outcome.CONTRACT_ERROR:
                            stop_all = True
                            break
                    continue
                if result.outcome is Outcome.CLARIFICATION_REQUIRED:
                    stop_all = True
                    break
                if (
                    resolver_use is not None
                    and resolver_use.mode == "select_set"
                    and result.outcome is Outcome.PARTIAL
                ):
                    stop_all = True
                    break
                if (
                    criticality == "required"
                    and result.outcome
                    in {Outcome.SUCCESS_EMPTY, Outcome.DOCUMENTATION_EMPTY}
                    and isinstance(step, SkillCall)
                    and step.on_empty == "stop_not_found"
                ):
                    stop_all = True
                    break
            if stop_all:
                break

        step_order = {
            step.step_id: index for index, step in enumerate(plan.result.steps)
        }
        ordered.sort(key=lambda result: step_order[result.step_id])
        outcome = _overall_outcome(ordered, plan, coverage_proof)
        selection_proofs = _selection_proofs(
            tuple(ordered), allowed_step_ids=frozenset(coverage_proof.required_steps)
        )
        filter_retention_proofs = _filter_retention_proofs(
            plan, coverage_proof, tuple(ordered)
        )
        evidence = self._build_evidence(
            plan,
            coverage_proof,
            context,
            tuple(ordered),
            outcome,
            selection_proofs,
            filter_retention_proofs,
        )
        outcome, evidence = _finalize_coverage_outcome(
            plan, coverage_proof, outcome, evidence
        )
        context_commit_allowed = _context_commit_allowed(tuple(ordered), outcome)
        if not context_commit_allowed:
            selection_proofs = ()
            filter_retention_proofs = ()
            evidence = evidence.model_copy(
                update={
                    "context_exports": (),
                }
            )
        validate_context_proofs_against_evidence(
            coverage_proof,
            evidence,
            selection_proofs=selection_proofs,
            filter_retention_proofs=filter_retention_proofs,
            available_skills=tuple(context.catalog.skills.values()),
        )
        exported_context = (
            ()
            if not context_commit_allowed
            else _context_facts(
                evidence,
                context.turn_id,
                tuple(ordered),
                selection_proofs,
                filter_retention_proofs,
            )
        )
        pending_clarification = _pending_from_resolver(plan, context, tuple(ordered))
        continuations = [
            result.continuation for result in ordered if result.continuation is not None
        ]
        if pending_clarification is not None:
            continuations = []
        if len(continuations) > 1:
            raise ApplicationError(
                "MULTIPLE_CONTINUATIONS_UNSUPPORTED",
                "Один turn не может публиковать несколько продолжений списка.",
                422,
            )
        return ExecutionResult(
            outcome,
            evidence,
            exported_context,
            tuple(ordered),
            continuations[0] if continuations else None,
            selection_proofs,
            filter_retention_proofs,
            pending_clarification,
        )

    async def execute_continuation(
        self,
        plan: PlannerOutput,
        context: ExecutionContext,
        continuation: PageContinuation,
    ) -> ExecutionResult:
        if not isinstance(plan.result, ExecuteResult):
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Сохраненный plan не является execute-plan.",
                409,
            )
        call = next(
            (
                step
                for step in plan.result.steps
                if isinstance(step, SkillCall) and step.step_id == continuation.step_id
            ),
            None,
        )
        skill = context.catalog.skills.get(continuation.skill_id)
        if (
            call is None
            or skill is None
            or call.skill_id != continuation.skill_id
            or call.skill_version != continuation.skill_version
            or skill.version != continuation.skill_version
            or skill.integrity.digest != continuation.skill_digest
        ):
            raise ApplicationError(
                "CONTINUATION_CATALOG_CHANGED",
                "Сохраненный шаг не совпадает с pinned catalog.",
                409,
            )
        if not isinstance(skill.operation, DataQueryOperation):
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Продолжение разрешено только для data-query skill.",
                409,
            )
        started = datetime.now(UTC)
        try:
            result = await self._execute_data(
                call,
                skill,
                dict(continuation.arguments),
                context,
                started,
                continuation=continuation,
            )
        except ApplicationError as error:
            result = _failed_step(
                call.step_id,
                skill,
                (
                    Outcome.MCP_UNAVAILABLE
                    if error.code in {"MCP_UNAVAILABLE", "MCP_DEADLINE_EXCEEDED"}
                    else Outcome.CONTRACT_ERROR
                ),
                error,
                started,
            )
        coverage_proof = build_plan_coverage_proof(
            plan, tuple(context.catalog.skills.values())
        )
        outcome = _overall_outcome([result], plan, coverage_proof)
        evidence = self._build_evidence(
            plan, coverage_proof, context, (result,), outcome, (), ()
        )
        outcome, evidence = _finalize_coverage_outcome(
            plan, coverage_proof, outcome, evidence
        )
        validate_context_proofs_against_evidence(
            coverage_proof,
            evidence,
            selection_proofs=(),
            filter_retention_proofs=(),
            available_skills=tuple(context.catalog.skills.values()),
        )
        exported_context = (
            ()
            if outcome is Outcome.CONTRACT_ERROR
            else _context_facts(evidence, context.turn_id, (result,), (), ())
        )
        return ExecutionResult(
            outcome,
            evidence,
            exported_context,
            (result,),
            result.continuation,
        )

    async def _execute_plan_step(
        self,
        plan: PlannerOutput,
        step: PlanStep,
        context: ExecutionContext,
        context_index: dict[str, tuple[ContextFact, ...]],
        previous: dict[str, StepResult],
    ) -> StepResult:
        if isinstance(step, SkillCall):
            skill = context.catalog.skills.get(step.skill_id)
            if skill is None or skill.version != step.skill_version:
                raise ApplicationError(
                    "PLAN_SKILL_MISSING",
                    f"Pinned skill {step.skill_id}@{step.skill_version} отсутствует.",
                    409,
                )
            return await self._execute_skill(
                plan, step, skill, context, context_index, previous
            )
        if isinstance(step, NormalizePeriodOperator):
            return self._execute_normalize_period(
                plan, step, context, context_index, previous
            )
        if isinstance(step, CountOperator):
            return self._execute_count(step, context, previous)
        raise ApplicationError(
            "OPERATOR_NOT_IMPLEMENTED",
            f"Оператор {step.operator} еще не входит в slice 2.",
            422,
        )

    async def _execute_skill(
        self,
        plan: PlannerOutput,
        call: SkillCall,
        skill: Skill,
        context: ExecutionContext,
        context_index: dict[str, tuple[ContextFact, ...]],
        previous: dict[str, StepResult],
    ) -> StepResult:
        started = datetime.now(UTC)
        try:
            resolved_arguments = {
                argument.parameter: self._resolve_binding(
                    plan,
                    argument.binding,
                    context,
                    context_index,
                    previous,
                )
                for argument in call.arguments
            }
            _validate_runtime_arguments(skill, resolved_arguments)
            arguments = {
                parameter: resolved.value
                for parameter, resolved in resolved_arguments.items()
            }
            if isinstance(skill.operation, DataQueryOperation):
                result = await self._execute_data(
                    call, skill, arguments, context, started
                )
            else:
                result = await self._execute_documentation(
                    call, skill, arguments, context, started
                )
            return result
        except ApplicationError as error:
            outcome = classify_failure(error.code, stage="mcp")
            return _failed_step(call.step_id, skill, outcome, error, started)

    async def _execute_data(
        self,
        call: SkillCall,
        skill: Skill,
        arguments: dict[str, JsonValue],
        context: ExecutionContext,
        started: datetime,
        *,
        continuation: PageContinuation | None = None,
    ) -> StepResult:
        operation = cast(DataQueryOperation, skill.operation)
        params: dict[str, JsonValue] = {}
        for binding in operation.parameter_bindings:
            if binding.parameter not in arguments:
                parameter = next(
                    item for item in skill.parameters if item.name == binding.parameter
                )
                if not parameter.required:
                    params[binding.query_parameter] = (
                        None
                        if parameter.default is None
                        else _encode_parameter(parameter.default, binding.encoding)
                    )
                continue
            params[binding.query_parameter] = _encode_parameter(
                arguments[binding.parameter], binding.encoding
            )
        page = _page_request(
            operation,
            default_page_size=context.default_list_limit,
            continuation=continuation,
        )
        params.update(page.query_params)
        request = ExecuteQueryRequest(
            query=operation.query_template.text,
            params=params,
            limit=page.request_limit,
            include_schema=True,
            deadline_at=context.deadline_at,
        )
        self._traces.put_artifact(
            context.trace_id,
            f"{step_trace_prefix(call.step_id)}/request.json",
            canonicalize(request.model_dump(mode="json", by_alias=True)),
        )
        envelope = await self._one_c.execute_query(request)
        self._traces.put_artifact(
            context.trace_id,
            f"{step_trace_prefix(call.step_id)}/response.json",
            canonicalize(envelope.model_dump(mode="json", by_alias=True)),
        )
        finished = datetime.now(UTC)
        if not envelope.success:
            error = _evidence_error(
                "QUERY_ERROR",
                "execution",
                "mcp",
                False,
                "1С вернула ошибку выполнения read-only запроса.",
                call.step_id,
            )
            return StepResult(
                call.step_id,
                skill,
                Outcome.QUERY_ERROR,
                (),
                (),
                0,
                started,
                finished,
                envelope.attempts,
                error=error,
                collection_scope=collection_scope_for_skill(skill),
            )
        _validate_response_columns(skill, envelope)
        raw_rows = tuple(envelope.data)
        if page.skip > len(raw_rows):
            raise ApplicationError(
                "CONTINUATION_PREFIX_DRIFT",
                "Prefix page больше не содержит ранее показанные строки.",
                409,
            )
        if continuation is not None and page.strategy == "prefix" and page.skip:
            boundary_row = raw_rows[page.skip - 1]
            _validate_projected_rows(skill, (boundary_row,))
            boundary_tuple, _ = _continuation_values(operation, boundary_row)
            if canonicalize(boundary_tuple) != canonicalize(continuation.sort_tuple):
                raise ApplicationError(
                    "CONTINUATION_PREFIX_DRIFT",
                    "Stable-order boundary исходной страницы изменилась.",
                    409,
                )
        page_rows = raw_rows[page.skip :]
        if continuation is not None and not page_rows:
            raise ApplicationError(
                "CONTINUATION_PREFIX_DRIFT",
                "Продолжение не вернуло ожидаемую следующую строку.",
                409,
            )
        has_probe_row = len(page_rows) > page.page_size
        has_more = has_probe_row or envelope.has_more or envelope.truncated
        visible_rows = page_rows[: page.page_size]
        structural_rows = page_rows[: page.page_size + 1]
        effective_empty = _validate_projected_rows(skill, structural_rows)
        if effective_empty:
            return _empty_step(
                call.step_id,
                skill,
                arguments,
                started,
                finished,
                envelope.attempts,
            )
        _validate_result_cardinality(skill, len(page_rows))
        if (
            skill.output_contract.cardinality in {"exactly_one", "zero_or_one"}
            and len(page_rows) > 1
        ):
            error = _evidence_error(
                "ENTITY_RESULT_AMBIGUOUS",
                "execution",
                "none",
                False,
                "Найдено несколько объектов; требуется уточнение выбора.",
                call.step_id,
            )
            return StepResult(
                call.step_id,
                skill,
                Outcome.CLARIFICATION_REQUIRED,
                (),
                (),
                len(page_rows),
                started,
                finished,
                envelope.attempts,
                error=error,
                collection_scope=collection_scope_for_skill(skill),
            )
        if has_more and operation.pagination.strategy == "none":
            raise ApplicationError(
                "RESULT_PAGINATION_UNDECLARED",
                "MCP вернул неполный результат для skill без pagination contract.",
                502,
            )
        truncation_policy = skill.output_contract.sufficiency.truncation_policy
        if has_more and truncation_policy == "error_if_truncated":
            raise ApplicationError(
                "RESULT_TRUNCATED_FORBIDDEN",
                "Skill contract запрещает усеченный результат.",
                502,
            )
        visible_envelope = envelope.model_copy(
            update={"data": visible_rows, "count": len(visible_rows)}
        )
        facts = _normalize_data_facts(
            context.trace_id, call.step_id, skill, visible_envelope
        )
        _validate_bound_entity_identity(skill, arguments, facts)
        _validate_result_sufficiency(skill, facts)
        zero = skill.output_contract.cardinality == "aggregate" and any(
            fact.fact_id in skill.output_contract.sufficiency.zero_fact_ids
            and type(fact.value) in {int, float}
            and fact.value == 0
            for fact in facts
        )
        continuation_draft: ContinuationDraft | None = None
        if has_more:
            if not visible_rows:
                raise ApplicationError(
                    "RESULT_PAGINATION_INVALID",
                    "Нельзя построить continuation без показанной строки.",
                    502,
                )
            sort_tuple, cursor_values = _continuation_values(
                operation, visible_rows[-1]
            )
            continuation_draft = ContinuationDraft(
                step_id=call.step_id,
                skill_id=skill.skill_id,
                skill_version=skill.version,
                skill_digest=skill.integrity.digest,
                arguments=dict(arguments),
                strategy=cast(Literal["prefix", "keyset"], page.strategy),
                page_size=page.page_size,
                cumulative_shown=page.cumulative_before + len(visible_rows),
                sort_tuple=sort_tuple,
                cursor_values=cursor_values,
            )
        step_outcome = (
            Outcome.ZERO_AGGREGATE
            if zero
            else Outcome.PARTIAL
            if has_more and truncation_policy == "partial_until_all_pages"
            else Outcome.SUCCESS_WITH_ROWS
        )
        return StepResult(
            call.step_id,
            skill,
            step_outcome,
            facts,
            (),
            len(visible_rows),
            started,
            finished,
            envelope.attempts,
            truncated=has_more,
            has_more=has_more,
            continuation=continuation_draft,
            collection_scope=collection_scope_for_skill(skill),
        )

    async def _execute_documentation(
        self,
        call: SkillCall,
        skill: Skill,
        arguments: dict[str, JsonValue],
        context: ExecutionContext,
        started: datetime,
    ) -> StepResult:
        operation = cast(DocumentationRetrievalOperation, skill.operation)
        value = arguments.get(operation.query_parameter)
        if not isinstance(value, str):
            raise ApplicationError(
                "DOCUMENTATION_QUERY_MISSING",
                "Для поиска в справке требуется строковый параметр.",
                422,
            )
        request = HelpSearchRequest(
            query=value,
            metadata_kinds=operation.filters.metadata_kinds,
            path_prefixes=operation.filters.path_prefixes,
            roles=operation.chunk_roles,
            top_k=operation.retrieval.top_k,
            max_chunks_per_source=operation.retrieval.max_chunks_per_source,
        )
        chunks = await self._documentation.search(request)
        finished = datetime.now(UTC)
        if not chunks:
            return StepResult(
                call.step_id,
                skill,
                Outcome.DOCUMENTATION_EMPTY,
                (),
                (),
                0,
                started,
                finished,
                1,
                collection_scope="visible_page",
            )
        facts, citations = _normalize_documentation_facts(
            context.trace_id, call.step_id, skill, chunks
        )
        return StepResult(
            call.step_id,
            skill,
            Outcome.DOCUMENTATION_FOUND,
            facts,
            citations,
            len(chunks),
            started,
            finished,
            1,
            collection_scope="visible_page",
        )

    def _execute_normalize_period(
        self,
        plan: PlannerOutput,
        step: NormalizePeriodOperator,
        context: ExecutionContext,
        context_index: dict[str, tuple[ContextFact, ...]],
        previous: dict[str, StepResult],
    ) -> StepResult:
        started = datetime.now(UTC)
        resolved = self._resolve_binding(
            plan, step.expression, context, context_index, previous
        )
        period = normalize_period(resolved.value, turn_time=context.turn_time)
        fact = Fact(
            fact_instance_id=uuid5(
                context.trace_id, f"{step.step_id}:{step.result_fact_id}:period"
            ),
            row_id=f"row_{_sha(step.step_id + period.start.isoformat())[:16]}",
            fact_id=step.result_fact_id,
            semantic_type="time.period",
            value_type=FactValueType.PERIOD,
            value=period,
            confirmation="confirmed",
            step_id=step.step_id,
            source_locator=SourceLocator(
                kind="operator_result", reference="normalize_period"
            ),
            unit=UnitNotApplicable(mode="not_applicable"),
            period=period,
        )
        finished = datetime.now(UTC)
        return StepResult(
            step.step_id,
            None,
            Outcome.SUCCESS_WITH_ROWS,
            (fact,),
            (),
            1,
            started,
            finished,
            0,
            collection_scope="complete_set",
        )

    def _execute_count(
        self,
        step: CountOperator,
        context: ExecutionContext,
        previous: dict[str, StepResult],
    ) -> StepResult:
        started = datetime.now(UTC)
        source = previous.get(step.input_step_id)
        if source is None:
            raise ApplicationError(
                "OPERATOR_INPUT_STEP_MISSING",
                "Count operator не получил declared input step.",
                422,
            )
        if source.collection_scope != "complete_set":
            raise ApplicationError(
                "OPERATOR_COLLECTION_SCOPE_MISMATCH",
                "Count operator не вычисляет total по page-scoped evidence.",
                422,
            )
        if source.has_more or source.truncated:
            raise ApplicationError(
                "OPERATOR_INPUT_INCOMPLETE",
                "Count operator не вычисляет total по неполной странице.",
                422,
            )
        if not step.distinct_by_fact_ids:
            raise ApplicationError(
                "OPERATOR_DISTINCT_IDENTITY_MISSING",
                "Count operator требует declared distinct identity.",
                422,
            )
        by_row: dict[str, dict[str, Fact]] = {}
        for fact in source.facts:
            row = by_row.setdefault(fact.row_id, {})
            if fact.fact_id in row:
                raise ApplicationError(
                    "OPERATOR_INPUT_FACT_DUPLICATE",
                    "Count operator получил duplicate identity fact в одной строке.",
                    502,
                )
            row[fact.fact_id] = fact
        identities: set[bytes] = set()
        for row in by_row.values():
            if any(fact_id not in row for fact_id in step.distinct_by_fact_ids):
                raise ApplicationError(
                    "OPERATOR_INPUT_FACT_MISSING",
                    "Count operator не получил полную declared row identity.",
                    502,
                )
            identities.add(
                canonicalize(
                    [
                        _json_value(row[fact_id].value)
                        for fact_id in step.distinct_by_fact_ids
                    ]
                )
            )
        value = len(identities)
        row_id = f"row_{_sha(step.step_id + ':' + str(value))[:16]}"
        fact = Fact(
            fact_instance_id=uuid5(
                context.trace_id, f"{step.step_id}:{row_id}:{step.result_fact_id}"
            ),
            row_id=row_id,
            fact_id=step.result_fact_id,
            semantic_type="measure.count",
            value_type=FactValueType.INTEGER,
            value=value,
            confirmation="confirmed",
            step_id=step.step_id,
            source_locator=SourceLocator(kind="operator_result", reference="count"),
            unit=UnitNotApplicable(mode="not_applicable"),
        )
        finished = datetime.now(UTC)
        return StepResult(
            step.step_id,
            None,
            Outcome.ZERO_AGGREGATE if value == 0 else Outcome.SUCCESS_WITH_ROWS,
            (fact,),
            (),
            1,
            started,
            finished,
            0,
            collection_scope=source.collection_scope,
        )

    def _resolve_binding(
        self,
        plan: PlannerOutput,
        binding: Binding,
        context: ExecutionContext,
        context_index: dict[str, tuple[ContextFact, ...]],
        previous: dict[str, StepResult],
    ) -> ResolvedBinding:
        if isinstance(binding, LiteralBinding):
            return ResolvedBinding(
                cast(JsonValue, binding.model_dump(mode="json")["value"]),
                "user_slot",
            )
        if isinstance(binding, ContextBinding):
            context_facts = context_index.get(binding.context_handle)
            if not context_facts:
                state = (context.context_handle_states or {}).get(
                    binding.context_handle
                )
                if state == "replaced":
                    raise ApplicationError(
                        "CONTEXT_HANDLE_REPLACED",
                        "Context handle заменен новым поколением.",
                        409,
                    )
                if state == "expired":
                    raise ApplicationError(
                        "CONTEXT_HANDLE_EXPIRED", "Context handle истек.", 409
                    )
                if state == "invalidated":
                    raise ApplicationError(
                        "CONTEXT_HANDLE_INVALIDATED",
                        "Context handle недействителен.",
                        409,
                    )
                raise ApplicationError(
                    "CONTEXT_PROVENANCE_MISSING",
                    "Context handle не имеет подтвержденной provenance в сессии.",
                    409,
                )
            if any(
                item.semantic_type != binding.expected_semantic_type
                for item in context_facts
            ):
                raise ApplicationError(
                    "ENTITY_REF_CONTRACT_MISMATCH",
                    "Context handle имеет другой semantic type.",
                    409,
                )
            if len(context_facts) == 1 and context_facts[0].cardinality == "one":
                item = context_facts[0]
                return ResolvedBinding(
                    item.value,
                    "session_context",
                    (
                        (item.origin,)
                        if isinstance(item.origin, EntityFactOrigin)
                        else ()
                    ),
                    (item,),
                )
            if any(item.cardinality != "many" for item in context_facts):
                raise ApplicationError(
                    "ENTITY_REF_CONTRACT_MISMATCH",
                    "Context handle содержит несовместимую cardinality.",
                    409,
                )
            return ResolvedBinding(
                cast(JsonValue, [item.value for item in context_facts]),
                "session_context",
                tuple(
                    item.origin
                    for item in context_facts
                    if isinstance(item.origin, EntityFactOrigin)
                ),
                context_facts,
            )
        if isinstance(binding, StepBinding):
            result = previous.get(binding.step_id)
            if result is None:
                raise ApplicationError(
                    "PLAN_STEP_BINDING_MISSING",
                    f"Previous step {binding.step_id} не выполнен.",
                    422,
                )
            facts = [fact for fact in result.facts if fact.fact_id == binding.fact_id]
            if (
                result.resolver_use is not None
                and binding.fact_id == result.resolver_use.identity_fact_id
            ):
                facts = list(_resolver_identity_facts(result))
            if binding.cardinality == "one":
                if len(facts) != 1:
                    raise ApplicationError(
                        "PLAN_STEP_CARDINALITY_MISMATCH",
                        "Step binding требует ровно один confirmed fact.",
                        422,
                    )
                result_fact = facts[0]
                fact_origins: tuple[EntityFactOrigin, ...] = (
                    (_entity_fact_origin(result_fact, result.skill),)
                    if isinstance(result_fact.value, EntityRef)
                    and result.skill is not None
                    else ()
                )
                return ResolvedBinding(
                    _json_value(result_fact.value), "previous_step", fact_origins
                )
            fact_origins = tuple(
                _entity_fact_origin(fact, result.skill)
                for fact in facts
                if isinstance(fact.value, EntityRef) and result.skill is not None
            )
            return ResolvedBinding(
                cast(JsonValue, [_json_value(fact.value) for fact in facts]),
                "previous_step",
                fact_origins,
            )
        if isinstance(binding, SystemBinding):
            if binding.name == "turn_time":
                return ResolvedBinding(context.turn_time.isoformat(), "system")
            if binding.name == "default_list_limit":
                return ResolvedBinding(context.default_list_limit, "system")
            if binding.name == "database_state_marker":
                return ResolvedBinding(context.database_state_marker.digest, "system")
            raise ApplicationError(
                "PAGE_CURSOR_MISSING", "Continuation cursor отсутствует.", 409
            )
        if isinstance(binding, SlotBinding):
            slot = next(
                (
                    item
                    for item in plan.interpretation.slots
                    if item.slot_id == binding.slot_id
                ),
                None,
            )
            if slot is None or slot.binding is None:
                raise ApplicationError(
                    "PLAN_SLOT_UNRESOLVED",
                    f"Slot {binding.slot_id} не имеет exact binding.",
                    422,
                )
            return self._resolve_binding(
                plan, slot.binding, context, context_index, previous
            )
        raise TypeError(f"unsupported binding {type(binding)!r}")

    def _build_evidence(
        self,
        plan: PlannerOutput,
        coverage_proof: PlanCoverageProof,
        context: ExecutionContext,
        results: tuple[StepResult, ...],
        outcome: Outcome,
        selection_proofs: tuple[SelectionProof, ...],
        filter_retention_proofs: tuple[FilterRetentionProof, ...],
    ) -> EvidenceBundle:
        facts = tuple(fact for result in results for fact in result.facts)
        citations = tuple(
            citation for result in results for citation in result.citations
        )
        errors = tuple(result.error for result in results if result.error is not None)
        final_refs = (
            {(ref.step_id, ref.fact_id) for ref in plan.result.final_outputs}
            if isinstance(plan.result, ExecuteResult)
            else set()
        )
        requirements: list[CoverageRequirement] = []
        proof_by_requirement = {
            requirement.requirement_id: requirement
            for requirement in coverage_proof.requirements
        }
        for requirement in plan.interpretation.required_facts:
            proof = proof_by_requirement.get(requirement.requirement_id)
            candidates = (
                ()
                if proof is None
                or proof.final_step_id is None
                or proof.final_fact_id is None
                else tuple(
                    fact
                    for fact in facts
                    if fact.semantic_type == requirement.semantic_type
                    and fact.step_id == proof.final_step_id
                    and fact.fact_id == proof.final_fact_id
                    and (fact.step_id, fact.fact_id) in final_refs
                )
            )
            covered = _runtime_requirement_covered(requirement, candidates)
            requirements.append(
                CoverageRequirement(
                    requirement_id=requirement.requirement_id,
                    semantic_type=requirement.semantic_type,
                    required=requirement.required,
                    status=(
                        CoverageStatus.COVERED if covered else CoverageStatus.MISSING
                    ),
                    fact_instance_ids=(
                        tuple(fact.fact_instance_id for fact in candidates)
                        if covered
                        else ()
                    ),
                )
            )
        exports = (
            ()
            if outcome is Outcome.CONTRACT_ERROR
            else _context_exports(selection_proofs, filter_retention_proofs, facts)
        )
        source_boundary = cast(
            Literal["data", "documentation", "mixed", "none"],
            (
                plan.interpretation.intent_kind
                if plan.interpretation.intent_kind in {"data", "documentation", "mixed"}
                else "none"
            ),
        )
        evidence = EvidenceBundle(
            schema_version="1.1.0",
            document_type="evidence_bundle",
            trace_id=context.trace_id,
            request_id=context.request_id,
            session_id=context.session_id,
            created_at=datetime.now(UTC),
            source_boundary=source_boundary,
            outcome=outcome,
            empty_reason=_empty_reason_for_outcome(results, outcome),
            catalog_snapshot=CatalogSnapshot(
                snapshot_id=context.catalog.snapshot_id,
                revision=context.catalog.revision,
                skills=tuple(
                    CatalogSkill(
                        skill_id=skill.skill_id,
                        version=skill.version,
                        digest=skill.integrity.digest,
                    )
                    for skill in sorted(
                        context.catalog.skills.values(), key=lambda item: item.skill_id
                    )
                ),
            ),
            database_state_marker=self._marker(context),
            steps=tuple(_step_evidence(result) for result in results),
            facts=facts,
            citations=citations,
            documentation_disagreements=(),
            coverage=Coverage(sufficient=False, requirements=tuple(requirements)),
            pagination=_evidence_pagination(results, context.default_list_limit),
            context_exports=exports,
            errors=errors,
        )
        sufficient = all(
            not requirement.required
            or (
                requirement.status is CoverageStatus.COVERED
                and (proof := proof_by_requirement.get(requirement.requirement_id))
                is not None
                and collection_obligation_satisfied(proof, evidence)
            )
            for requirement in requirements
        )
        return evidence.model_copy(
            update={
                "coverage": Coverage(
                    sufficient=sufficient,
                    requirements=tuple(requirements),
                )
            }
        )

    def _marker(self, context: ExecutionContext) -> DatabaseStateMarker:
        return context.database_state_marker


def _page_request(
    operation: DataQueryOperation,
    *,
    default_page_size: int,
    continuation: PageContinuation | None,
) -> _PageRequest:
    pagination = operation.pagination
    maximum = operation.query_template.mcp_limit.maximum
    if pagination.strategy == "none":
        if continuation is not None:
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Skill больше не объявляет pagination.",
                409,
            )
        return _PageRequest(
            "none",
            operation.query_template.mcp_limit.default,
            operation.query_template.mcp_limit.default,
            0,
            0,
            {},
        )

    page_size = (
        continuation.page_size
        if continuation is not None
        else min(default_page_size, maximum - 1)
    )
    if page_size < 1 or page_size + 1 > maximum:
        raise ApplicationError(
            "RESULT_PAGINATION_INVALID",
            "Pagination contract не оставляет место для probe row.",
            502,
        )
    if continuation is not None and continuation.strategy != pagination.strategy:
        raise ApplicationError(
            "CONTINUATION_PLAN_INVALID",
            "Pagination strategy сохраненного continuation изменилась.",
            409,
        )
    if isinstance(pagination, PrefixPagination):
        shown = 0 if continuation is None else continuation.shown
        request_limit = min(
            shown + page_size + 1,
            pagination.maximum_total,
            maximum,
        )
        if request_limit <= shown:
            raise ApplicationError(
                "CONTINUATION_PREFIX_EXHAUSTED",
                "Prefix continuation достиг declared maximum_total.",
                409,
            )
        return _PageRequest("prefix", page_size, request_limit, shown, shown, {})
    if not isinstance(pagination, KeysetPagination):
        raise TypeError("unknown pagination policy")
    query_params: dict[str, JsonValue] = {
        pagination.has_cursor_query_parameter: continuation is not None
    }
    if continuation is None:
        query_params.update(
            {binding.query_parameter: None for binding in pagination.cursor_bindings}
        )
        shown = 0
    else:
        expected = {binding.query_parameter for binding in pagination.cursor_bindings}
        if set(continuation.cursor_values) != expected:
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Сохраненные cursor bindings не совпадают с skill contract.",
                409,
            )
        query_params.update(dict(continuation.cursor_values))
        shown = continuation.shown
    return _PageRequest("keyset", page_size, page_size + 1, 0, shown, query_params)


def _validate_response_columns(skill: Skill, envelope: object) -> None:
    from chatbot1c.application.models import ExecuteQueryEnvelope

    normalized = cast(ExecuteQueryEnvelope, envelope)
    operation = cast(DataQueryOperation, skill.operation)
    expected = {binding.column for binding in operation.column_bindings}
    actual = {column.name for column in normalized.schema_.columns}
    if actual != expected:
        raise ApplicationError(
            "MCP_COLUMN_CONTRACT_MISMATCH",
            "MCP schema columns не совпадают с exact skill bindings.",
            502,
        )
    schema = {column.name: set(column.types) for column in normalized.schema_.columns}
    for binding in operation.column_bindings:
        if not schema[binding.column] & set(binding.accepted_mcp_types):
            raise ApplicationError(
                "MCP_COLUMN_TYPE_MISMATCH",
                f"Колонка {binding.column} имеет несовместимый MCP type.",
                502,
            )


def _validate_projected_rows(
    skill: Skill, rows: tuple[dict[str, JsonValue], ...]
) -> bool:
    if not rows:
        return True
    operation = cast(DataQueryOperation, skill.operation)
    definitions = {fact.fact_id: fact for fact in skill.output_contract.facts}
    bindings = {binding.fact_id: binding for binding in operation.column_bindings}
    identities = skill.output_contract.row_identity_fact_ids or ()
    seen_identities: set[bytes] = set()
    for row in rows:
        for fact_id, binding in bindings.items():
            raw = row[binding.column]
            definition = definitions[fact_id]
            if raw is None:
                if not definition.nullable:
                    raise ApplicationError(
                        "RESULT_REQUIRED_FACT_NULL",
                        f"Fact {fact_id} не допускает null.",
                        502,
                    )
                continue
            _convert_value(raw, binding.converter)
        if identities:
            identity = tuple(row[bindings[fact_id].column] for fact_id in identities)
            if all(value is not None for value in identity):
                key = canonicalize(list(identity))
                if key in seen_identities:
                    raise ApplicationError(
                        "RESULT_ROW_IDENTITY_DUPLICATE",
                        "MCP result содержит duplicate row identity.",
                        502,
                    )
                seen_identities.add(key)

    if len(rows) == 1:
        row = rows[0]
        no_identity = not identities or all(
            row[bindings[fact_id].column] is None for fact_id in identities
        )
        sentinel_set = any(
            all(
                row[bindings[fact_id].column] is None and definitions[fact_id].nullable
                for fact_id in required_set
            )
            for required_set in skill.output_contract.sufficiency.required_fact_sets
        )
        if no_identity and sentinel_set:
            return True

    for row in rows:
        if identities and any(
            row[bindings[fact_id].column] is None for fact_id in identities
        ):
            raise ApplicationError(
                "RESULT_ROW_IDENTITY_INVALID",
                "Factual row не содержит полную row identity.",
                502,
            )
    return False


def _validate_result_cardinality(skill: Skill, row_count: int) -> None:
    if skill.output_contract.cardinality == "aggregate" and row_count != 1:
        raise ApplicationError(
            "RESULT_CARDINALITY_MISMATCH",
            "Aggregate skill должен вернуть ровно одну factual row.",
            502,
        )


def _validate_result_sufficiency(skill: Skill, facts: tuple[Fact, ...]) -> None:
    by_row: dict[str, set[str]] = {}
    for fact in facts:
        by_row.setdefault(fact.row_id, set()).add(fact.fact_id)
    required_sets = skill.output_contract.sufficiency.required_fact_sets
    for fact_ids in by_row.values():
        if not any(set(required_set) <= fact_ids for required_set in required_sets):
            raise ApplicationError(
                "RESULT_REQUIRED_FACT_SET_UNSATISFIED",
                "Factual row не удовлетворяет ни одному required_fact_set.",
                502,
            )


def _empty_step(
    step_id: str,
    skill: Skill,
    arguments: dict[str, JsonValue],
    started: datetime,
    finished: datetime,
    attempts: int,
) -> StepResult:
    del arguments
    semantics = skill.output_contract.sufficiency.empty_semantics
    if semantics == "not_applicable":
        raise ApplicationError(
            "RESULT_EMPTY_SEMANTICS_NOT_APPLICABLE",
            "Пустой результат неприменим для этого skill contract.",
            502,
        )
    if semantics == "error_if_empty":
        raise ApplicationError(
            "RESULT_EMPTY_FORBIDDEN",
            "Skill contract запрещает пустой результат.",
            502,
        )
    return StepResult(
        step_id,
        skill,
        Outcome.SUCCESS_EMPTY,
        (),
        (),
        0,
        started,
        finished,
        attempts,
        collection_scope=collection_scope_for_skill(skill),
        empty_reason=("not_found" if semantics == "confirmed_not_found" else "no_rows"),
    )


def _continuation_values(
    operation: DataQueryOperation,
    row: dict[str, JsonValue],
) -> tuple[tuple[JsonValue, ...], dict[str, JsonValue]]:
    bindings = {binding.fact_id: binding for binding in operation.column_bindings}
    pagination = operation.pagination
    if isinstance(pagination, PrefixPagination):
        values = tuple(
            row[bindings[fact_id].column]
            for fact_id in pagination.stable_order_fact_ids
        )
        return values, {}
    if isinstance(pagination, KeysetPagination):
        values = tuple(row[bindings[item.fact_id].column] for item in pagination.sort)
        cursor_values = {
            item.query_parameter: row[bindings[item.fact_id].column]
            for item in pagination.cursor_bindings
        }
        return values, cursor_values
    raise ApplicationError(
        "RESULT_PAGINATION_UNDECLARED",
        "Skill не объявляет continuation sort.",
        502,
    )


def _validate_runtime_arguments(
    skill: Skill,
    arguments: dict[str, ResolvedBinding],
) -> None:
    declared = {parameter.name: parameter for parameter in skill.parameters}
    if set(arguments) - set(declared):
        raise ApplicationError(
            "PLAN_ARGUMENT_UNKNOWN", "Plan передал необъявленный parameter.", 422
        )
    for parameter in skill.parameters:
        if parameter.required and parameter.name not in arguments:
            raise ApplicationError(
                "PLAN_ARGUMENT_MISSING",
                f"Отсутствует required parameter {parameter.name}.",
                422,
            )
        resolved = arguments.get(parameter.name)
        if resolved is None:
            continue
        if resolved.source not in parameter.allowed_sources:
            raise ApplicationError(
                "PLAN_ARGUMENT_SOURCE_FORBIDDEN",
                f"Источник parameter {parameter.name} запрещен skill contract.",
                422,
            )
        if resolved.source == "session_context":
            _validate_context_argument(parameter, resolved)
        if parameter.value_type.value == "entity_ref":
            _validate_entity_argument(parameter, resolved)
        elif parameter.value_type.value == "entity_ref_list":
            values = resolved.value
            if not isinstance(values, list) or len(values) != len(resolved.origins):
                raise ApplicationError(
                    "ENTITY_BINDING_PROVENANCE_MISSING",
                    "Entity list не имеет exact producer provenance.",
                    422,
                )
            for value, origin in zip(values, resolved.origins, strict=True):
                _validate_entity_argument(
                    parameter,
                    ResolvedBinding(value, resolved.source, (origin,)),
                )


def _validate_context_argument(parameter: object, resolved: ResolvedBinding) -> None:
    from chatbot1c.domain.skill import Parameter

    if not isinstance(parameter, Parameter):
        raise TypeError("expected Parameter")
    items = resolved.context_items
    if not items:
        raise ApplicationError(
            "CONTEXT_PROVENANCE_MISSING",
            "Session context parameter не имеет active slot provenance.",
            409,
        )
    slot_keys = set(parameter.context_slot_keys or ())
    if not slot_keys or any(item.slot_key not in slot_keys for item in items):
        raise ApplicationError(
            "CONTEXT_FILTER_CONTRACT_MISMATCH",
            "Context slot key не разрешен consumer parameter.",
            409,
        )
    if any(item.semantic_type != parameter.semantic_type for item in items):
        raise ApplicationError(
            "ENTITY_REF_CONTRACT_MISMATCH",
            "Context semantic type не совпадает с consumer parameter.",
            409,
        )
    expects_many = parameter.value_type is ParameterValueType.ENTITY_REF_LIST
    if expects_many != any(item.cardinality == "many" for item in items):
        raise ApplicationError(
            "ENTITY_REF_CONTRACT_MISMATCH",
            "Context cardinality не совпадает с consumer parameter.",
            409,
        )
    for item in items:
        digest = hashlib.sha256(canonicalize(item.value)).hexdigest()
        if item.value_digest is not None and item.value_digest != digest:
            raise ApplicationError(
                "CONTEXT_PROVENANCE_MISSING",
                "Context canonical value digest поврежден.",
                409,
            )
        if item.policy_mode == "confirmed_filter":
            if item.value_type.value != parameter.value_type.value:
                raise ApplicationError(
                    "CONTEXT_FILTER_CONTRACT_MISMATCH",
                    "Scalar context value_type не совпадает с consumer parameter.",
                    409,
                )
            if parameter.value_type is ParameterValueType.ENUM and str(
                item.value
            ) not in (parameter.allowed_values or ()):
                raise ApplicationError(
                    "CONTEXT_FILTER_CONTRACT_MISMATCH",
                    "Scalar context enum не входит в consumer domain.",
                    409,
                )


def _validate_entity_argument(parameter: object, resolved: ResolvedBinding) -> None:
    from chatbot1c.domain.skill import Parameter

    if not isinstance(parameter, Parameter):
        raise TypeError("expected Parameter")
    try:
        ref = EntityRef.model_validate(resolved.value)
    except ValueError as error:
        raise ApplicationError(
            "ENTITY_REF_INVALID", "Parameter не содержит exact _objectRef.", 422
        ) from error
    if len(resolved.origins) != 1:
        raise ApplicationError(
            "ENTITY_BINDING_PROVENANCE_MISSING",
            "Entity parameter не связан с confirmed producer fact.",
            422,
        )
    origin = resolved.origins[0]
    fact = origin.fact
    if (
        fact.confirmation != "confirmed"
        or fact.semantic_type != parameter.semantic_type
        or fact.semantic_type not in (parameter.entity_types or ())
        or not isinstance(fact.value, EntityRef)
        or fact.value.model_dump(mode="json", by_alias=True)
        != ref.model_dump(mode="json", by_alias=True)
        or ref.object_type not in origin.accepted_mcp_types
    ):
        raise ApplicationError(
            "ENTITY_BINDING_PROVENANCE_MISMATCH",
            (
                "Entity parameter не совпадает с exact producer fact, "
                "entity_types allowlist или column binding."
            ),
            422,
        )


def _validate_bound_entity_identity(
    skill: Skill, arguments: dict[str, JsonValue], facts: tuple[Fact, ...]
) -> None:
    """Enforce only identity equalities explicitly declared by the skill."""

    definitions = {
        definition.fact_id: definition for definition in skill.output_contract.facts
    }
    for constraint in skill.result_constraints:
        if constraint.parameter not in arguments:
            continue
        definition = definitions[constraint.fact_id]
        matching = [fact for fact in facts if fact.fact_id == constraint.fact_id]
        raw_expected = arguments[constraint.parameter]
        expected: tuple[EntityRef, ...]
        if isinstance(constraint, FactEqualsParameterConstraint):
            expected = (EntityRef.model_validate(raw_expected),)
        else:
            if not isinstance(raw_expected, list):
                raise ApplicationError(
                    "ENTITY_REF_BINDING_MISMATCH",
                    "Bound entity membership parameter не является списком exact refs.",
                    502,
                )
            expected = tuple(EntityRef.model_validate(item) for item in raw_expected)
        allowed_identities = {(item.object_type, item.unique_id) for item in expected}
        for fact in matching:
            if (
                fact.semantic_type != definition.semantic_type
                or not isinstance(fact.value, EntityRef)
                or (fact.value.object_type, fact.value.unique_id)
                not in allowed_identities
            ):
                raise ApplicationError(
                    "ENTITY_REF_BINDING_MISMATCH",
                    "Результат содержит business identity вне bound entity parameter.",
                    502,
                )


def _normalize_data_facts(
    trace_id: UUID,
    step_id: str,
    skill: Skill,
    envelope: object,
) -> tuple[Fact, ...]:
    from chatbot1c.application.models import ExecuteQueryEnvelope

    normalized = cast(ExecuteQueryEnvelope, envelope)
    operation = cast(DataQueryOperation, skill.operation)
    definitions = {fact.fact_id: fact for fact in skill.output_contract.facts}
    schema = {column.name: set(column.types) for column in normalized.schema_.columns}
    for binding in operation.column_bindings:
        if not schema.get(binding.column, set()) & set(binding.accepted_mcp_types):
            raise ApplicationError(
                "MCP_COLUMN_TYPE_MISMATCH",
                f"Колонка {binding.column} имеет несовместимый MCP type.",
                502,
            )
    facts: list[Fact] = []
    for row_index, row in enumerate(normalized.data):
        identity = [
            row[
                next(
                    binding.column
                    for binding in operation.column_bindings
                    if binding.fact_id == fact_id
                )
            ]
            for fact_id in (skill.output_contract.row_identity_fact_ids or ())
        ]
        row_key = _sha(
            canonicalize(identity).decode("utf-8")
            if identity
            else f"{step_id}:{row_index}"
        )[:20]
        row_id = f"row_{row_key}"
        row_facts: list[Fact] = []
        for binding in operation.column_bindings:
            definition = definitions[binding.fact_id]
            raw = row[binding.column]
            if raw is None:
                continue
            value = _convert_value(raw, binding.converter)
            if (
                isinstance(value, EntityRef)
                and value.object_type not in binding.accepted_mcp_types
            ):
                raise ApplicationError(
                    "ENTITY_REF_CONTRACT_MISMATCH",
                    "MCP _objectRef type не совпадает с column binding.",
                    502,
                )
            fact_id = uuid5(trace_id, f"{step_id}:{row_id}:{binding.fact_id}")
            row_facts.append(
                Fact(
                    fact_instance_id=fact_id,
                    row_id=row_id,
                    fact_id=binding.fact_id,
                    semantic_type=definition.semantic_type,
                    value_type=definition.value_type,
                    value=value,
                    confirmation="confirmed",
                    step_id=step_id,
                    source_locator=SourceLocator(
                        kind="query_column_binding",
                        reference=binding.column,
                    ),
                    unit=_fact_unit(definition, row, operation),
                    moment=(
                        _parse_datetime(value)
                        if definition.value_type is FactValueType.DATETIME
                        else None
                    ),
                )
            )
        moment = next(
            (fact.moment for fact in row_facts if fact.moment is not None), None
        )
        period = next(
            (fact.value for fact in row_facts if isinstance(fact.value, Period)), None
        )
        for fact in row_facts:
            if fact.moment is None and fact.period is None:
                fact = fact.model_copy(
                    update={
                        "moment": moment,
                        "period": period if isinstance(period, Period) else None,
                    }
                )
            facts.append(fact)
    return tuple(facts)


def _normalize_documentation_facts(
    trace_id: UUID,
    step_id: str,
    skill: Skill,
    chunks: tuple[HelpChunk, ...],
) -> tuple[tuple[Fact, ...], tuple[Citation, ...]]:
    operation = cast(DocumentationRetrievalOperation, skill.operation)
    definitions = {fact.fact_id: fact for fact in skill.output_contract.facts}
    facts: list[Fact] = []
    citations: list[Citation] = []
    for index, chunk in enumerate(chunks):
        citation_id = uuid5(trace_id, f"citation:{step_id}:{chunk.chunk_id}")
        citations.append(
            Citation(
                citation_id=citation_id,
                source_kind="built_in_help",
                corpus_id="ut_11_5_27_56_built_in_help",
                release="11.5.27.56",
                title=chunk.title,
                source_uri=chunk.source_uri,
                relative_path=chunk.relative_path,
                anchor=chunk.anchor,
                chunk_sha256=chunk.chunk_sha256,
            )
        )
        row_id = f"row_{_sha(chunk.chunk_id)[:16]}"
        for binding in operation.output_bindings:
            definition = definitions[binding.fact_id]
            if binding.chunk_field == "text":
                value: FactValue = DocumentFragment(
                    chunk_id=chunk.chunk_id,
                    role=_documentation_role(chunk.role),
                    text=chunk.text,
                )
            elif binding.chunk_field == "citation":
                value = CitationValue(citation_id=citation_id)
            elif binding.chunk_field == "title":
                value = chunk.title
            elif binding.chunk_field == "heading":
                value = chunk.heading
            else:
                value = chunk.role
            facts.append(
                Fact(
                    fact_instance_id=uuid5(
                        trace_id, f"{step_id}:{row_id}:{binding.fact_id}:{index}"
                    ),
                    row_id=row_id,
                    fact_id=binding.fact_id,
                    semantic_type=definition.semantic_type,
                    value_type=definition.value_type,
                    value=value,
                    confirmation="confirmed",
                    step_id=step_id,
                    source_locator=SourceLocator(
                        kind="documentation_chunk", reference=chunk.chunk_id
                    ),
                    unit=UnitNotApplicable(mode="not_applicable"),
                )
            )
    return tuple(facts), tuple(citations)


def _convert_value(value: JsonValue, converter: str) -> FactValue:
    if converter in {"identity", "string", "date", "datetime"}:
        if not isinstance(value, str):
            raise ApplicationError(
                "MCP_VALUE_TYPE_MISMATCH", "Ожидалось строковое MCP значение.", 502
            )
        return value
    if converter == "integer":
        if type(value) is not int:
            raise ApplicationError(
                "MCP_VALUE_TYPE_MISMATCH", "Ожидалось целое MCP значение.", 502
            )
        return value
    if converter == "decimal":
        if type(value) not in {int, float}:
            raise ApplicationError(
                "MCP_VALUE_TYPE_MISMATCH", "Ожидалось числовое MCP значение.", 502
            )
        return float(cast(int | float, value))
    if converter == "boolean":
        if type(value) is not bool:
            raise ApplicationError(
                "MCP_VALUE_TYPE_MISMATCH", "Ожидалось boolean MCP значение.", 502
            )
        return value
    if converter == "object_ref":
        try:
            return EntityRef.model_validate(value)
        except ValueError as error:
            raise ApplicationError(
                "ENTITY_REF_INVALID", "MCP вернул некорректный _objectRef.", 502
            ) from error
    raise ApplicationError("CONVERTER_UNKNOWN", "Неизвестный converter.", 500)


def _fact_unit(
    definition: FactDefinition,
    row: dict[str, JsonValue],
    operation: DataQueryOperation,
) -> UnitNotApplicable | UnitResolved:
    contract = definition.unit_contract
    if isinstance(contract, UnitFixed):
        return UnitResolved(mode="resolved", code=contract.code, label_ru=contract.code)
    if isinstance(contract, UnitFromFact):
        binding = next(
            item
            for item in operation.column_bindings
            if item.fact_id == contract.fact_id
        )
        value = row[binding.column]
        label = str(value)
        return UnitResolved(mode="resolved", code=label[:40], label_ru=label[:80])
    return UnitNotApplicable(mode="not_applicable")


def _encode_parameter(value: JsonValue, encoding: str) -> JsonValue:
    if encoding == "like_contains":
        if not isinstance(value, str):
            raise ApplicationError(
                "PARAMETER_ENCODING_ERROR", "like_contains требует строку.", 422
            )
        escaped = value.replace("~", "~~")
        for special in ("%", "_", "[", "]", "^"):
            escaped = escaped.replace(special, f"~{special}")
        return f"%{escaped}%"
    if encoding in {"period_start", "period_end_exclusive"}:
        period = Period.model_validate(value)
        selected = period.start if encoding == "period_start" else period.end_exclusive
        return selected.isoformat()
    return value


def _runtime_requirement_covered(
    requirement: object, candidates: tuple[Fact, ...]
) -> bool:
    from chatbot1c.domain.plan import FactRequirement

    item = cast(FactRequirement, requirement)
    count = len(candidates)
    cardinality_ok = {
        "one": count == 1,
        "zero_or_one": count <= 1 and (count == 1 or not item.required),
        "many": count > 0,
        "aggregate": count == 1,
    }[item.cardinality]
    if not cardinality_ok:
        return False
    if item.unit_dimension in {"currency", "quantity_unit", "percentage"} and any(
        fact.unit.mode != "resolved" for fact in candidates
    ):
        return False
    if item.time_semantics == "moment" and any(
        fact.moment is None for fact in candidates
    ):
        return False
    if item.time_semantics == "period" and any(
        fact.period is None for fact in candidates
    ):
        return False
    return True


def _derive_resolver_use_proofs(
    plan: PlannerOutput, catalog: PinnedCatalog
) -> dict[str, ResolverUseProof]:
    if not isinstance(plan.result, ExecuteResult):
        return {}
    proofs: dict[str, ResolverUseProof] = {}
    for step in plan.result.steps:
        if not isinstance(step, SkillCall):
            continue
        skill = catalog.skills.get(step.skill_id)
        if skill is None or skill.output_contract.resolution is None:
            continue
        resolution = skill.output_contract.resolution
        uses: list[tuple[Literal["select_one", "select_set"], str]] = []
        for consumer in plan.result.steps:
            if not isinstance(consumer, SkillCall):
                continue
            consumer_skill = catalog.skills.get(consumer.skill_id)
            if consumer_skill is None:
                continue
            parameter_map = {item.name: item for item in consumer_skill.parameters}
            for argument in consumer.arguments:
                binding = argument.binding
                if (
                    isinstance(binding, StepBinding)
                    and binding.step_id == step.step_id
                    and binding.fact_id == resolution.identity_fact_id
                ):
                    parameter = parameter_map.get(argument.parameter)
                    if parameter is None:
                        continue
                    derived_mode: Literal["select_one", "select_set"] = (
                        "select_set"
                        if binding.cardinality == "many"
                        and parameter.value_type is ParameterValueType.ENTITY_REF_LIST
                        else "select_one"
                    )
                    uses.append(
                        (derived_mode, f"{consumer.step_id}.{argument.parameter}")
                    )
        identity_is_final = any(
            output.step_id == step.step_id
            and output.fact_id == resolution.identity_fact_id
            for output in plan.result.final_outputs
        )
        if identity_is_final:
            identity_definition = next(
                item
                for item in skill.output_contract.facts
                if item.fact_id == resolution.identity_fact_id
            )
            for requirement in plan.interpretation.required_facts:
                if (
                    requirement.required
                    and requirement.semantic_type == identity_definition.semantic_type
                    and requirement.cardinality in {"one", "zero_or_one"}
                ):
                    uses.append(("select_one", f"final:{requirement.requirement_id}"))
        modes = {mode for mode, _ in uses}
        if len(modes) > 1:
            raise ApplicationError(
                "PLAN_RESOLVER_MODE_AMBIGUOUS",
                "Один resolver не может одновременно выбирать один объект и множество.",
                422,
            )
        use_mode: Literal["select_one", "select_set", "display_only"] = (
            next(iter(modes)) if modes else "display_only"
        )
        proofs[step.step_id] = ResolverUseProof(
            step_id=step.step_id,
            skill_id=skill.skill_id,
            mode=use_mode,
            identity_fact_id=resolution.identity_fact_id,
            slot_key=resolution.default_slot_key,
            consumer_parameters=tuple(name for _, name in uses),
        )
    return proofs


def _resolver_identity_facts(result: StepResult) -> tuple[Fact, ...]:
    proof = result.resolver_use
    if proof is None:
        return ()
    unique: dict[tuple[str, str, UUID], Fact] = {}
    for fact in result.facts:
        if fact.fact_id != proof.identity_fact_id or not isinstance(
            fact.value, EntityRef
        ):
            continue
        if not _resolver_role_proofs_satisfied(result, fact.row_id):
            continue
        identity = (fact.semantic_type, fact.value.object_type, fact.value.unique_id)
        unique.setdefault(identity, fact)
    return tuple(unique.values())


def _resolver_role_proofs_satisfied(result: StepResult, row_id: str) -> bool:
    skill = result.skill
    if skill is None:
        return False
    resolution = skill.output_contract.resolution
    if resolution is None:
        return False
    definitions = {
        item.fact_id: item for item in skill.output_contract.facts
    }
    for fact_id in resolution.role_proof_fact_ids:
        definition = definitions.get(fact_id)
        matches = [
            fact
            for fact in result.facts
            if fact.row_id == row_id and fact.fact_id == fact_id
        ]
        if definition is None or len(matches) != 1:
            return False
        value = matches[0].value
        if definition.value_type is FactValueType.BOOLEAN:
            if value is not True:
                return False
        elif definition.value_type is FactValueType.ENUM:
            allowed = definition.allowed_values or ()
            if len(allowed) != 1 or value != allowed[0]:
                return False
        else:
            return False
    return True


def _apply_resolver_state(result: StepResult, proof: ResolverUseProof) -> StepResult:
    typed = replace(result, resolver_use=proof)
    identities = _resolver_identity_facts(typed)
    if (
        proof.mode in {"select_one", "select_set"}
        and identities
        and not typed.has_more
        and not typed.truncated
    ):
        typed = replace(typed, collection_scope="complete_set")
    if proof.mode == "select_one" and (
        len(identities) > 1
        or (bool(identities) and (typed.has_more or typed.truncated))
    ):
        # A probe/truncation flag means the visible rows are not the complete
        # candidate universe, even when those rows deduplicate to one identity.
        # Resolver ambiguity is narrowed through its typed pending protocol, not
        # through the ordinary list continuation channel.
        return replace(
            typed,
            outcome=Outcome.CLARIFICATION_REQUIRED,
            continuation=None,
        )
    if proof.mode == "select_set" and identities:
        policy = _selected_policy(typed.skill, proof.identity_fact_id, proof.slot_key)
        # An initial keyset request uses one probe row.  No probe row means the
        # bounded resolver result is complete even though ordinary paged lists
        # retain ``visible_page`` scope.  Only the resolver protocol may promote
        # that result to a selected set.
        complete = typed.collection_scope == "complete_set"
        if policy is None or not complete or len(identities) > policy.max_members:
            return replace(typed, outcome=Outcome.PARTIAL)
    return typed


def _selected_policy(
    skill: Skill | None, fact_id: str, slot_key: str
) -> SelectedOnlyContextPolicy | None:
    if skill is None:
        return None
    return next(
        (
            item
            for item in (skill.output_contract.context_export_policy or ())
            if isinstance(item, SelectedOnlyContextPolicy)
            and item.fact_id == fact_id
            and item.slot_key == slot_key
        ),
        None,
    )


def _selection_proofs(
    results: tuple[StepResult, ...],
    *,
    allowed_step_ids: frozenset[str] | None = None,
) -> tuple[SelectionProof, ...]:
    proofs: list[SelectionProof] = []
    for result in results:
        if allowed_step_ids is not None and result.step_id not in allowed_step_ids:
            continue
        use = result.resolver_use
        if use is None or use.mode == "display_only":
            continue
        identities = _resolver_identity_facts(result)
        policy = _selected_policy(result.skill, use.identity_fact_id, use.slot_key)
        selected = (use.mode == "select_one" and len(identities) == 1) or (
            use.mode == "select_set"
            and bool(identities)
            and policy is not None
            and len(identities) <= policy.max_members
            and result.collection_scope == "complete_set"
            and not result.has_more
            and result.outcome not in {Outcome.PARTIAL, Outcome.CONTRACT_ERROR}
        )
        if use.mode == "select_one" and (
            result.has_more
            or result.truncated
            or result.outcome is Outcome.CLARIFICATION_REQUIRED
        ):
            selected = False
        if not selected:
            continue
        identity_values = tuple(
            EntityIdentity(
                semantic_type=fact.semantic_type,
                physical_type=cast(EntityRef, fact.value).object_type,
                unique_id=cast(EntityRef, fact.value).unique_id,
            )
            for fact in identities
        )
        state: Literal["selected_one", "selected_set"] = (
            "selected_one" if use.mode == "select_one" else "selected_set"
        )
        payload = {
            "resolver": use.model_dump(mode="json"),
            "state": state,
            "fact_instance_ids": [str(fact.fact_instance_id) for fact in identities],
            "identities": [item.model_dump(mode="json") for item in identity_values],
            "complete": True,
        }
        proofs.append(
            SelectionProof(
                resolver=use,
                state=state,
                fact_instance_ids=tuple(fact.fact_instance_id for fact in identities),
                identities=identity_values,
                complete=True,
                proof_digest=hashlib.sha256(canonicalize(payload)).hexdigest(),
            )
        )
    return tuple(proofs)


def _filter_retention_proofs(
    plan: PlannerOutput,
    coverage: PlanCoverageProof,
    results: tuple[StepResult, ...],
) -> tuple[FilterRetentionProof, ...]:
    if not isinstance(plan.result, ExecuteResult):
        return ()
    final_refs = {(item.step_id, item.fact_id) for item in plan.result.final_outputs}
    required_steps = set(coverage.required_steps)
    proofs: list[FilterRetentionProof] = []
    for result in results:
        if result.skill is None or result.step_id not in required_steps:
            continue
        for policy in result.skill.output_contract.context_export_policy or ():
            if not isinstance(policy, ConfirmedFilterContextPolicy):
                continue
            candidates = tuple(
                fact
                for fact in result.facts
                if fact.fact_id == policy.fact_id
                and (fact.step_id, fact.fact_id) in final_refs
            )
            if len(candidates) != 1:
                continue
            fact = candidates[0]
            value_digest = hashlib.sha256(
                canonicalize(_json_value(fact.value))
            ).hexdigest()
            payload = {
                "step_id": fact.step_id,
                "fact_instance_id": str(fact.fact_instance_id),
                "fact_id": fact.fact_id,
                "slot_key": policy.slot_key,
                "semantic_type": policy.semantic_type,
                "value_type": policy.value_type,
                "canonical_value_digest": value_digest,
            }
            proofs.append(
                FilterRetentionProof(
                    step_id=fact.step_id,
                    fact_instance_id=fact.fact_instance_id,
                    fact_id=fact.fact_id,
                    slot_key=policy.slot_key,
                    semantic_type=policy.semantic_type,
                    value_type=policy.value_type,
                    canonical_value_digest=value_digest,
                    proof_digest=hashlib.sha256(canonicalize(payload)).hexdigest(),
                )
            )
    return tuple(proofs)


def _context_exports(
    selection_proofs: tuple[SelectionProof, ...],
    filter_proofs: tuple[FilterRetentionProof, ...],
    facts: tuple[Fact, ...],
) -> tuple[ContextExport, ...]:
    fact_index = {fact.fact_instance_id: fact for fact in facts}
    exports: list[ContextExport] = []
    for proof in selection_proofs:
        handle = f"ctx_{secrets.token_urlsafe(24)}"
        for fact_instance_id in proof.fact_instance_ids:
            fact = fact_index[fact_instance_id]
            exports.append(
                ContextExport(
                    context_handle=handle,
                    fact_instance_id=fact_instance_id,
                    semantic_type=fact.semantic_type,
                )
            )
    for filter_proof in filter_proofs:
        fact = fact_index[filter_proof.fact_instance_id]
        exports.append(
            ContextExport(
                context_handle=f"ctx_{secrets.token_urlsafe(24)}",
                fact_instance_id=fact.fact_instance_id,
                semantic_type=fact.semantic_type,
            )
        )
    return tuple(exports)


def _evidence_pagination(
    results: tuple[StepResult, ...], default_page_size: int
) -> Pagination | None:
    if any(
        result.resolver_use is not None
        and result.outcome is Outcome.CLARIFICATION_REQUIRED
        for result in results
    ):
        return None
    paged = next(
        (
            result
            for result in reversed(results)
            if result.skill is not None
            and isinstance(result.skill.operation, DataQueryOperation)
            and result.skill.operation.pagination.strategy != "none"
            and not (
                result.resolver_use is not None
                and result.resolver_use.mode == "select_one"
                and result.outcome is Outcome.CLARIFICATION_REQUIRED
            )
        ),
        None,
    )
    if paged is None:
        return None
    return Pagination(
        shown=paged.row_count,
        page_size=(
            paged.continuation.page_size
            if paged.continuation is not None
            else default_page_size
        ),
        has_more=paged.has_more,
        continuation_handle=None,
    )


def _context_facts(
    evidence: EvidenceBundle,
    turn_id: UUID,
    results: tuple[StepResult, ...],
    selection_proofs: tuple[SelectionProof, ...],
    filter_proofs: tuple[FilterRetentionProof, ...],
) -> tuple[ContextFact, ...]:
    facts = {fact.fact_instance_id: fact for fact in evidence.facts}
    result_by_step = {result.step_id: result for result in results}
    selection_by_fact = {
        fact_id: proof
        for proof in selection_proofs
        for fact_id in proof.fact_instance_ids
    }
    filter_by_fact = {proof.fact_instance_id: proof for proof in filter_proofs}
    member_counts = {
        proof.proof_digest: len(proof.fact_instance_ids) for proof in selection_proofs
    }
    member_indexes = {
        fact_id: index
        for proof in selection_proofs
        for index, fact_id in enumerate(proof.fact_instance_ids)
    }
    context_items: list[ContextFact] = []
    for exported in evidence.context_exports:
        fact = facts[exported.fact_instance_id]
        producer_result = result_by_step.get(fact.step_id)
        if producer_result is None or producer_result.skill is None:
            raise ApplicationError(
                "ENTITY_BINDING_PROVENANCE_MISSING",
                "Context export не имеет producer skill.",
                500,
            )
        selection = selection_by_fact.get(fact.fact_instance_id)
        retained = filter_by_fact.get(fact.fact_instance_id)
        if selection is not None and isinstance(fact.value, EntityRef):
            policy = _selected_policy(
                producer_result.skill,
                fact.fact_id,
                selection.resolver.slot_key,
            )
            if policy is None:
                raise ApplicationError(
                    "CONTEXT_EXPORT_POLICY_INVALID",
                    "Selection proof не имеет matching portable policy.",
                    500,
                )
            expires_at = _context_expiry(policy, producer_result.facts)
            context_items.append(
                ContextFact(
                    handle=exported.context_handle,
                    semantic_type=exported.semantic_type,
                    value=cast(
                        JsonValue,
                        fact.value.model_dump(mode="json", by_alias=True),
                    ),
                    presentation=fact.value.presentation,
                    origin_turn_id=turn_id,
                    origin_fact_instance_id=fact.fact_instance_id,
                    origin=_entity_fact_origin(fact, producer_result.skill),
                    slot_key=selection.resolver.slot_key,
                    value_type=FactValueType.ENTITY_REF,
                    policy_mode="selected_only",
                    cardinality=(
                        "many"
                        if member_counts[selection.proof_digest] > 1
                        or selection.state == "selected_set"
                        else "one"
                    ),
                    member_index=member_indexes[fact.fact_instance_id],
                    value_digest=hashlib.sha256(
                        canonicalize(fact.value.model_dump(mode="json", by_alias=True))
                    ).hexdigest(),
                    lifetime_mode=policy.lifetime.mode,
                    expires_at=expires_at,
                    proof_digest=selection.proof_digest,
                )
            )
            continue
        if retained is not None:
            filter_policy = next(
                (
                    item
                    for item in (
                        producer_result.skill.output_contract.context_export_policy
                        or ()
                    )
                    if isinstance(item, ConfirmedFilterContextPolicy)
                    and item.fact_id == retained.fact_id
                    and item.slot_key == retained.slot_key
                ),
                None,
            )
            if filter_policy is None:
                raise ApplicationError(
                    "CONTEXT_FILTER_CONTRACT_INVALID",
                    "Filter proof не имеет matching portable policy.",
                    500,
                )
            value = _json_value(fact.value)
            context_items.append(
                ContextFact(
                    handle=exported.context_handle,
                    semantic_type=exported.semantic_type,
                    value=value,
                    presentation=f"Сохраненный фильтр: {fact.semantic_type}",
                    origin_turn_id=turn_id,
                    origin_fact_instance_id=fact.fact_instance_id,
                    origin=_scalar_fact_origin(fact, producer_result.skill),
                    slot_key=retained.slot_key,
                    value_type=fact.value_type,
                    policy_mode="confirmed_filter",
                    cardinality="one",
                    member_index=0,
                    value_digest=retained.canonical_value_digest,
                    lifetime_mode=filter_policy.lifetime.mode,
                    expires_at=_context_expiry(filter_policy, producer_result.facts),
                    proof_digest=retained.proof_digest,
                )
            )
            continue
        raise ApplicationError(
            "CONTEXT_EXPORT_NOT_SELECTED",
            "Context export не подтвержден selection/filter proof.",
            500,
        )
    return tuple(context_items)


def _context_expiry(
    policy: SelectedOnlyContextPolicy | ConfirmedFilterContextPolicy,
    facts: tuple[Fact, ...],
) -> datetime | None:
    if policy.lifetime.mode != "until":
        return None
    matches = [
        fact for fact in facts if fact.fact_id == policy.lifetime.expires_at_fact_id
    ]
    if len(matches) != 1:
        raise ApplicationError(
            "CONTEXT_EXPORT_POLICY_INVALID",
            "until lifetime не имеет exact expiry fact.",
            500,
        )
    return _parse_datetime(matches[0].value)


def _scalar_fact_origin(fact: Fact, skill: Skill) -> ScalarFactOrigin:
    definition = next(
        (item for item in skill.output_contract.facts if item.fact_id == fact.fact_id),
        None,
    )
    if (
        definition is None
        or definition.semantic_type != fact.semantic_type
        or definition.value_type is not fact.value_type
        or fact.source_locator.kind
        not in {"query_column_binding", "operator_result", "system_value"}
    ):
        raise ApplicationError(
            "CONTEXT_FILTER_CONTRACT_MISMATCH",
            "Scalar fact не совпадает с producer contract.",
            500,
        )
    return ScalarFactOrigin(
        fact=fact,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        skill_digest=skill.integrity.digest,
        source_kind=cast(
            Literal["query_column_binding", "operator_result", "system_value"],
            fact.source_locator.kind,
        ),
        source_reference=fact.source_locator.reference,
        allowed_values=definition.allowed_values,
    )


def _pending_from_resolver(
    plan: PlannerOutput,
    context: ExecutionContext,
    results: tuple[StepResult, ...],
) -> PendingClarificationDraft | None:
    ambiguous = next(
        (
            result
            for result in results
            if result.resolver_use is not None
            and result.resolver_use.mode == "select_one"
            and result.outcome is Outcome.CLARIFICATION_REQUIRED
        ),
        None,
    )
    if ambiguous is None or ambiguous.skill is None:
        return None
    use = cast(ResolverUseProof, ambiguous.resolver_use)
    resolution = ambiguous.skill.output_contract.resolution
    if resolution is None:
        return None
    identity_facts = _resolver_identity_facts(ambiguous)
    must_narrow = ambiguous.has_more or ambiguous.truncated or len(identity_facts) > 5
    choices: list[PendingChoice] = []
    if not must_narrow:
        by_row: dict[str, tuple[Fact, ...]] = {}
        for fact in ambiguous.facts:
            by_row[fact.row_id] = (*by_row.get(fact.row_id, ()), fact)
        prepared: list[tuple[tuple[Fact, ...], str]] = []
        for identity in identity_facts:
            row = by_row.get(identity.row_id, (identity,))
            labels = [
                str(fact.value)
                for fact in row
                if fact.fact_id in resolution.candidate_label_fact_ids
                and fact.value not in {None, ""}
            ]
            label = (
                " · ".join(labels) or cast(EntityRef, identity.value).presentation
            )[:160]
            prepared.append((row, label))
        rendered_labels = [label for _, label in prepared]
        must_narrow = len(set(rendered_labels)) != len(rendered_labels)
        if not must_narrow:
            for index, (row, label) in enumerate(prepared, 1):
                choices.append(
                    PendingChoice(
                        choice_id=f"c{index}",
                        label_ru=label,
                        binding={
                            "source": "step",
                            "step_id": use.step_id,
                            "fact_id": use.identity_fact_id,
                        },
                        facts=row,
                    )
                )
    return PendingClarificationDraft(
        kind="resolver_choice",
        question_ru=(
            "Найдено несколько подходящих вариантов. Уточните критерий поиска."
            if must_narrow
            else "Выберите один из найденных вариантов."
        ),
        original_question=plan.interpretation.goal_ru,
        plan_json=plan.model_dump_json(by_alias=True),
        resolver_step_id=use.step_id,
        choices=tuple(choices),
        has_more_candidates=must_narrow,
        catalog_snapshot_id=context.catalog.snapshot_id,
        catalog_revision=context.catalog.revision,
        database_marker=context.database_state_marker.digest,
    )


def _entity_fact_origin(fact: Fact, skill: Skill) -> EntityFactOrigin:
    if not isinstance(fact.value, EntityRef) or not isinstance(
        skill.operation, DataQueryOperation
    ):
        raise ApplicationError(
            "ENTITY_BINDING_PROVENANCE_MISSING",
            "Entity fact не имеет data-query producer.",
            500,
        )
    definitions = {
        definition.fact_id: definition for definition in skill.output_contract.facts
    }
    definition = definitions.get(fact.fact_id)
    bindings = [
        binding
        for binding in skill.operation.column_bindings
        if binding.fact_id == fact.fact_id
        and binding.column == fact.source_locator.reference
    ]
    if (
        definition is None
        or definition.semantic_type != fact.semantic_type
        or definition.value_type is not fact.value_type
        or fact.source_locator.kind != "query_column_binding"
        or len(bindings) != 1
        or bindings[0].converter != "object_ref"
        or fact.value.object_type not in bindings[0].accepted_mcp_types
    ):
        raise ApplicationError(
            "ENTITY_BINDING_PROVENANCE_MISMATCH",
            "Entity fact не совпадает с producer output/column contract.",
            500,
        )
    binding = bindings[0]
    return EntityFactOrigin(
        fact=fact,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        skill_digest=skill.integrity.digest,
        column=binding.column,
        accepted_mcp_types=binding.accepted_mcp_types,
    )


def _step_evidence(result: StepResult) -> StepEvidence:
    return StepEvidence(
        step_id=result.step_id,
        source_kind=(
            "deterministic_operator"
            if result.skill is None
            else (
                "mcp_data"
                if isinstance(result.skill.operation, DataQueryOperation)
                else "documentation_index"
            )
        ),
        operation_ref=(
            (
                "operator:" + result.facts[0].source_locator.reference
                if result.facts
                and result.facts[0].source_locator.kind == "operator_result"
                else "operator:unknown"
            )
            if result.skill is None
            else f"skill://{result.skill.skill_id}/{result.skill.version}"
        ),
        started_at=result.started_at,
        finished_at=result.finished_at,
        attempts=result.attempts,
        status=result.outcome,
        row_count=result.row_count,
        truncated=result.truncated,
        has_more=result.has_more,
        produced_fact_instance_ids=tuple(
            fact.fact_instance_id for fact in result.facts
        ),
        error_ids=() if result.error is None else (result.error.error_id,),
        collection_scope=result.collection_scope,
    )


def _failed_step(
    step_id: str,
    skill: Skill,
    outcome: Outcome,
    error: ApplicationError,
    started: datetime,
) -> StepResult:
    evidence_error = _evidence_error(
        error.code,
        "execution",
        "mcp" if error.code == "MCP_UNAVAILABLE" else "none",
        error.code == "MCP_UNAVAILABLE",
        error.message_ru,
        step_id,
    )
    return StepResult(
        step_id,
        skill,
        outcome,
        (),
        (),
        0,
        started,
        datetime.now(UTC),
        0,
        evidence_error,
        collection_scope=collection_scope_for_skill(skill),
    )


def _evidence_error(
    code: str,
    stage: Literal[
        "request",
        "planning",
        "coverage",
        "execution",
        "evidence_validation",
        "answering",
    ],
    dependency: Literal[
        "none", "deepseek", "mcp", "documentation_index", "skill_catalog", "database"
    ],
    retryable: bool,
    message: str,
    seed: str,
) -> EvidenceError:
    return EvidenceError(
        error_id=uuid5(NAMESPACE_URL, f"{seed}:{code}:{message}"),
        code=code,
        stage=stage,
        dependency=dependency,
        retryable=retryable,
        public_message_ru=message,
        diagnostic_ref=f"diag_{_sha(seed + code)[:16]}",
    )


def _runtime_step_dependencies(step: PlanStep) -> set[str]:
    dependencies: set[str] = set()
    if isinstance(step, SkillCall):
        dependencies.update(
            argument.binding.step_id
            for argument in step.arguments
            if isinstance(argument.binding, StepBinding)
        )
    elif isinstance(step, NormalizePeriodOperator):
        if isinstance(step.expression, StepBinding):
            dependencies.add(step.expression.step_id)
    elif isinstance(step, CountOperator):
        dependencies.add(step.input_step_id)
    return dependencies


def _overall_outcome(
    results: list[StepResult],
    plan: PlannerOutput,
    coverage_proof: PlanCoverageProof,
) -> Outcome:
    required_results = [
        result for result in results if result.criticality == "required"
    ]
    outcomes = [result.outcome for result in required_results]
    if Outcome.CONTRACT_ERROR in outcomes:
        return Outcome.CONTRACT_ERROR
    if Outcome.CLARIFICATION_REQUIRED in outcomes:
        return Outcome.CLARIFICATION_REQUIRED
    if plan.interpretation.intent_kind == "documentation":
        return (
            Outcome.DOCUMENTATION_FOUND
            if any(result.facts for result in required_results)
            else Outcome.DOCUMENTATION_EMPTY
        )
    produced_required_refs = {
        (fact.step_id, fact.fact_id)
        for result in required_results
        for fact in result.facts
        if (fact.step_id, fact.fact_id) in coverage_proof.required_final_refs
    }
    has_final_facts = bool(produced_required_refs)
    if coverage_proof.required_final_refs <= produced_required_refs:
        return combine_step_outcomes(outcomes, has_facts=has_final_facts)
    if has_final_facts:
        return Outcome.PARTIAL
    for result in required_results:
        if result.outcome in {
            Outcome.QUERY_ERROR,
            Outcome.MCP_UNAVAILABLE,
            Outcome.LLM_UNAVAILABLE,
        }:
            return result.outcome
        if result.outcome in {
            Outcome.SUCCESS_EMPTY,
            Outcome.DOCUMENTATION_EMPTY,
        }:
            return Outcome.SUCCESS_EMPTY
    return combine_step_outcomes(outcomes, has_facts=False)


def _context_commit_allowed(
    results: tuple[StepResult, ...], outcome: Outcome
) -> bool:
    if outcome is Outcome.CONTRACT_ERROR:
        return False
    technical_failures = {
        Outcome.QUERY_ERROR,
        Outcome.MCP_UNAVAILABLE,
        Outcome.CONTRACT_ERROR,
    }
    return not any(
        result.criticality == "required" and result.outcome in technical_failures
        for result in results
    )


def _finalize_coverage_outcome(
    plan: PlannerOutput,
    coverage_proof: PlanCoverageProof,
    outcome: Outcome,
    evidence: EvidenceBundle,
) -> tuple[Outcome, EvidenceBundle]:
    finalized_outcome = outcome
    if (
        not evidence.coverage.sufficient
        and evidence.facts
        and outcome
        in {
            Outcome.SUCCESS_WITH_ROWS,
            Outcome.ZERO_AGGREGATE,
            Outcome.DOCUMENTATION_FOUND,
        }
    ):
        finalized_outcome = Outcome.PARTIAL
        evidence = evidence.model_copy(update={"outcome": finalized_outcome})
    validate_evidence_against_plan(plan, coverage_proof, evidence)
    return finalized_outcome, evidence


def _empty_reason_for_outcome(
    results: tuple[StepResult, ...], outcome: Outcome
) -> Literal["not_found", "no_rows"] | None:
    if outcome is not Outcome.SUCCESS_EMPTY:
        return None
    reasons = {
        result.empty_reason
        for result in results
        if result.criticality == "required" and result.empty_reason is not None
    }
    if len(reasons) > 1:
        raise ApplicationError(
            "EMPTY_REASON_CONFLICT",
            "Required empty producers объявили несовместимые stable reasons.",
            502,
        )
    if not reasons:
        raise ApplicationError(
            "EMPTY_REASON_MISSING",
            "Required success_empty producer не объявил stable reason.",
            502,
        )
    return reasons.pop()


def _documentation_role(
    value: str,
) -> Literal[
    "definition",
    "procedure",
    "prerequisite",
    "restriction",
    "error_cause",
    "verification_action",
    "status_meaning",
    "navigation",
]:
    allowed = {
        "definition",
        "procedure",
        "prerequisite",
        "restriction",
        "error_cause",
        "verification_action",
        "status_meaning",
        "navigation",
    }
    return cast(
        Literal[
            "definition",
            "procedure",
            "prerequisite",
            "restriction",
            "error_cause",
            "verification_action",
            "status_meaning",
            "navigation",
        ],
        value if value in allowed else "definition",
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime fact must contain ISO text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("datetime fact must contain timezone")
    return parsed


def _json_value(value: object) -> JsonValue:
    if isinstance(value, EntityRef):
        return cast(JsonValue, value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Period):
        return cast(JsonValue, value.model_dump(mode="json"))
    return cast(JsonValue, value)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
