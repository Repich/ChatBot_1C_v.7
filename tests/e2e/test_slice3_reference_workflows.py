from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ExecutionContext,
    ExecutionResult,
    PlanExecutor,
)
from chatbot1c.application.models import (
    ContextFact,
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    HelpSearchRequest,
    MetadataEnvelope,
    PinnedCatalog,
    PlannerRequest,
)
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.evidence import DatabaseStateMarker
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import DataQueryOperation, Skill
from chatbot1c.domain.types import EntityRef

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills/ut-11.5.27.56"
REFERENCE_PACKAGE = SKILLS / "ut115-reference-1.1.0.package.json"

CUSTOMER_RESOLVER = "ut115.ref.customer.resolve-name-contains"
CUSTOMER_DETAILS = "ut115.ref.customer.details"

PERIOD = {
    "start": "2026-01-01T00:00:00+03:00",
    "end_exclusive": "2027-01-01T00:00:00+03:00",
    "timezone": "Europe/Moscow",
    "precision": "year",
}


class _QueueMcp:
    def __init__(self, responses: list[ExecuteQueryEnvelope]) -> None:
        self.responses = list(responses)
        self.requests: list[ExecuteQueryRequest] = []

    async def execute_query(self, request: ExecuteQueryRequest) -> ExecuteQueryEnvelope:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected MCP execute_query call")
        return self.responses.pop(0)

    async def get_metadata(self, request: object) -> MetadataEnvelope:
        del request
        raise AssertionError("metadata is not used by reference workflow tests")


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


class _PlannerQueue:
    def __init__(
        self, builders: list[Callable[[PlannerRequest], PlannerOutput]]
    ) -> None:
        self.builders = list(builders)
        self.requests: list[PlannerRequest] = []

    def outbound_http_request(self, request: PlannerRequest) -> bytes | None:
        del request
        return None

    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        self.requests.append(request)
        if not self.builders:
            raise AssertionError("unexpected planner call")
        return self.builders.pop(0)(request)


def _load_skill(filename: str) -> Skill:
    return Skill.model_validate_json((SKILLS / filename).read_bytes())


def _entity_ref(
    object_type: str, number: int, presentation: str
) -> dict[str, JsonValue]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": f"00000000-0000-4000-8000-{number:012d}",
        "ТипОбъекта": object_type,
        "Представление": presentation,
    }


def _envelope(
    skill: Skill,
    rows: list[dict[str, JsonValue]],
    *,
    has_more: bool = False,
    truncated: bool = False,
) -> ExecuteQueryEnvelope:
    operation = skill.operation
    assert isinstance(operation, DataQueryOperation)
    return ExecuteQueryEnvelope.model_validate(
        {
            "success": True,
            "data": rows,
            "schema": {
                "columns": [
                    {
                        "name": binding.column,
                        "types": list(binding.accepted_mcp_types),
                    }
                    for binding in operation.column_bindings
                ]
            },
            "count": len(rows),
            "has_more": has_more,
            "truncated": truncated,
        }
    )


def _required_fact_ids(skill: Skill) -> list[str]:
    required = {
        fact.fact_id
        for fact in skill.output_contract.facts
        if fact.required and not fact.nullable
    }
    required.update(skill.output_contract.row_identity_fact_ids or ())
    return sorted(required)


def _plan(
    *,
    request_id: UUID,
    context_version: int,
    catalog: PinnedCatalog,
    goal: str,
    requirement: dict[str, JsonValue],
    steps: list[dict[str, JsonValue]],
    final_outputs: list[dict[str, JsonValue]],
    slots: list[dict[str, JsonValue]] | None = None,
) -> PlannerOutput:
    return PlannerOutput.model_validate(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(request_id),
            "session_context_version": context_version,
            "catalog_snapshot_id": str(catalog.snapshot_id),
            "catalog_revision": catalog.revision,
            "decision": "execute",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": goal,
                "required_facts": [requirement],
                "slots": slots or [],
            },
            "result": {
                "kind": "execute",
                "plan_id": str(uuid4()),
                "steps": steps,
                "final_outputs": final_outputs,
            },
        }
    )


