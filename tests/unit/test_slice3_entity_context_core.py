from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ContinuationDraft,
    ExecutionContext,
    PlanExecutor,
    StepResult,
    _apply_resolver_state,
    _context_exports,
    _derive_resolver_use_proofs,
    _entity_fact_origin,
    _pending_from_resolver,
    _selection_proofs,
)
from chatbot1c.application.models import (
    ClarificationResponse,
    ContextFact,
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    HelpSearchRequest,
    MetadataEnvelope,
    PendingChoice,
    PendingClarificationDraft,
    PinnedCatalog,
)
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.digest import canonicalize, generate_integrity
from chatbot1c.contracts.semantic import (
    SemanticValidator,
    build_plan_coverage_proof,
    context_proof_evidence_issues,
)
from chatbot1c.domain.evidence import (
    CatalogSkill,
    CatalogSnapshot,
    ContextExport,
    Coverage,
    DatabaseStateMarker,
    EvidenceBundle,
    Fact,
    SourceLocator,
    StepEvidence,
    UnitNotApplicable,
)
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import FactValueType, McpFixture, Skill
from chatbot1c.domain.types import EntityRef

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills/ut-11.5.27.56"


def _resolver() -> Skill:
    document = json.loads(
        (SKILLS / "ut115.ref.warehouse.resolve.skill.json").read_text()
    )
    document["schema_version"] = "1.1.0"
    document["version"] = "1.1.0"
    for parameter in document["parameters"]:
        parameter["context_slot_keys"] = (
            ["selection.department"]
            if "session_context" in parameter["allowed_sources"]
            else []
        )
    output = document["output_contract"]
    output["contract_version"] = "1.1.0"
    output["resolution"] = {
        "protocol": "typed_entity_resolver_v1",
        "identity_fact_id": "warehouse.ref",
        "candidate_label_fact_ids": ["warehouse.name", "warehouse.type"],
        "role_proof_fact_ids": [],
        "default_slot_key": "selection.warehouse",
    }
    output["context_export_policy"] = [
        {
            "fact_id": "warehouse.ref",
            "slot_key": "selection.warehouse",
            "mode": "selected_only",
            "lifetime": {"mode": "session"},
            "max_members": 100,
        }
    ]
    return Skill.model_validate(generate_integrity(document))


def _legacy(skill_id: str) -> Skill:
    return Skill.model_validate_json((SKILLS / f"{skill_id}.skill.json").read_bytes())


def _plan(resolver: Skill, consumer: Skill | None, cardinality: str = "one") -> PlannerOutput:
    steps: list[dict[str, object]] = [
        {
            "step_id": "s1",
            "kind": "skill_call",
            "skill_id": resolver.skill_id,
            "skill_version": resolver.version,
            "arguments": [],
            "required_output_fact_ids": ["warehouse.ref", "warehouse.name"],
            "on_empty": "stop_not_found",
        }
    ]
    final = [{"step_id": "s1", "fact_id": "warehouse.ref"}]
    if consumer is not None:
        parameter = next(
            item
            for item in consumer.parameters
            if item.semantic_type == "catalog.warehouse"
        )
        arguments: list[dict[str, object]] = [
            {
                "parameter": parameter.name,
                "binding": {
                    "source": "step",
                    "step_id": "s1",
                    "fact_id": "warehouse.ref",
                    "cardinality": cardinality,
                },
            }
        ]
        if any(item.name == "period" and item.required for item in consumer.parameters):
            arguments.append(
                {
                    "parameter": "period",
                    "binding": {
                        "source": "literal",
                        "value_type": "period",
                        "value": {
                            "start": "2026-01-01T00:00:00+03:00",
                            "end_exclusive": "2027-01-01T00:00:00+03:00",
                            "timezone": "Europe/Moscow",
                            "precision": "year",
                        },
                    },
                }
            )
        steps.append(
            {
                "step_id": "s2",
                "kind": "skill_call",
                "skill_id": consumer.skill_id,
                "skill_version": consumer.version,
                "arguments": arguments,
                "required_output_fact_ids": [
                    consumer.output_contract.facts[0].fact_id
                ],
                "on_empty": "stop_not_found",
            }
        )
        final = [
            {"step_id": "s2", "fact_id": consumer.output_contract.facts[0].fact_id}
        ]
    snapshot_id = uuid4()
    return PlannerOutput.model_validate(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(uuid4()),
            "session_context_version": 1,
            "catalog_snapshot_id": str(snapshot_id),
            "catalog_revision": 1,
            "decision": "execute",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": "Синтетическая проверка resolver protocol.",
                "required_facts": [],
                "slots": [],
            },
            "result": {
                "kind": "execute",
                "plan_id": str(uuid4()),
                "steps": steps,
                "final_outputs": final,
            },
        }
    )


