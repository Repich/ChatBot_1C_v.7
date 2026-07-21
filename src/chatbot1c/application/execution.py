"""Validated plan execution, fact normalization and evidence construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
    PinnedCatalog,
)
from chatbot1c.application.operators import normalize_period
from chatbot1c.application.ports import (
    DocumentationPort,
    ReadOnly1CPort,
    TraceRepository,
)
from chatbot1c.application.trace_paths import step_trace_prefix
from chatbot1c.contracts.digest import canonicalize
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
    ExecuteResult,
    LiteralBinding,
    NormalizePeriodOperator,
    PlannerOutput,
    SkillCall,
    SlotBinding,
    StepBinding,
    SystemBinding,
)
from chatbot1c.domain.skill import (
    DataQueryOperation,
    DocumentationRetrievalOperation,
    FactDefinition,
    FactValueType,
    Skill,
    UnitFixed,
    UnitFromFact,
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


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    outcome: Outcome
    evidence: EvidenceBundle
    context_facts: tuple[ContextFact, ...]
    steps: tuple[StepResult, ...]


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    value: JsonValue
    source: Literal["user_slot", "session_context", "previous_step", "system"]
    origins: tuple[EntityFactOrigin, ...] = ()


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
        context_index = {fact.handle: fact for fact in context.context_facts}
        step_results: dict[str, StepResult] = {}
        ordered: list[StepResult] = []
        for step in plan.result.steps:
            if isinstance(step, SkillCall):
                skill = context.catalog.skills.get(step.skill_id)
                if skill is None or skill.version != step.skill_version:
                    raise ApplicationError(
                        "PLAN_SKILL_MISSING",
                        f"Pinned skill {step.skill_id}@{step.skill_version} отсутствует.",
                        409,
                    )
                result = await self._execute_skill(
                    plan,
                    step,
                    skill,
                    context,
                    context_index,
                    step_results,
                )
            elif isinstance(step, NormalizePeriodOperator):
                result = self._execute_normalize_period(
                    plan, step, context, context_index, step_results
                )
            else:
                raise ApplicationError(
                    "OPERATOR_NOT_IMPLEMENTED",
                    f"Оператор {step.operator} еще не входит в slice 1.",
                    422,
                )
            step_results[step.step_id] = result
            ordered.append(result)
            if result.outcome in {
                Outcome.QUERY_ERROR,
                Outcome.MCP_UNAVAILABLE,
                Outcome.CONTRACT_ERROR,
                Outcome.CLARIFICATION_REQUIRED,
            }:
                break
            if (
                result.outcome in {Outcome.SUCCESS_EMPTY, Outcome.DOCUMENTATION_EMPTY}
                and isinstance(step, SkillCall)
                and step.on_empty == "stop_not_found"
            ):
                break

        outcome = _overall_outcome(ordered, plan.interpretation.intent_kind)
        evidence = self._build_evidence(plan, context, tuple(ordered), outcome)
        exported_context = _context_facts(evidence, context.turn_id, tuple(ordered))
        return ExecutionResult(outcome, evidence, exported_context, tuple(ordered))

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
            if error.code in {
                "MCP_ENVELOPE_INVALID",
                "MCP_RESPONSE_TOO_LARGE",
                "MCP_TOOL_RESULT_INVALID",
            }:
                raise
            outcome = (
                Outcome.MCP_UNAVAILABLE
                if error.code == "MCP_UNAVAILABLE"
                else Outcome.CONTRACT_ERROR
            )
            return _failed_step(call.step_id, skill, outcome, error, started)

    async def _execute_data(
        self,
        call: SkillCall,
        skill: Skill,
        arguments: dict[str, JsonValue],
        context: ExecutionContext,
        started: datetime,
    ) -> StepResult:
        operation = cast(DataQueryOperation, skill.operation)
        params: dict[str, JsonValue] = {}
        for binding in operation.parameter_bindings:
            if binding.parameter not in arguments:
                continue
            params[binding.query_parameter] = _encode_parameter(
                arguments[binding.parameter], binding.encoding
            )
        limit = operation.query_template.mcp_limit.default
        for value in arguments.values():
            pagination_limit = value.get("limit") if isinstance(value, dict) else None
            if type(pagination_limit) is int:
                limit = min(
                    pagination_limit, operation.query_template.mcp_limit.maximum
                )
        request = ExecuteQueryRequest(
            query=operation.query_template.text,
            params=params,
            limit=limit,
            include_schema=True,
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
                1,
                error=error,
            )
        if envelope.count == 0:
            return StepResult(
                call.step_id,
                skill,
                Outcome.SUCCESS_EMPTY,
                (),
                (),
                0,
                started,
                finished,
                1,
            )
        if skill.output_contract.cardinality in {"exactly_one", "zero_or_one"} and envelope.count > 1:
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
                envelope.count,
                started,
                finished,
                1,
                error=error,
            )
        facts = _normalize_data_facts(context.trace_id, call.step_id, skill, envelope)
        _validate_bound_entity_identity(skill, arguments, facts)
        zero = skill.output_contract.cardinality == "aggregate" and any(
            fact.fact_id in skill.output_contract.sufficiency.zero_fact_ids
            and type(fact.value) in {int, float}
            and fact.value == 0
            for fact in facts
        )
        return StepResult(
            call.step_id,
            skill,
            Outcome.ZERO_AGGREGATE if zero else Outcome.SUCCESS_WITH_ROWS,
            facts,
            (),
            envelope.count,
            started,
            finished,
            1,
            truncated=envelope.truncated,
            has_more=envelope.has_more,
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
                return ResolvedBinding(
                    _sha(str(context.catalog.snapshot_id)), "system"
                )
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
        for requirement in plan.interpretation.required_facts:
            candidates = tuple(
                fact
                for fact in facts
                if fact.semantic_type == requirement.semantic_type
                and (fact.step_id, fact.fact_id) in final_refs
            )
            covered = _runtime_requirement_covered(requirement, candidates)
            requirements.append(
                CoverageRequirement(
                    requirement_id=requirement.requirement_id,
                    semantic_type=requirement.semantic_type,
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
        sufficient = all(
            requirement.status is CoverageStatus.COVERED
            for requirement in requirements
            if next(
                item.required
                for item in plan.interpretation.required_facts
                if item.requirement_id == requirement.requirement_id
            )
        ) and outcome in {
            Outcome.SUCCESS_WITH_ROWS,
            Outcome.ZERO_AGGREGATE,
            Outcome.DOCUMENTATION_FOUND,
        }
        exports = _context_exports(facts)
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
            schema_version="1.0.0",
            document_type="evidence_bundle",
            trace_id=context.trace_id,
            request_id=context.request_id,
            session_id=context.session_id,
            created_at=datetime.now(UTC),
            source_boundary=source_boundary,
            outcome=outcome,
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
            database_state_marker=self._marker(context.catalog),
            steps=tuple(_step_evidence(result) for result in results),
            facts=facts,
            citations=citations,
            documentation_disagreements=(),
            coverage=Coverage(sufficient=sufficient, requirements=tuple(requirements)),
            pagination=(
                Pagination(
                    shown=sum(result.row_count for result in results),
                    page_size=context.default_list_limit,
                    has_more=False,
                )
                if any(result.row_count for result in results)
                else None
            ),
            context_exports=exports,
            errors=errors,
        )
        return evidence

    def _marker(self, catalog: PinnedCatalog) -> DatabaseStateMarker:
        projection_digest = _sha("[]")
        components = {
            "configuration_revision": "11.5.27.56",
            "configuration_profile_digest": self._configuration_profile_digest,
            "catalog_revision": catalog.revision,
            "catalog_snapshot_digest": catalog.digest,
            "documentation_revision": self._documentation_revision,
            "documentation_manifest_digest": self._documentation_digest,
            "projection_manifest_digest": projection_digest,
        }
        digest = hashlib.sha256(canonicalize(components)).hexdigest()
        return DatabaseStateMarker(
            marker_id=uuid5(NAMESPACE_URL, digest),
            algorithm="sha256",
            scope="acceptance_observable_state",
            digest=digest,
            captured_at=datetime.now(UTC),
            profile_version="1.0.0",
            acceptance_suite_version="q001-q116-v1",
            configuration_revision="11.5.27.56",
            configuration_profile_digest=self._configuration_profile_digest,
            catalog_revision=catalog.revision,
            catalog_snapshot_digest=catalog.digest,
            documentation_revision=self._documentation_revision,
            documentation_manifest_digest=self._documentation_digest,
            projection_manifest_digest=projection_digest,
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
        expected = EntityRef.model_validate(arguments[constraint.parameter])
        definition = definitions[constraint.fact_id]
        matching = [fact for fact in facts if fact.fact_id == constraint.fact_id]
        for fact in matching:
            if (
                fact.semantic_type != definition.semantic_type
                or not isinstance(fact.value, EntityRef)
                or fact.value.object_type != expected.object_type
                or fact.value.unique_id != expected.unique_id
            ):
                raise ApplicationError(
                    "ENTITY_REF_BINDING_MISMATCH",
                    "Результат содержит business identity, отличную от bound entity parameter.",
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
            "operator:normalize_period"
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


def _overall_outcome(results: list[StepResult], intent: str) -> Outcome:
    outcomes = [result.outcome for result in results]
    for terminal in (
        Outcome.CLARIFICATION_REQUIRED,
        Outcome.MCP_UNAVAILABLE,
        Outcome.QUERY_ERROR,
        Outcome.CONTRACT_ERROR,
    ):
        if terminal in outcomes:
            if any(result.facts for result in results):
                return Outcome.PARTIAL
            return terminal
    if Outcome.ZERO_AGGREGATE in outcomes:
        return Outcome.ZERO_AGGREGATE
    if intent == "documentation":
        return (
            Outcome.DOCUMENTATION_FOUND
            if any(result.facts for result in results)
            else Outcome.DOCUMENTATION_EMPTY
        )
    return (
        Outcome.SUCCESS_WITH_ROWS
        if any(result.facts for result in results)
        else Outcome.SUCCESS_EMPTY
    )


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