def _customer_plan(
    *,
    request_id: UUID,
    context_version: int,
    catalog: PinnedCatalog,
    context_handle: str | None = None,
) -> PlannerOutput:
    resolver = catalog.skills[CUSTOMER_RESOLVER]
    details = catalog.skills[CUSTOMER_DETAILS]
    if context_handle is None:
        customer_binding: dict[str, JsonValue] = {
            "source": "step",
            "step_id": "s1",
            "fact_id": "customer.ref",
            "cardinality": "one",
        }
        steps: list[dict[str, JsonValue]] = [
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": resolver.skill_id,
                "skill_version": resolver.version,
                "arguments": [
                    {
                        "parameter": "name_fragment",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "Альфа",
                        },
                    }
                ],
                "required_output_fact_ids": _required_fact_ids(resolver),
                "on_empty": "stop_not_found",
            }
        ]
        details_step_id = "s2"
        slots: list[dict[str, JsonValue]] = []
    else:
        customer_binding = {
            "source": "context",
            "context_handle": context_handle,
            "expected_semantic_type": "party.customer",
        }
        steps = []
        details_step_id = "s1"
        slots = [
            {
                "slot_id": "customer",
                "semantic_type": "party.customer",
                "value_type": "entity_ref",
                "status": "resolved_context",
                "mentions": ["этот клиент"],
                "binding": customer_binding,
            }
        ]
    steps.append(
        {
            "step_id": details_step_id,
            "kind": "skill_call",
            "skill_id": details.skill_id,
            "skill_version": details.version,
            "arguments": [{"parameter": "customer", "binding": customer_binding}],
            "required_output_fact_ids": _required_fact_ids(details),
            "on_empty": "stop_not_found",
        }
    )
    return _plan(
        request_id=request_id,
        context_version=context_version,
        catalog=catalog,
        goal="Получить реквизиты точно выбранного клиента.",
        requirement={
            "requirement_id": "r1",
            "semantic_type": "party.partner.name",
            "value_type": "string",
            "cardinality": "many",
            "required": True,
        },
        steps=steps,
        final_outputs=[{"step_id": details_step_id, "fact_id": "partner.name"}],
        slots=slots,
    )


def _customer_resolution_plan(
    *,
    request_id: UUID,
    context_version: int,
    catalog: PinnedCatalog,
) -> PlannerOutput:
    resolver = catalog.skills[CUSTOMER_RESOLVER]
    return _plan(
        request_id=request_id,
        context_version=context_version,
        catalog=catalog,
        goal="Точно выбрать клиента.",
        requirement={
            "requirement_id": "r1",
            "semantic_type": "party.customer",
            "value_type": "entity_ref",
            "cardinality": "one",
            "required": True,
        },
        steps=[
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": resolver.skill_id,
                "skill_version": resolver.version,
                "arguments": [
                    {
                        "parameter": "name_fragment",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "Альфа",
                        },
                    }
                ],
                "required_output_fact_ids": _required_fact_ids(resolver),
                "on_empty": "stop_not_found",
            }
        ],
        final_outputs=[{"step_id": "s1", "fact_id": "customer.ref"}],
    )


def _document_rank_plan(
    *,
    skill: Skill,
    catalog: PinnedCatalog,
    identity_fact_id: str,
    rank_fact_id: str,
    semantic_type: str,
    ties: str = "include_all",
) -> PlannerOutput:
    return _plan(
        request_id=uuid4(),
        context_version=1,
        catalog=catalog,
        goal="Выбрать последний документ из полного набора.",
        requirement={
            "requirement_id": "r1",
            "semantic_type": semantic_type,
            "value_type": "entity_ref",
            "cardinality": "one",
            "required": True,
        },
        steps=[
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": skill.skill_id,
                "skill_version": skill.version,
                "arguments": [
                    {
                        "parameter": "period",
                        "binding": {
                            "source": "literal",
                            "value_type": "period",
                            "value": PERIOD,
                        },
                    }
                ],
                "required_output_fact_ids": _required_fact_ids(skill),
                "on_empty": "stop_not_found",
            },
            {
                "step_id": "s2",
                "kind": "operator_call",
                "operator": "rank",
                "input_step_id": "s1",
                "sort_fact_id": rank_fact_id,
                "direction": "descending",
                "limit": {
                    "source": "literal",
                    "value_type": "integer",
                    "value": 1,
                },
                "ties": ties,
            },
        ],
        final_outputs=[{"step_id": "s2", "fact_id": identity_fact_id}],
    )