def _ref(number: int) -> EntityRef:
    return EntityRef(
        _objectRef=True,
        УникальныйИдентификатор=UUID(f"00000000-0000-4000-8000-{number:012d}"),
        ТипОбъекта="SyntheticRef.UnseenWarehouse",
        Представление=f"Синтетический объект {number}",
    )


def _resolver_facts(refs: tuple[EntityRef, ...]) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for index, ref in enumerate(refs, 1):
        row = f"row_synthetic{index:04d}"
        facts.extend(
            (
                Fact(
                    fact_instance_id=uuid4(),
                    row_id=row,
                    fact_id="warehouse.ref",
                    semantic_type="catalog.warehouse",
                    value_type=FactValueType.ENTITY_REF,
                    value=ref,
                    confirmation="confirmed",
                    step_id="s1",
                    source_locator=SourceLocator(
                        kind="query_column_binding", reference="Склад"
                    ),
                    unit=UnitNotApplicable(mode="not_applicable"),
                ),
                Fact(
                    fact_instance_id=uuid4(),
                    row_id=row,
                    fact_id="warehouse.name",
                    semantic_type="catalog.warehouse.name",
                    value_type=FactValueType.STRING,
                    value=ref.presentation,
                    confirmation="confirmed",
                    step_id="s1",
                    source_locator=SourceLocator(
                        kind="query_column_binding", reference="Наименование"
                    ),
                    unit=UnitNotApplicable(mode="not_applicable"),
                ),
            )
        )
    return tuple(facts)


class _QueueMcp:
    def __init__(self, responses: list[ExecuteQueryEnvelope | ApplicationError]) -> None:
        self.responses = list(responses)

    async def execute_query(self, request: ExecuteQueryRequest) -> ExecuteQueryEnvelope:
        del request
        response = self.responses.pop(0)
        if isinstance(response, ApplicationError):
            raise response
        return response

    async def get_metadata(self, request: object) -> MetadataEnvelope:
        del request
        raise AssertionError("metadata is not used")


class _EmptyDocumentation:
    async def search(self, request: HelpSearchRequest) -> tuple[object, ...]:
        del request
        return ()


class _MemoryTraces:
    def put_artifact(self, trace_id: UUID, name: str, content: bytes) -> None:
        del trace_id, name, content

    def artifacts(self, trace_id: UUID) -> dict[str, bytes]:
        del trace_id
        return {}


def _fixture_envelope(skill: Skill) -> ExecuteQueryEnvelope:
    fixture = skill.tests[0].fixture
    assert isinstance(fixture, McpFixture)
    return ExecuteQueryEnvelope.model_validate(fixture.response)


def _execution_context(plan: PlannerOutput, catalog: PinnedCatalog) -> ExecutionContext:
    now = datetime.now(UTC)
    return ExecutionContext(
        trace_id=uuid4(),
        request_id=plan.request_id,
        session_id=uuid4(),
        turn_id=uuid4(),
        turn_time=now,
        default_list_limit=20,
        catalog=catalog,
        context_facts=(),
        database_state_marker=_marker(catalog),
        deadline_at=now + timedelta(seconds=30),
    )


def _required_execution_plan(resolver: Skill, consumer: Skill) -> PlannerOutput:
    plan = _plan(resolver, consumer)
    payload = plan.model_dump(mode="json", by_alias=True)
    final = consumer.output_contract.facts[0]
    cardinality = {
        "exactly_one": "one",
        "zero_or_one": "zero_or_one",
        "many": "many",
        "aggregate": "aggregate",
    }[consumer.output_contract.cardinality]
    payload["interpretation"]["required_facts"] = [
        {
            "requirement_id": "r1",
            "semantic_type": final.semantic_type,
            "value_type": final.value_type.value,
            "cardinality": cardinality,
            "required": True,
            "unit_dimension": None,
            "time_semantics": "none",
        }
    ]
    return PlannerOutput.model_validate(payload)


