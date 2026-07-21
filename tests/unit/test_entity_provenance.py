from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ResolvedBinding,
    _entity_fact_origin,
    _validate_bound_entity_identity,
    _validate_runtime_arguments,
)
from chatbot1c.application.models import ContextFact
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
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
from chatbot1c.domain.skill import DataQueryOperation, FactValueType, Skill
from chatbot1c.domain.types import EntityRef

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills/ut-11.5.27.56"
PRODUCER_ID = "ut115.sales.order-header-status-by-number"
CONSUMER_ID = "ut115.sales.order-lines"


def _skill(skill_id: str) -> Skill:
    return Skill.model_validate_json((SKILLS / f"{skill_id}.skill.json").read_bytes())


def _ref(
    *,
    object_type: str = "ДокументСсылка.ЗаказКлиента",
    unique_id: UUID = UUID("00000000-0000-4000-8000-000000000136"),
    presentation: str = "Заказ 0000-000005",
) -> EntityRef:
    return EntityRef(
        _objectRef=True,
        УникальныйИдентификатор=unique_id,
        ТипОбъекта=object_type,
        Представление=presentation,
    )


def _fact(ref: EntityRef, *, semantic_type: str = "document.sales_order") -> Fact:
    return Fact(
        fact_instance_id=uuid4(),
        row_id="row_order000001",
        fact_id="order.ref",
        semantic_type=semantic_type,
        value_type=FactValueType.ENTITY_REF,
        value=ref,
        confirmation="confirmed",
        step_id="s1",
        source_locator=SourceLocator(
            kind="query_column_binding", reference="Заказ"
        ),
        unit=UnitNotApplicable(mode="not_applicable"),
    )


def _json_ref(ref: EntityRef) -> JsonValue:
    return ref.model_dump(mode="json", by_alias=True)


def test_entity_argument_requires_exact_producer_and_closed_entity_types() -> None:
    producer = _skill(PRODUCER_ID)
    consumer = _skill(CONSUMER_ID)
    ref = _ref()
    fact = _fact(ref)
    origin = _entity_fact_origin(fact, producer)
    proven = ResolvedBinding(_json_ref(ref), "previous_step", (origin,))

    _validate_runtime_arguments(consumer, {"order": proven})

    with pytest.raises(ApplicationError) as missing:
        _validate_runtime_arguments(
            consumer,
            {"order": ResolvedBinding(_json_ref(ref), "user_slot")},
        )
    assert missing.value.code == "PLAN_ARGUMENT_SOURCE_FORBIDDEN"

    parameter = consumer.parameters[0].model_copy(
        update={"entity_types": ("document.synthetic",)}
    )
    disallowed = consumer.model_copy(update={"parameters": (parameter,)})
    with pytest.raises(ApplicationError) as mismatch:
        _validate_runtime_arguments(disallowed, {"order": proven})
    assert mismatch.value.code == "ENTITY_BINDING_PROVENANCE_MISMATCH"

    renamed = _ref(presentation="Заказ с новым представлением")
    with pytest.raises(ApplicationError) as changed_outbound:
        _validate_runtime_arguments(
            consumer,
            {
                "order": ResolvedBinding(
                    _json_ref(renamed), "previous_step", (origin,)
                )
            },
        )
    assert changed_outbound.value.code == "ENTITY_BINDING_PROVENANCE_MISMATCH"


def test_result_identity_ignores_presentation_but_rejects_other_uuid() -> None:
    skill = _skill(CONSUMER_ID)
    expected = _ref()
    renamed_fact = _fact(_ref(presentation="Переименованный заказ"))
    _validate_bound_entity_identity(skill, {"order": _json_ref(expected)}, (renamed_fact,))

    wrong = _fact(_ref(unique_id=UUID("00000000-0000-4000-8000-000000000999")))
    with pytest.raises(ApplicationError) as mismatch:
        _validate_bound_entity_identity(skill, {"order": _json_ref(expected)}, (wrong,))
    assert mismatch.value.code == "ENTITY_REF_BINDING_MISMATCH"