def _execution_context(
    plan: PlannerOutput,
    catalog: PinnedCatalog,
    *,
    context_facts: tuple[ContextFact, ...] = (),
) -> ExecutionContext:
    now = datetime(2038, 4, 5, 6, 7, 8, tzinfo=UTC)
    return ExecutionContext(
        trace_id=uuid4(),
        request_id=plan.request_id,
        session_id=uuid4(),
        turn_id=uuid4(),
        turn_time=now,
        default_list_limit=20,
        catalog=catalog,
        context_facts=context_facts,
        database_state_marker=DatabaseStateMarker(
            marker_id=uuid4(),
            algorithm="sha256",
            scope="acceptance_observable_state",
            digest="1" * 64,
            captured_at=now,
            profile_version="1.0.0",
            acceptance_suite_version="q001-q116-v1",
            configuration_revision="11.5.27.56",
            configuration_profile_digest="2" * 64,
            catalog_revision=catalog.revision,
            catalog_snapshot_digest=catalog.digest,
            documentation_revision="reference-e2e",
            documentation_manifest_digest="3" * 64,
            projection_manifest_digest="4" * 64,
        ),
        deadline_at=now + timedelta(seconds=30),
    )


def _execute(
    plan: PlannerOutput,
    catalog: PinnedCatalog,
    mcp: _QueueMcp,
    *,
    context_facts: tuple[ContextFact, ...] = (),
) -> ExecutionResult:
    ContractHarness.discover(ROOT).semantics.validate(
        plan, available_skills=tuple(catalog.skills.values())
    )
    executor = PlanExecutor(mcp, _EmptyDocumentation(), _MemoryTraces())
    return asyncio.run(
        executor.execute(
            plan,
            _execution_context(plan, catalog, context_facts=context_facts),
        )
    )


def _customer_rows(
    resolver: Skill, details: Skill, number: int, name: str
) -> tuple[ExecuteQueryEnvelope, ExecuteQueryEnvelope, dict[str, JsonValue]]:
    customer = _entity_ref("СправочникСсылка.Партнеры", number, name)
    contractor = _entity_ref(
        "СправочникСсылка.Контрагенты", number + 100, f"{name}, юрлицо"
    )
    resolver_row: dict[str, JsonValue] = {
        "Клиент": customer,
        "Наименование": name,
        "Код": f"КЛ-{number:04d}",
        "ЭтоКлиент": True,
        "ЭтоПоставщик": False,
        "ИНН": "7700000000",
    }
    details_row: dict[str, JsonValue] = {
        "Клиент": customer,
        "Наименование": name,
        "Код": f"КЛ-{number:04d}",
        "Контрагент": contractor,
        "КонтрагентНаименование": f"{name}, юрлицо",
        "ИНН": "7700000000",
        "КПП": "770001001",
        "ВидКонтакта": "Телефон",
        "Контакт": "+7 495 000-00-00",
    }
    return (
        _envelope(resolver, [resolver_row]),
        _envelope(details, [details_row]),
        customer,
    )


def _receipt_row(number: int, moment: str, amount: float) -> dict[str, JsonValue]:
    return {
        "Поступление": _entity_ref(
            "ДокументСсылка.ПриобретениеТоваровУслуг",
            number,
            f"Поступление {number}",
        ),
        "Номер": f"ПТ-{number:04d}",
        "Дата": moment,
        "Партнер": _entity_ref(
            "СправочникСсылка.Партнеры", 1000 + number, f"Поставщик {number}"
        ),
        "ПартнерНаименование": f"Поставщик {number}",
        "Организация": _entity_ref(
            "СправочникСсылка.Организации", 2000, "Торговый дом"
        ),
        "ОрганизацияНаименование": "Торговый дом",
        "СуммаДокумента": amount,
        "Валюта": "RUB",
    }