def _filter_resolver() -> Skill:
    document = _resolver().model_dump(mode="json", by_alias=True)
    document.pop("integrity")
    type_fact = next(
        item
        for item in document["output_contract"]["facts"]
        if item["fact_id"] == "warehouse.type"
    )
    type_fact["value_type"] = "enum"
    type_fact["allowed_values"] = ["Розничный магазин"]
    document["output_contract"]["context_export_policy"].append(
        {
            "fact_id": "warehouse.type",
            "slot_key": "filter.warehouse_type",
            "mode": "confirmed_filter",
            "semantic_type": "catalog.warehouse.type",
            "value_type": "enum",
            "lifetime": {"mode": "session"},
        }
    )
    return Skill.model_validate(generate_integrity(document))


def _filter_plan(resolver: Skill) -> PlannerOutput:
    plan = _plan(resolver, None)
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["interpretation"]["required_facts"] = [
        {
            "requirement_id": "r1",
            "semantic_type": "catalog.warehouse.type",
            "value_type": "enum",
            "cardinality": "many",
            "required": True,
            "unit_dimension": None,
            "time_semantics": "none",
        }
    ]
    payload["result"]["final_outputs"] = [
        {"step_id": "s1", "fact_id": "warehouse.type"}
    ]
    payload["result"]["steps"][0]["required_output_fact_ids"].append(
        "warehouse.type"
    )
    return PlannerOutput.model_validate(payload)


def test_resolver_use_mode_is_derived_from_dag_not_words_or_planner_flag() -> None:
    resolver = _resolver()
    display_plan = _plan(resolver, None)
    display_catalog = PinnedCatalog.create(
        display_plan.catalog_snapshot_id, 1, {resolver.skill_id: resolver}
    )
    assert _derive_resolver_use_proofs(display_plan, display_catalog)["s1"].mode == (
        "display_only"
    )

    consumer = _legacy("ut115.sales.shipment-list")
    select_plan = _plan(resolver, consumer)
    select_catalog = PinnedCatalog.create(
        select_plan.catalog_snapshot_id,
        1,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    proof = _derive_resolver_use_proofs(select_plan, select_catalog)["s1"]
    assert proof.mode == "select_one"
    assert proof.consumer_parameters == ("s2.warehouse",)


def test_required_final_zero_or_one_entity_uses_select_one_protocol() -> None:
    resolver = _resolver()
    plan = _plan(resolver, None)
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["interpretation"]["required_facts"] = [
        {
            "requirement_id": "r1",
            "semantic_type": "catalog.warehouse",
            "value_type": "entity_ref",
            "cardinality": "zero_or_one",
            "required": True,
            "unit_dimension": None,
            "time_semantics": "none",
        }
    ]
    plan = PlannerOutput.model_validate(payload)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver},
    )

    proof = _derive_resolver_use_proofs(plan, catalog)["s1"]

    assert proof.mode == "select_one"
    assert proof.consumer_parameters == ("final:r1",)


def test_mixed_resolver_use_modes_have_stable_public_error_code() -> None:
    resolver = _resolver()
    consumer_document = _legacy("ut115.sales.shipment-list").model_dump(
        mode="json", by_alias=True
    )
    consumer_document.pop("integrity")
    for parameter_document in consumer_document["parameters"]:
        parameter_document.pop("context_slot_keys", None)
    consumer_document["output_contract"].pop("resolution", None)
    consumer_document["output_contract"].pop("context_export_policy", None)
    warehouse = next(
        item for item in consumer_document["parameters"] if item["name"] == "warehouse"
    )
    warehouse["value_type"] = "entity_ref_list"
    warehouse["normalization"] = "object_ref"
    binding = next(
        item
        for item in consumer_document["operation"]["parameter_bindings"]
        if item["parameter"] == "warehouse"
    )
    binding["encoding"] = "object_ref_list"
    consumer = Skill.model_validate(generate_integrity(consumer_document))
    plan = _plan(resolver, consumer)
    payload = plan.model_dump(mode="json", by_alias=True)
    second = payload["result"]["steps"][1]
    second["arguments"][0]["binding"]["cardinality"] = "one"
    third = json.loads(json.dumps(second))
    third["step_id"] = "s3"
    third["arguments"][0]["binding"]["cardinality"] = "many"
    payload["result"]["steps"].append(third)
    payload["result"]["final_outputs"].append(
        {"step_id": "s3", "fact_id": consumer.output_contract.facts[0].fact_id}
    )
    mixed = PlannerOutput.model_validate(payload)
    catalog = PinnedCatalog.create(
        mixed.catalog_snapshot_id,
        mixed.catalog_revision,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )

    with pytest.raises(ApplicationError) as rejected:
        _derive_resolver_use_proofs(mixed, catalog)

    assert rejected.value.code == "PLAN_RESOLVER_MODE_AMBIGUOUS"