def test_synthetic_semantic_and_physical_pair_needs_no_application_map() -> None:
    producer = _skill(PRODUCER_ID)
    consumer = _skill(CONSUMER_ID)
    assert isinstance(producer.operation, DataQueryOperation)

    producer_fact = producer.output_contract.facts[0].model_copy(
        update={"semantic_type": "entity.synthetic_pair"}
    )
    output = producer.output_contract.model_copy(
        update={
            "facts": (producer_fact, *producer.output_contract.facts[1:]),
        }
    )
    producer_binding = producer.operation.column_bindings[0].model_copy(
        update={"accepted_mcp_types": ("SyntheticRef.Pair",)}
    )
    operation = producer.operation.model_copy(
        update={
            "column_bindings": (
                producer_binding,
                *producer.operation.column_bindings[1:],
            )
        }
    )
    synthetic_producer = producer.model_copy(
        update={"output_contract": output, "operation": operation}
    )
    consumer_parameter = consumer.parameters[0].model_copy(
        update={
            "semantic_type": "entity.synthetic_pair",
            "entity_types": ("entity.synthetic_pair",),
        }
    )
    synthetic_consumer = consumer.model_copy(
        update={"parameters": (consumer_parameter,)}
    )
    ref = _ref(object_type="SyntheticRef.Pair")
    fact = _fact(ref, semantic_type="entity.synthetic_pair")
    origin = _entity_fact_origin(fact, synthetic_producer)

    _validate_runtime_arguments(
        synthetic_consumer,
        {"order": ResolvedBinding(_json_ref(ref), "previous_step", (origin,))},
    )

    application_source = (ROOT / "src/chatbot1c/application").glob("*.py")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in application_source)
    for forbidden in (
        "_physical_type",
        "ДокументСсылка.",
        "СправочникСсылка.",
        "ЗаказКлиента",
        "Номенклатура",
    ):
        assert forbidden not in combined


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("entity_types", ["document.other"], "PARAMETER_ENTITY_TYPE_NOT_ALLOWED"),
        ("allowed_sources", ["user_slot"], "ENTITY_REF_SOURCE_UNPROVEN"),
    ],
)
def test_import_rejects_unproven_entity_parameter_contract(
    field: str, value: list[str], expected_code: str
) -> None:
    document = json.loads((SKILLS / f"{CONSUMER_ID}.skill.json").read_bytes())
    document["parameters"][0][field] = value
    signed = generate_integrity(document)
    with pytest.raises(ContractValidationError) as rejected:
        ContractHarness.discover(ROOT).validate_document(signed)
    assert expected_code in {issue.code for issue in rejected.value.issues}


def test_sqlite_context_restores_exact_origin_fact_after_restart(tmp_path: Path) -> None:
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        auto_import=True,
    )
    catalog = runtime.catalog.pin()
    producer = catalog.skills[PRODUCER_ID]
    session = runtime.store.create_session()
    turn, _ = runtime.store.create_turn(
        session_id=session.session_id,
        text="Статус заказа",
        client_message_id="message-0001",
        expected_context_version=session.context_version,
    )
    runtime.store.pin_turn(turn.turn_id, catalog)

    fact = _fact(_ref())
    origin = _entity_fact_origin(fact, producer)
    handle = "ctx_orderprovenance000001"
    context = ContextFact(
        handle=handle,
        semantic_type=fact.semantic_type,
        value=_json_ref(fact.value),
        presentation=fact.value.presentation,
        origin_turn_id=turn.turn_id,
        origin_fact_instance_id=fact.fact_instance_id,
        origin=origin,
    )
    now = datetime.now(UTC)
    marker = DatabaseStateMarker(
        marker_id=uuid4(),
        algorithm="sha256",
        scope="acceptance_observable_state",
        digest="0" * 64,
        captured_at=now,
        profile_version="1.0.0",
        acceptance_suite_version="q001-q116-v1",
        configuration_revision="11.5.27.56",
        configuration_profile_digest="0" * 64,
        catalog_revision=catalog.revision,
        catalog_snapshot_digest=catalog.digest,
        documentation_revision="unavailable",
        documentation_manifest_digest="0" * 64,
        projection_manifest_digest="0" * 64,
    )
    evidence = EvidenceBundle(
        schema_version="1.0.0",
        document_type="evidence_bundle",
        trace_id=turn.trace_id,
        request_id=turn.request_id,
        session_id=session.session_id,
        created_at=now,
        source_boundary="data",
        outcome=Outcome.SUCCESS_WITH_ROWS,
        catalog_snapshot=CatalogSnapshot(
            snapshot_id=catalog.snapshot_id,
            revision=catalog.revision,
            skills=tuple(
                CatalogSkill(
                    skill_id=skill.skill_id,
                    version=skill.version,
                    digest=skill.integrity.digest,
                )
                for skill in catalog.skills.values()
            ),
        ),
        database_state_marker=marker,
        steps=(
            StepEvidence(
                step_id="s1",
                source_kind="mcp_data",
                operation_ref=f"skill://{producer.skill_id}/{producer.version}",
                started_at=now,
                finished_at=now,
                attempts=1,
                status=Outcome.SUCCESS_WITH_ROWS,
                row_count=1,
                truncated=False,
                has_more=False,
                produced_fact_instance_ids=(fact.fact_instance_id,),
                error_ids=(),
            ),
        ),
        facts=(fact,),
        citations=(),
        documentation_disagreements=(),
        coverage=Coverage(sufficient=True, requirements=()),
        context_exports=(
            ContextExport(
                context_handle=handle,
                fact_instance_id=fact.fact_instance_id,
                semantic_type=fact.semantic_type,
            ),
        ),
        errors=(),
    )
    runtime.store.complete_turn(
        turn_id=turn.turn_id,
        assistant_text="Статус найден",
        status="completed",
        outcome=Outcome.SUCCESS_WITH_ROWS,
        plan_json=None,
        evidence_json=evidence.model_dump_json(by_alias=True),
        context_exports=(context,),
    )
    asyncio.run(runtime.close())

    restarted = build_runtime(Settings(app_data_dir=tmp_path), auto_import=False)
    restored = restarted.store.context_facts(session.session_id)
    assert len(restored) == 1
    assert restored[0].origin_turn_id == turn.turn_id
    assert restored[0].origin_fact_instance_id == fact.fact_instance_id
    assert restored[0].origin.fact == fact
    asyncio.run(restarted.close())