def _transfer_row(number: int, moment: str) -> dict[str, JsonValue]:
    return {
        "Перемещение": _entity_ref(
            "ДокументСсылка.ПеремещениеТоваров",
            number,
            f"Перемещение {number}",
        ),
        "Номер": f"ПМ-{number:04d}",
        "Дата": moment,
        "СкладОтправитель": _entity_ref(
            "СправочникСсылка.Склады", 3001, "Центральный склад"
        ),
        "СкладОтправительНаименование": "Центральный склад",
        "СкладПолучатель": _entity_ref("СправочникСсылка.Склады", 3002, "Торговый зал"),
        "СкладПолучательНаименование": "Торговый зал",
        "Статус": "К выполнению",
    }


def test_production_customer_resolver_feeds_exact_details_with_provenance() -> None:
    resolver = _load_skill("ut115.ref.customer.resolve-name-contains-1.0.0.skill.json")
    details = _load_skill("ut115.ref.customer.details-1.0.0.skill.json")
    snapshot_id = uuid4()
    catalog = PinnedCatalog.create(
        snapshot_id, 1, {resolver.skill_id: resolver, details.skill_id: details}
    )
    plan = _customer_plan(request_id=uuid4(), context_version=1, catalog=catalog)
    resolver_response, details_response, expected = _customer_rows(
        resolver, details, 1, "Альфа"
    )
    mcp = _QueueMcp([resolver_response, details_response])

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert len(mcp.requests) == 2
    assert mcp.requests[1].params["Клиент"] == expected
    assert len(execution.selection_proofs) == 1
    assert len(execution.context_facts) == 1
    context = execution.context_facts[0]
    assert context.semantic_type == "party.customer"
    assert context.slot_key == "selection.customer"
    assert context.origin.skill_id == resolver.skill_id
    assert context.origin.fact.fact_id == "customer.ref"
    assert context.origin.column == "Клиент"
    assert context.origin.accepted_mcp_types == ("СправочникСсылка.Партнеры",)
    assert execution.evidence.coverage.sufficient is True


@pytest.mark.parametrize(
    (
        "filename",
        "identity_fact_id",
        "rank_fact_id",
        "semantic_type",
        "slot_key",
        "rows",
        "winner_number",
    ),
    [
        (
            "ut115.purchase.receipt-list-1.0.0.skill.json",
            "receipt.ref",
            "receipt.date",
            "document.purchase_receipt",
            "selection.purchase_receipt",
            [
                _receipt_row(11, "2026-02-01T12:00:00+03:00", 100.0),
                _receipt_row(12, "2026-03-01T12:00:00+03:00", 200.0),
            ],
            12,
        ),
        (
            "ut115.logistics.transfer-list-1.0.0.skill.json",
            "transfer.ref",
            "transfer.date",
            "document.stock_transfer",
            "selection.stock_transfer",
            [
                _transfer_row(21, "2026-04-01T12:00:00+03:00"),
                _transfer_row(22, "2026-05-01T12:00:00+03:00"),
            ],
            22,
        ),
    ],
)
def test_two_production_document_producers_rank_exact_winner_and_context(
    filename: str,
    identity_fact_id: str,
    rank_fact_id: str,
    semantic_type: str,
    slot_key: str,
    rows: list[dict[str, JsonValue]],
    winner_number: int,
) -> None:
    producer = _load_skill(filename)
    catalog = PinnedCatalog.create(uuid4(), 1, {producer.skill_id: producer})
    plan = _document_rank_plan(
        skill=producer,
        catalog=catalog,
        identity_fact_id=identity_fact_id,
        rank_fact_id=rank_fact_id,
        semantic_type=semantic_type,
    )
    mcp = _QueueMcp([_envelope(producer, rows)])

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert len(mcp.requests) == 1
    assert len(execution.selection_proofs) == 1
    proof = execution.selection_proofs[0]
    assert proof.resolver.step_id == "s1"
    assert proof.selector_step_id == "s2"
    assert proof.selector_digest is not None
    assert len(execution.context_facts) == 1
    context = execution.context_facts[0]
    assert context.semantic_type == semantic_type
    assert context.slot_key == slot_key
    assert context.origin.skill_id == producer.skill_id
    assert context.origin.fact.fact_id == identity_fact_id
    assert isinstance(context.origin.fact.value, EntityRef)
    assert context.origin.fact.value.unique_id == UUID(
        f"00000000-0000-4000-8000-{winner_number:012d}"
    )
    rank_step = next(step for step in execution.evidence.steps if step.step_id == "s2")
    assert rank_step.source_kind == "deterministic_operator"
    assert rank_step.operation_ref == "operator:rank"
    assert rank_step.produced_fact_instance_ids == proof.fact_instance_ids
    exported = {item.fact_instance_id for item in execution.evidence.context_exports}
    assert exported == set(proof.fact_instance_ids)