def test_candidates_are_not_selection_and_selected_set_shares_one_random_handle() -> None:
    resolver = _resolver()
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        1,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    use = _derive_resolver_use_proofs(plan, catalog)["s1"]
    facts = _resolver_facts((_ref(1), _ref(2)))
    now = datetime.now(UTC)
    candidates = StepResult(
        "s1",
        resolver,
        Outcome.SUCCESS_WITH_ROWS,
        facts,
        (),
        2,
        now,
        now,
        1,
        collection_scope="complete_set",
    )
    ambiguous = _apply_resolver_state(candidates, use)
    assert ambiguous.outcome is Outcome.CLARIFICATION_REQUIRED
    assert _selection_proofs((ambiguous,)) == ()

    set_use = use.model_copy(update={"mode": "select_set"})
    selected = _apply_resolver_state(candidates, set_use)
    proofs = _selection_proofs((selected,))
    assert len(proofs) == 1
    exports = _context_exports(proofs, (), facts)
    assert len(exports) == 2
    assert len({item.context_handle for item in exports}) == 1
    assert exports[0].context_handle.startswith("ctx_")


def test_indistinguishable_resolver_labels_require_narrowing_without_choices() -> None:
    resolver = _resolver()
    plan = _plan(resolver, None)
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["interpretation"]["required_facts"] = [
        {
            "requirement_id": "r1",
            "semantic_type": "catalog.warehouse",
            "value_type": "entity_ref",
            "cardinality": "one",
            "required": True,
            "unit_dimension": None,
            "time_semantics": "none",
        }
    ]
    plan = PlannerOutput.model_validate(payload)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver},
    )
    use = _derive_resolver_use_proofs(plan, catalog)["s1"]
    first = _ref(1).model_copy(update={"presentation": "Одинаковая подпись"})
    second = _ref(2).model_copy(update={"presentation": "Одинаковая подпись"})
    facts = _resolver_facts((first, second))
    now = datetime.now(UTC)
    ambiguous = _apply_resolver_state(
        StepResult(
            "s1",
            resolver,
            Outcome.SUCCESS_WITH_ROWS,
            facts,
            (),
            2,
            now,
            now,
            1,
            collection_scope="complete_set",
        ),
        use,
    )

    pending = _pending_from_resolver(
        plan,
        _execution_context(plan, catalog),
        (ambiguous,),
    )

    assert pending is not None
    assert pending.has_more_candidates is True
    assert pending.choices == ()
    assert "Уточните критерий" in pending.question_ru


def test_truncated_single_identity_requires_clarification_without_continuation() -> None:
    resolver = _resolver()
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        1,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    use = _derive_resolver_use_proofs(plan, catalog)["s1"]
    now = datetime.now(UTC)
    result = StepResult(
        "s1",
        resolver,
        Outcome.SUCCESS_WITH_ROWS,
        _resolver_facts((_ref(1),)),
        (),
        1,
        now,
        now,
        1,
        has_more=True,
        truncated=True,
        continuation=ContinuationDraft(
            step_id="s1",
            skill_id=resolver.skill_id,
            skill_version=resolver.version,
            skill_digest=resolver.integrity.digest,
            arguments={},
            strategy="keyset",
            page_size=20,
            cumulative_shown=1,
            sort_tuple=("Синтетический объект 1",),
            cursor_values={"ИмяКурсора": "Синтетический объект 1"},
        ),
        collection_scope="visible_page",
    )

    clarified = _apply_resolver_state(result, use)

    assert clarified.outcome is Outcome.CLARIFICATION_REQUIRED
    assert clarified.continuation is None
    assert _selection_proofs((clarified,)) == ()


