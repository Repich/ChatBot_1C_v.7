"""Validated plan execution, fact normalization and evidence construction."""

from __future__ import annotations

import hashlib
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
    PinnedCatalog,
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
    EvidenceBundle,
    EvidenceError,
    Fact,
    FactValue,
    Pagination,
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
    DataQueryOperation,
    DocumentationRetrievalOperation,
    FactDefinition,
    FactEqualsParameterConstraint,
    FactValueType,
    KeysetPagination,
    PrefixPagination,
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


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    value: JsonValue
    source: Literal["user_slot", "session_context", "previous_step", "system"]
    origins: tuple[EntityFactOrigin, ...] = ()


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
        self._configuration_profile_digest = (
            configuration_profile_digest or _sha("ut-11.5.27.56")
        )

    async def execute(
        self, plan: PlannerOutput, context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(plan.result, ExecuteResult):
            raise ValueError("PlanExecutor accepts execute decisions only")
        coverage_proof = build_plan_coverage_proof(
            plan, tuple(context.catalog.skills.values())
        )
        context_index = {fact.handle: fact for fact in context.context_facts}
        step_results: dict[str, StepResult] = {}
        ordered: list[StepResult] = []
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
        evidence = self._build_evidence(
            plan, coverage_proof, context, tuple(ordered), outcome
        )
        outcome, evidence = _finalize_coverage_outcome(
            plan, coverage_proof, outcome, evidence
        )
        exported_context = (
            ()
            if outcome is Outcome.CONTRACT_ERROR
            else _context_facts(evidence, context.turn_id, tuple(ordered))
        )
        continuations = [
            result.continuation
            for result in ordered
            if result.continuation is not None
        ]
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
            plan, coverage_proof, context, (result,), outcome
        )
        outcome, evidence = _finalize_coverage_outcome(
            plan, coverage_proof, outcome, evidence
        )
        exported_context = (
            ()
            if outcome is Outcome.CONTRACT_ERROR
            else _context_facts(evidence, context.turn_id, (result,))
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
        context_index: dict[str, ContextFact],
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
        context_index: dict[str, ContextFact],
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
                    item
                    for item in skill.parameters
                    if item.name == binding.parameter
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
        has_more = has_probe_row or (
            (envelope.has_more or envelope.truncated)
            and len(page_rows) >= page.page_size
        )
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
        context_index: dict[str, ContextFact],
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
        context_index: dict[str, ContextFact],
        previous: dict[str, StepResult],
    ) -> ResolvedBinding:
        if isinstance(binding, LiteralBinding):
            return ResolvedBinding(
                cast(JsonValue, binding.model_dump(mode="json")["value"]),
                "user_slot",
            )
        if isinstance(binding, ContextBinding):
            context_fact = context_index.get(binding.context_handle)
            if context_fact is None:
                raise ApplicationError(
                    "PLAN_CONTEXT_MISSING",
                    f"Context handle {binding.context_handle} отсутствует в сессии.",
                    409,
                )
            if context_fact.semantic_type != binding.expected_semantic_type:
                raise ApplicationError(
                    "PLAN_CONTEXT_TYPE_MISMATCH",
                    "Context handle имеет другой semantic type.",
                    409,
                )
            return ResolvedBinding(
                context_fact.value,
                "session_context",
                (context_fact.origin,),
            )
        if isinstance(binding, StepBinding):
            result = previous.get(binding.step_id)
            if result is None:
                raise ApplicationError(
                    "PLAN_STEP_BINDING_MISSING",
                    f"Previous step {binding.step_id} не выполнен.",
                    422,
                )
            facts = [
                fact for fact in result.facts if fact.fact_id == binding.fact_id
            ]
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
                (item for item in plan.interpretation.slots if item.slot_id == binding.slot_id),
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
    ) -> EvidenceBundle:
        facts = tuple(fact for result in results for fact in result.facts)
        citations = tuple(citation for result in results for citation in result.citations)
        errors = tuple(
            result.error for result in results if result.error is not None
        )
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
        exports = () if outcome is Outcome.CONTRACT_ERROR else _context_exports(facts)
        source_boundary = cast(
            Literal["data", "documentation", "mixed", "none"],
            (
                plan.interpretation.intent_kind
                if plan.interpretation.intent_kind
                in {"data", "documentation", "mixed"}
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
        return _PageRequest(
            "prefix", page_size, request_limit, shown, shown, {}
        )
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
        expected = {
            binding.query_parameter for binding in pagination.cursor_bindings
        }
        if set(continuation.cursor_values) != expected:
            raise ApplicationError(
                "CONTINUATION_PLAN_INVALID",
                "Сохраненные cursor bindings не совпадают с skill contract.",
                409,
            )
        query_params.update(dict(continuation.cursor_values))
        shown = continuation.shown
    return _PageRequest(
        "keyset", page_size, page_size + 1, 0, shown, query_params
    )


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
                row[bindings[fact_id].column] is None
                and definitions[fact_id].nullable
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
        empty_reason=(
            "not_found" if semantics == "confirmed_not_found" else "no_rows"
        ),
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
        values = tuple(
            row[bindings[item.fact_id].column] for item in pagination.sort
        )
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
        allowed_identities = {
            (item.object_type, item.unique_id) for item in expected
        }
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
            if isinstance(value, EntityRef) and value.object_type not in binding.accepted_mcp_types:
                raise ApplicationError(
                    "ENTITY_REF_TYPE_MISMATCH",
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
        moment = next((fact.moment for fact in row_facts if fact.moment is not None), None)
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
            item for item in operation.column_bindings if item.fact_id == contract.fact_id
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


def _runtime_requirement_covered(requirement: object, candidates: tuple[Fact, ...]) -> bool:
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


def _context_exports(facts: tuple[Fact, ...]) -> tuple[ContextExport, ...]:
    unique: dict[tuple[str, str, UUID], Fact] = {}
    for fact in facts:
        if fact.value_type is FactValueType.ENTITY_REF and isinstance(fact.value, EntityRef):
            unique[(fact.semantic_type, fact.value.object_type, fact.value.unique_id)] = fact
    return tuple(
        ContextExport(
            context_handle=f"ctx_{fact.fact_instance_id.hex[:24]}",
            fact_instance_id=fact.fact_instance_id,
            semantic_type=fact.semantic_type,
        )
        for fact in unique.values()
    )


def _evidence_pagination(
    results: tuple[StepResult, ...], default_page_size: int
) -> Pagination | None:
    paged = next(
        (
            result
            for result in reversed(results)
            if result.skill is not None
            and isinstance(result.skill.operation, DataQueryOperation)
            and result.skill.operation.pagination.strategy != "none"
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
) -> tuple[ContextFact, ...]:
    facts = {fact.fact_instance_id: fact for fact in evidence.facts}
    result_by_step = {result.step_id: result for result in results}
    context_items: list[ContextFact] = []
    for exported in evidence.context_exports:
        fact = facts[exported.fact_instance_id]
        if not isinstance(fact.value, EntityRef):
            continue
        producer_result = result_by_step.get(fact.step_id)
        if producer_result is None or producer_result.skill is None:
            raise ApplicationError(
                "ENTITY_BINDING_PROVENANCE_MISSING",
                "Context export не имеет producer skill.",
                500,
            )
        origin = _entity_fact_origin(fact, producer_result.skill)
        context_items.append(
            ContextFact(
                handle=exported.context_handle,
                semantic_type=exported.semantic_type,
                value=cast(JsonValue, fact.value.model_dump(mode="json", by_alias=True)),
                presentation=fact.value.presentation,
                origin_turn_id=turn_id,
                origin_fact_instance_id=fact.fact_instance_id,
                origin=origin,
            )
        )
    return tuple(context_items)


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
        "request", "planning", "coverage", "execution", "evidence_validation", "answering"
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