def test_customer_metric_rank_producer_is_an_explicit_catalog_gap() -> None:
    production = [
        Skill.model_validate_json(path.read_bytes())
        for path in SKILLS.glob("*.skill.json")
    ]
    customer_metric_producers = [
        skill.skill_id
        for skill in production
        if any(
            fact.semantic_type == "party.customer" and fact.role == "entity"
            for fact in skill.output_contract.facts
        )
        and any(
            fact.role == "measure" and fact.required and not fact.nullable
            for fact in skill.output_contract.facts
        )
    ]
    assert customer_metric_producers == [], (
        "A real customer metric producer now exists; replace this gap sentinel "
        "with the mandatory party.customer rank workflow.",
        customer_metric_producers,
    )


def test_reference_package_survives_restart_and_reuses_context_without_search(
    tmp_path: Path,
) -> None:
    resolver = _load_skill("ut115.ref.customer.resolve-name-contains-1.0.0.skill.json")
    details = _load_skill("ut115.ref.customer.details-1.0.0.skill.json")
    resolver_response, details_response, expected = _customer_rows(
        resolver, details, 31, "Альфа"
    )
    first_mcp = _QueueMcp([resolver_response])

    def first_plan(request: PlannerRequest) -> PlannerOutput:
        shortlisted = {card.skill_id for card in request.skill_cards}
        assert CUSTOMER_RESOLVER in shortlisted
        catalog = first_runtime.catalog.pin()
        return _customer_resolution_plan(
            request_id=request.request_id,
            context_version=request.context_version,
            catalog=catalog,
        )

    first_planner = _PlannerQueue([first_plan])
    first_runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=False),
        planner=first_planner,
        one_c=first_mcp,
        auto_import=False,
    )
    try:
        empty_revision = first_runtime.catalog.pin().revision
        assert first_runtime.catalog.pin().skills == {}
        imported = first_runtime.catalog_service.import_package(
            REFERENCE_PACKAGE.read_bytes()
        )
        assert imported.revision == empty_revision + 1
        imported_digest = first_runtime.catalog.pin().digest
        session = first_runtime.store.create_session("Reference package restart")
        turn, created = first_runtime.chat.submit_message(
            session_id=session.session_id,
            text="Найди клиент Альфа",
            client_message_id="slice3-reference-first",
            expected_context_version=session.context_version,
        )
        assert created is True
        completed = asyncio.run(first_runtime.chat.process_turn(turn.turn_id))
        assert completed.status == "completed"
        assert completed.outcome == Outcome.SUCCESS_WITH_ROWS.value
        assert len(first_mcp.requests) == 1
        stored = first_runtime.store.context_facts(session.session_id)
        assert len(stored) == 1
        context_handle = stored[0].handle
        assert stored[0].semantic_type == "party.customer"
    finally:
        asyncio.run(first_runtime.close())

    second_mcp = _QueueMcp([details_response])

    def second_plan(request: PlannerRequest) -> PlannerOutput:
        assert {card.skill_id for card in request.skill_cards} >= {CUSTOMER_DETAILS}
        assert len(request.confirmed_facts) == 1
        confirmed = request.confirmed_facts[0]
        assert confirmed.handle == context_handle
        assert confirmed.semantic_type == "party.customer"
        catalog = restarted.catalog.pin()
        return _customer_plan(
            request_id=request.request_id,
            context_version=request.context_version,
            catalog=catalog,
            context_handle=context_handle,
        )

    second_planner = _PlannerQueue([second_plan])
    restarted = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=False),
        planner=second_planner,
        one_c=second_mcp,
        auto_import=False,
    )
    try:
        assert restarted.catalog.pin().digest == imported_digest
        restored_session = restarted.store.get_session(session.session_id)
        assert restored_session is not None
        assert restarted.store.context_facts(session.session_id)[0].handle == (
            context_handle
        )
        follow_up, created = restarted.chat.submit_message(
            session_id=session.session_id,
            text="Покажи реквизиты этого клиента",
            client_message_id="slice3-reference-follow-up",
            expected_context_version=restored_session.context_version,
        )
        assert created is True
        completed = asyncio.run(restarted.chat.process_turn(follow_up.turn_id))
        assert completed.status == "completed"
        assert completed.outcome == Outcome.SUCCESS_WITH_ROWS.value
        assert len(second_mcp.requests) == 1
        assert second_mcp.requests[0].params["Клиент"] == expected
        assert CUSTOMER_RESOLVER not in {
            step.skill_id
            for step in _customer_plan(
                request_id=uuid4(),
                context_version=restored_session.context_version,
                catalog=restarted.catalog.pin(),
                context_handle=context_handle,
            ).result.steps
            if hasattr(step, "skill_id")
        }
    finally:
        asyncio.run(restarted.close())