def test_false_boolean_role_proof_is_not_a_selected_identity() -> None:
    document = _resolver().model_dump(mode="json", by_alias=True)
    document.pop("integrity")
    document["output_contract"]["resolution"]["role_proof_fact_ids"] = [
        "warehouse.role_allowed"
    ]
    document["output_contract"]["facts"].append(
        {
            "fact_id": "warehouse.role_allowed",
            "semantic_type": "catalog.warehouse.role_allowed",
            "value_type": "boolean",
            "role": "attribute",
            "required": True,
            "nullable": False,
            "title_ru": "Соответствует роли",
            "unit_contract": {"mode": "not_applicable"},
        }
    )
    document["provides"]["fact_types"].append("catalog.warehouse.role_allowed")
    document["operation"]["column_bindings"].append(
        {
            "column": "РольПодтверждена",
            "fact_id": "warehouse.role_allowed",
            "accepted_mcp_types": ["Булево"],
            "converter": "boolean",
        }
    )
    resolver = Skill.model_validate(generate_integrity(document))
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        1,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    use = _derive_resolver_use_proofs(plan, catalog)["s1"]
    facts = list(_resolver_facts((_ref(1),)))
    facts.append(
        Fact(
            fact_instance_id=uuid4(),
            row_id=facts[0].row_id,
            fact_id="warehouse.role_allowed",
            semantic_type="catalog.warehouse.role_allowed",
            value_type=FactValueType.BOOLEAN,
            value=False,
            confirmation="confirmed",
            step_id="s1",
            source_locator=SourceLocator(
                kind="query_column_binding", reference="РольПодтверждена"
            ),
            unit=UnitNotApplicable(mode="not_applicable"),
        )
    )
    now = datetime.now(UTC)
    result = _apply_resolver_state(
        StepResult(
            "s1",
            resolver,
            Outcome.SUCCESS_WITH_ROWS,
            tuple(facts),
            (),
            1,
            now,
            now,
            1,
            collection_scope="complete_set",
        ),
        use,
    )

    assert _selection_proofs((result,)) == ()


@pytest.mark.parametrize("failure_kind", ["query_error", "mcp_unavailable"])
def test_required_downstream_technical_failure_does_not_replace_context(
    failure_kind: str,
) -> None:
    resolver = _resolver()
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _required_execution_plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    downstream = _fixture_envelope(consumer)
    failure: ExecuteQueryEnvelope | ApplicationError
    if failure_kind == "query_error":
        failure = downstream.model_copy(
            update={
                "success": False,
                "data": (),
                "count": 0,
                "error": "synthetic query failure",
            }
        )
    else:
        failure = ApplicationError(
            "MCP_UNAVAILABLE", "Синтетическая недоступность MCP.", 503
        )
    context = _execution_context(plan, catalog)
    executor = PlanExecutor(
        _QueueMcp([_fixture_envelope(resolver), failure]),
        _EmptyDocumentation(),
        _MemoryTraces(),
    )

    execution = asyncio.run(executor.execute(plan, context))

    assert execution.context_facts == ()
    assert execution.selection_proofs == ()
    assert execution.evidence.context_exports == ()
    assert context.context_facts == ()
    assert any(
        step.criticality == "required"
        and step.outcome in {Outcome.QUERY_ERROR, Outcome.MCP_UNAVAILABLE}
        for step in execution.steps
    )
    assert not SemanticValidator().evidence_issues(
        execution.evidence, available_skills=tuple(catalog.skills.values())
    )


def test_valid_required_downstream_empty_may_keep_confirmed_entity() -> None:
    resolver = _resolver()
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _required_execution_plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    empty = _fixture_envelope(consumer).model_copy(
        update={"data": (), "count": 0, "has_more": False, "truncated": False}
    )
    executor = PlanExecutor(
        _QueueMcp([_fixture_envelope(resolver), empty]),
        _EmptyDocumentation(),
        _MemoryTraces(),
    )

    execution = asyncio.run(executor.execute(plan, _execution_context(plan, catalog)))

    assert execution.outcome is Outcome.SUCCESS_EMPTY
    assert len(execution.selection_proofs) == 1
    assert len(execution.context_facts) == 1
    assert len(execution.evidence.context_exports) == 1
    assert not SemanticValidator().evidence_issues(
        execution.evidence, available_skills=tuple(catalog.skills.values())
    )


def test_tampered_selection_proof_is_rejected_cross_artifact() -> None:
    resolver = _resolver()
    consumer = _legacy("ut115.sales.shipment-list")
    plan = _required_execution_plan(resolver, consumer)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver, consumer.skill_id: consumer},
    )
    empty = _fixture_envelope(consumer).model_copy(update={"data": (), "count": 0})
    execution = asyncio.run(
        PlanExecutor(
            _QueueMcp([_fixture_envelope(resolver), empty]),
            _EmptyDocumentation(),
            _MemoryTraces(),
        ).execute(plan, _execution_context(plan, catalog))
    )
    proof = execution.selection_proofs[0]
    tampered = (
        proof.model_copy(update={"proof_digest": "0" * 64}),
    )

    codes = {
        issue.code
        for issue in context_proof_evidence_issues(
            build_plan_coverage_proof(plan, tuple(catalog.skills.values())),
            execution.evidence,
            selection_proofs=tampered,
            filter_retention_proofs=execution.filter_retention_proofs,
            available_skills=tuple(catalog.skills.values()),
        )
    }

    assert "CONTEXT_SELECTION_PROOF_INVALID" in codes


def test_tampered_filter_retention_proof_is_rejected_cross_artifact() -> None:
    resolver = _filter_resolver()
    plan = _filter_plan(resolver)
    catalog = PinnedCatalog.create(
        plan.catalog_snapshot_id,
        plan.catalog_revision,
        {resolver.skill_id: resolver},
    )
    execution = asyncio.run(
        PlanExecutor(
            _QueueMcp([_fixture_envelope(resolver)]),
            _EmptyDocumentation(),
            _MemoryTraces(),
        ).execute(plan, _execution_context(plan, catalog))
    )
    assert len(execution.filter_retention_proofs) == 1
    assert not SemanticValidator().evidence_issues(
        execution.evidence, available_skills=(resolver,)
    )
    proof = execution.filter_retention_proofs[0]
    tampered = (
        proof.model_copy(update={"canonical_value_digest": "0" * 64}),
    )

    codes = {
        issue.code
        for issue in context_proof_evidence_issues(
            build_plan_coverage_proof(plan, (resolver,)),
            execution.evidence,
            selection_proofs=execution.selection_proofs,
            filter_retention_proofs=tampered,
            available_skills=(resolver,),
        )
    }

    assert "CONTEXT_FILTER_PROOF_INVALID" in codes


def _marker(catalog: PinnedCatalog) -> DatabaseStateMarker:
    return DatabaseStateMarker(
        marker_id=uuid4(),
        algorithm="sha256",
        scope="acceptance_observable_state",
        digest="1" * 64,
        captured_at=datetime.now(UTC),
        profile_version="1.0.0",
        acceptance_suite_version="q001-q116-v1",
        configuration_revision="11.5.27.56",
        configuration_profile_digest="2" * 64,
        catalog_revision=catalog.revision,
        catalog_snapshot_digest=catalog.digest,
        documentation_revision="fixture",
        documentation_manifest_digest="3" * 64,
        projection_manifest_digest="4" * 64,
    )