def test_ambiguity_stops_before_exact_customer_consumer() -> None:
    resolver = _load_skill("ut115.ref.customer.resolve-name-contains-1.0.0.skill.json")
    details = _load_skill("ut115.ref.customer.details-1.0.0.skill.json")
    catalog = PinnedCatalog.create(
        uuid4(), 1, {resolver.skill_id: resolver, details.skill_id: details}
    )
    plan = _customer_resolution_plan(
        request_id=uuid4(), context_version=1, catalog=catalog
    )
    first = _customer_rows(resolver, details, 41, "Альфа Москва")[0]
    second = _customer_rows(resolver, details, 42, "Альфа Казань")[0]
    rows = [*first.data, *second.data]
    mcp = _QueueMcp([_envelope(resolver, rows)])

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.CLARIFICATION_REQUIRED
    assert execution.pending_clarification is not None
    assert len(mcp.requests) == 1
    assert execution.selection_proofs == ()
    assert execution.context_facts == ()
    assert execution.evidence.context_exports == ()


def test_rank_tie_and_incomplete_input_fail_closed_without_selection() -> None:
    receipt = _load_skill("ut115.purchase.receipt-list-1.0.0.skill.json")
    receipt_catalog = PinnedCatalog.create(uuid4(), 1, {receipt.skill_id: receipt})
    receipt_plan = _document_rank_plan(
        skill=receipt,
        catalog=receipt_catalog,
        identity_fact_id="receipt.ref",
        rank_fact_id="receipt.date",
        semantic_type="document.purchase_receipt",
    )
    tied = _QueueMcp(
        [
            _envelope(
                receipt,
                [
                    _receipt_row(51, "2026-06-01T12:00:00+03:00", 100.0),
                    _receipt_row(52, "2026-06-01T12:00:00+03:00", 200.0),
                ],
            )
        ]
    )

    tie_execution = _execute(receipt_plan, receipt_catalog, tied)

    assert tie_execution.outcome is Outcome.CLARIFICATION_REQUIRED
    assert tie_execution.selection_proofs == ()
    assert tie_execution.context_facts == ()
    assert tie_execution.evidence.context_exports == ()
    assert len(tied.requests) == 1

    transfer = _load_skill("ut115.logistics.transfer-list-1.0.0.skill.json")
    transfer_catalog = PinnedCatalog.create(uuid4(), 1, {transfer.skill_id: transfer})
    transfer_plan = _document_rank_plan(
        skill=transfer,
        catalog=transfer_catalog,
        identity_fact_id="transfer.ref",
        rank_fact_id="transfer.date",
        semantic_type="document.stock_transfer",
    )
    incomplete = _QueueMcp(
        [
            _envelope(
                transfer,
                [_transfer_row(61, "2026-07-01T12:00:00+03:00")],
                has_more=True,
            )
        ]
    )

    try:
        incomplete_execution = _execute(transfer_plan, transfer_catalog, incomplete)
    except ApplicationError as error:
        assert error.code != "OPERATOR_NOT_IMPLEMENTED"
    else:
        assert incomplete_execution.outcome in {
            Outcome.PARTIAL,
            Outcome.CONTRACT_ERROR,
        }
        assert incomplete_execution.selection_proofs == ()
        assert incomplete_execution.context_facts == ()
        assert incomplete_execution.evidence.context_exports == ()
    assert len(incomplete.requests) == 1