def _evidence(
    *,
    turn: object,
    session_id: UUID,
    catalog: PinnedCatalog,
    producer: Skill,
    facts: tuple[Fact, ...],
    handle: str,
) -> EvidenceBundle:
    from chatbot1c.application.models import TurnRecord

    assert isinstance(turn, TurnRecord)
    now = datetime.now(UTC)
    return EvidenceBundle(
        schema_version="1.1.0",
        document_type="evidence_bundle",
        trace_id=turn.trace_id,
        request_id=turn.request_id,
        session_id=session_id,
        created_at=now,
        source_boundary="data",
        outcome=Outcome.SUCCESS_WITH_ROWS,
        catalog_snapshot=CatalogSnapshot(
            snapshot_id=catalog.snapshot_id,
            revision=catalog.revision,
            skills=tuple(
                CatalogSkill(
                    skill_id=item.skill_id,
                    version=item.version,
                    digest=item.integrity.digest,
                )
                for item in catalog.skills.values()
            ),
        ),
        database_state_marker=_marker(catalog),
        steps=(
            StepEvidence(
                step_id="s1",
                source_kind="mcp_data",
                operation_ref=f"skill://{producer.skill_id}/{producer.version}",
                started_at=now,
                finished_at=now,
                attempts=1,
                status=Outcome.SUCCESS_WITH_ROWS,
                row_count=2,
                truncated=False,
                has_more=False,
                produced_fact_instance_ids=tuple(
                    item.fact_instance_id for item in facts
                ),
                error_ids=(),
                collection_scope="complete_set",
            ),
        ),
        facts=facts,
        citations=(),
        documentation_disagreements=(),
        coverage=Coverage(sufficient=True, requirements=()),
        context_exports=tuple(
            ContextExport(
                context_handle=handle,
                fact_instance_id=item.fact_instance_id,
                semantic_type=item.semantic_type,
            )
            for item in facts
            if item.fact_id == "warehouse.ref"
        ),
        errors=(),
    )


def test_sqlite_selected_set_is_one_slot_generation_and_survives_restart(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        auto_import=True,
    )
    catalog = runtime.catalog.pin()
    producer = catalog.skills["ut115.ref.warehouse.resolve"]
    refs = (
        EntityRef(
            _objectRef=True,
            УникальныйИдентификатор=UUID("00000000-0000-4000-8000-000000000101"),
            ТипОбъекта="СправочникСсылка.Склады",
            Представление="Первый склад",
        ),
        EntityRef(
            _objectRef=True,
            УникальныйИдентификатор=UUID("00000000-0000-4000-8000-000000000102"),
            ТипОбъекта="СправочникСсылка.Склады",
            Представление="Второй склад",
        ),
    )
    facts = _resolver_facts(refs)
    session = runtime.store.create_session()
    turn, _ = runtime.store.create_turn(
        session_id=session.session_id,
        text="Выбрать два склада",
        client_message_id="slice3-selected-set",
        expected_context_version=1,
    )
    runtime.store.pin_turn(turn.turn_id, catalog)
    handle = "ctx_abcdefghijklmnopqrstuvwxyzABCDEF"
    contexts = tuple(
        ContextFact(
            handle=handle,
            semantic_type=fact.semantic_type,
            value=fact.value.model_dump(mode="json", by_alias=True),
            presentation=fact.value.presentation,
            origin_turn_id=turn.turn_id,
            origin_fact_instance_id=fact.fact_instance_id,
            origin=_entity_fact_origin(fact, producer),
            slot_key="selection.warehouse",
            value_type=FactValueType.ENTITY_REF,
            policy_mode="selected_only",
            cardinality="many",
            member_index=index,
            value_digest=hashlib.sha256(
                canonicalize(fact.value.model_dump(mode="json", by_alias=True))
            ).hexdigest(),
            proof_digest="5" * 64,
        )
        for index, fact in enumerate(
            item for item in facts if item.fact_id == "warehouse.ref"
        )
    )
    evidence = _evidence(
        turn=turn,
        session_id=session.session_id,
        catalog=catalog,
        producer=producer,
        facts=facts,
        handle=handle,
    )
    runtime.store.complete_turn(
        turn_id=turn.turn_id,
        assistant_text="Выбрано два склада",
        status="completed",
        outcome=Outcome.SUCCESS_WITH_ROWS,
        plan_json=None,
        evidence_json=evidence.model_dump_json(by_alias=True),
        context_exports=contexts,
    )

    turn_session = runtime.store.create_session("Turn lifetime")
    turn_only, _ = runtime.store.create_turn(
        session_id=turn_session.session_id,
        text="Эфемерный выбор",
        client_message_id="slice3-turn-lifetime",
        expected_context_version=1,
    )
    runtime.store.pin_turn(turn_only.turn_id, catalog)
    turn_fact = next(
        item for item in _resolver_facts((refs[0],)) if item.fact_id == "warehouse.ref"
    )
    assert isinstance(turn_fact.value, EntityRef)
    runtime.store.complete_turn(
        turn_id=turn_only.turn_id,
        assistant_text="Эфемерный выбор завершен",
        status="completed",
        outcome=Outcome.SUCCESS_WITH_ROWS,
        plan_json=None,
        evidence_json=None,
        context_exports=(
            ContextFact(
                handle="ctx_ZYXWVUTSRQPONMLKJIHGFEDCBA987654",
                semantic_type=turn_fact.semantic_type,
                value=turn_fact.value.model_dump(mode="json", by_alias=True),
                presentation=turn_fact.value.presentation,
                origin_turn_id=turn_only.turn_id,
                origin_fact_instance_id=turn_fact.fact_instance_id,
                origin=_entity_fact_origin(turn_fact, producer),
                slot_key="selection.warehouse",
                lifetime_mode="turn",
            ),
        ),
    )
    assert runtime.store.context_facts(turn_session.session_id) == ()
    with runtime.store.engine.connect() as connection:
        stored_lifetime = connection.exec_driver_sql(
            "SELECT lifetime_mode,status,reason FROM context_slots "
            "WHERE session_id=?",
            (str(turn_session.session_id),),
        ).one()
    assert tuple(stored_lifetime) == ("turn", "expired", "policy_time_reached")
    asyncio.run(runtime.close())

    restarted = build_runtime(Settings(app_data_dir=tmp_path), auto_import=False)
    restored = restarted.store.context_facts(session.session_id)
    assert len(restored) == 2
    assert {item.handle for item in restored} == {handle}
    assert [item.member_index for item in restored] == [0, 1]
    slots = restarted.store.context_slots(session.session_id)
    assert len(slots) == 1
    assert slots[0].member_count == 2
    assert slots[0].cardinality == "many"
    asyncio.run(restarted.close())


def test_pending_clarification_is_persisted_one_use_and_claim_is_deterministic(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        auto_import=True,
    )
    catalog = runtime.catalog.pin()
    marker = runtime.marker.capture(catalog)
    session = runtime.store.create_session()
    turn, _ = runtime.store.create_turn(
        session_id=session.session_id,
        text="Неоднозначный вопрос",
        client_message_id="slice3-pending-origin",
        expected_context_version=1,
    )
    runtime.store.pin_turn(turn.turn_id, catalog)
    draft = PendingClarificationDraft(
        kind="interpretation_choice",
        question_ru="Какой показатель выбрать?",
        original_question=turn.user_text,
        plan_json="{}",
        resolver_step_id=None,
        choices=(
            PendingChoice(
                choice_id="c1",
                label_ru="Количество",
                binding={
                    "source": "literal",
                    "value_type": "enum",
                    "value": "quantity",
                },
            ),
        ),
        has_more_candidates=False,
        catalog_snapshot_id=catalog.snapshot_id,
        catalog_revision=catalog.revision,
        database_marker=marker.digest,
    )
    runtime.store.complete_turn(
        turn_id=turn.turn_id,
        assistant_text=draft.question_ru,
        status="completed",
        outcome=Outcome.CLARIFICATION_REQUIRED,
        plan_json="{}",
        evidence_json=None,
        context_exports=(),
        pending_clarification=draft,
    )
    pending = runtime.store.active_pending(session.session_id)
    assert pending is not None
    assert pending.context_version == 2
    handle = pending.handle
    asyncio.run(runtime.close())

    restarted = build_runtime(Settings(app_data_dir=tmp_path), auto_import=False)
    active = restarted.catalog.pin()
    claimed, response_turn = restarted.store.claim_clarification(
        session_id=session.session_id,
        text="Количество",
        client_message_id="slice3-pending-choice",
        expected_context_version=2,
        response=ClarificationResponse(
            handle=handle, action="choose", choice_id="c1"
        ),
        active_catalog=active,
        database_marker=restarted.marker.capture(active).digest,
    )
    assert claimed.claim_turn_id == response_turn.turn_id
    assert claimed.claimed_choice_id == "c1"
    with pytest.raises(ApplicationError) as reused:
        restarted.store.claim_clarification(
            session_id=session.session_id,
            text="Количество",
            client_message_id="slice3-pending-reuse",
            expected_context_version=2,
            response=ClarificationResponse(
                handle=handle, action="choose", choice_id="c1"
            ),
            active_catalog=active,
            database_marker=restarted.marker.capture(active).digest,
        )
    assert reused.value.code == "CLARIFICATION_CONSUMED"
    asyncio.run(restarted.close())
