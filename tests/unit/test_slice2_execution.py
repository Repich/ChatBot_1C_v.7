from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ExecutionContext,
    PlanExecutor,
    StepResult,
    _empty_reason_for_outcome,
)
from chatbot1c.application.models import (
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    HelpSearchRequest,
    MetadataEnvelope,
    PageContinuation,
    PinnedCatalog,
)
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.contracts.serialization import wire_dict
from chatbot1c.domain.evidence import DatabaseStateMarker, EvidenceBundle
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import CountOperator, PlannerOutput, SkillCall, SystemBinding
from chatbot1c.domain.skill import DataQueryOperation, McpFixture, Skill

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "skills/ut-11.5.27.56/ut.starter.slice-two.package.json"


class QueueMcp:
    def __init__(self, responses: list[ExecuteQueryEnvelope]) -> None:
        self.responses = list(responses)
        self.requests: list[ExecuteQueryRequest] = []

    async def execute_query(
        self, request: ExecuteQueryRequest
    ) -> ExecuteQueryEnvelope:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected MCP call")
        return self.responses.pop(0)

    async def get_metadata(self, request: object) -> MetadataEnvelope:
        del request
        raise AssertionError("metadata is not used by execution tests")


class EmptyDocumentation:
    async def search(self, request: HelpSearchRequest) -> tuple[object, ...]:
        del request
        return ()


class MemoryTraces:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, str], bytes] = {}

    def put_artifact(self, trace_id: UUID, name: str, content: bytes) -> None:
        self.items[(trace_id, name)] = content

    def artifacts(self, trace_id: UUID) -> dict[str, bytes]:
        return {
            name: content
            for (stored_trace, name), content in self.items.items()
            if stored_trace == trace_id
        }


def _package() -> SkillPackage:
    document = ContractHarness.discover(ROOT).validate_json_bytes(PACKAGE.read_bytes())
    assert isinstance(document, SkillPackage)
    return document


def _catalog() -> PinnedCatalog:
    package = _package()
    return PinnedCatalog.create(
        uuid4(), 2, {skill.skill_id: skill for skill in package.skills}
    )


def _marker(catalog: PinnedCatalog, digest: str = "a" * 64) -> DatabaseStateMarker:
    return DatabaseStateMarker(
        marker_id=uuid4(),
        algorithm="sha256",
        scope="acceptance_observable_state",
        digest=digest,
        captured_at=datetime.now(UTC),
        profile_version="1.0.0",
        acceptance_suite_version="q001-q116-v1",
        configuration_revision="11.5.27.56",
        configuration_profile_digest="b" * 64,
        catalog_revision=catalog.revision,
        catalog_snapshot_digest=catalog.digest,
        documentation_revision="fixture",
        documentation_manifest_digest="c" * 64,
        projection_manifest_digest="d" * 64,
    )


def _context(catalog: PinnedCatalog, marker: DatabaseStateMarker) -> ExecutionContext:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    return ExecutionContext(
        trace_id=uuid4(),
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        turn_time=now,
        default_list_limit=20,
        catalog=catalog,
        context_facts=(),
        database_state_marker=marker,
        deadline_at=now + timedelta(seconds=90),
    )


def _executor(mcp: QueueMcp) -> PlanExecutor:
    return PlanExecutor(mcp, EmptyDocumentation(), MemoryTraces())  # type: ignore[arg-type]


def _fixture_envelope(skill: Skill, test_index: int = 0) -> ExecuteQueryEnvelope:
    fixture = skill.tests[test_index].fixture
    assert isinstance(fixture, McpFixture)
    return ExecuteQueryEnvelope.model_validate(fixture.response)


def _item_rows(skill: Skill, count: int) -> list[dict[str, JsonValue]]:
    template = _fixture_envelope(skill).data[0]
    rows: list[dict[str, JsonValue]] = []
    for index in range(1, count + 1):
        row = copy.deepcopy(template)
        ref = copy.deepcopy(row["Номенклатура"])
        assert isinstance(ref, dict)
        ref["УникальныйИдентификатор"] = f"00000000-0000-4000-8000-{index:012d}"
        ref["Представление"] = f"Одинаковый товар {index:04d}"
        row["Номенклатура"] = ref
        row["Код"] = f"{index:09d}"
        row["Артикул"] = f"A-{index:04d}"
        row["Наименование"] = "Одинаковое наименование"
        rows.append(row)
    return rows


def _envelope(skill: Skill, rows: list[dict[str, JsonValue]]) -> ExecuteQueryEnvelope:
    fixture = _fixture_envelope(skill)
    return fixture.model_copy(update={"data": tuple(rows), "count": len(rows)})


def _call(skill: Skill) -> SkillCall:
    return SkillCall.model_validate(
        {
            "step_id": "s1",
            "kind": "skill_call",
            "skill_id": skill.skill_id,
            "skill_version": skill.version,
            "arguments": [],
            "required_output_fact_ids": [
                fact.fact_id for fact in skill.output_contract.facts if fact.required
            ],
            "on_empty": "stop_not_found",
        }
    )


def _continuation(
    context: ExecutionContext,
    skill: Skill,
    result: StepResult,
) -> PageContinuation:
    draft = result.continuation
    assert draft is not None
    now = datetime.now(UTC)
    return PageContinuation(
        handle="page_" + "A" * 32,
        session_id=context.session_id,
        origin_turn_id=context.turn_id,
        step_id=draft.step_id,
        skill_id=draft.skill_id,
        skill_version=draft.skill_version,
        skill_digest=draft.skill_digest,
        catalog_snapshot_id=context.catalog.snapshot_id,
        catalog_revision=context.catalog.revision,
        normalized_params_digest="0" * 64,
        arguments=draft.arguments,
        plan_json="{}",
        strategy=draft.strategy,
        page_size=draft.page_size,
        shown=draft.cumulative_shown,
        database_marker=context.database_state_marker.digest,
        sort_tuple=draft.sort_tuple,
        cursor_values=draft.cursor_values,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )


@pytest.mark.parametrize(
    ("row_count", "expected_rows", "has_more"),
    [(0, 0, False), (19, 19, False), (20, 20, False), (21, 20, True)],
)
def test_keyset_page_size_plus_one_boundaries(
    row_count: int, expected_rows: int, has_more: bool
) -> None:
    catalog = _catalog()
    skill = catalog.skills["ut115.ref.item.resolve-name-contains"]
    assert isinstance(skill.operation, DataQueryOperation)
    assert "ПЕРВЫЕ 20" not in skill.operation.query_template.text.upper()
    mcp = QueueMcp([_envelope(skill, _item_rows(skill, row_count))])
    context = _context(catalog, _marker(catalog))
    result = asyncio.run(
        _executor(mcp)._execute_data(  # noqa: SLF001
            _call(skill),
            skill,
            {"name_fragment": "товар"},
            context,
            datetime.now(UTC),
        )
    )

    assert mcp.requests[0].limit == 21
    assert result.row_count == expected_rows
    assert result.has_more is has_more
    assert (result.continuation is not None) is has_more
    assert result.outcome is (
        Outcome.SUCCESS_EMPTY if row_count == 0 else Outcome.SUCCESS_WITH_ROWS
    )


def test_keyset_43_tied_rows_has_no_duplicate_and_uses_local_pages() -> None:
    catalog = _catalog()
    skill = catalog.skills["ut115.ref.item.resolve-name-contains"]
    all_rows = _item_rows(skill, 43)
    mcp = QueueMcp(
        [
            _envelope(skill, all_rows[:21]),
            _envelope(skill, all_rows[20:41]),
            _envelope(skill, all_rows[40:]),
        ]
    )
    context = _context(catalog, _marker(catalog))
    executor = _executor(mcp)
    call = _call(skill)
    pages: list[StepResult] = []
    continuation: PageContinuation | None = None
    for _ in range(3):
        result = asyncio.run(
            executor._execute_data(  # noqa: SLF001
                call,
                skill,
                {"name_fragment": "товар"},
                context,
                datetime.now(UTC),
                continuation=continuation,
            )
        )
        pages.append(result)
        continuation = (
            _continuation(context, skill, result)
            if result.continuation is not None
            else None
        )
    refs = [
        str(fact.value.unique_id)
        for page in pages
        for fact in page.facts
        if fact.fact_id == "item.ref" and hasattr(fact.value, "unique_id")
    ]
    assert [page.row_count for page in pages] == [20, 20, 3]
    assert [request.limit for request in mcp.requests] == [21, 21, 21]
    assert mcp.requests[0].params["ЕстьКурсор"] is False
    assert mcp.requests[0].params["ИмяКурсора"] is None
    assert mcp.requests[0].params["СсылкаКурсора"] is None
    assert mcp.requests[1].params["ЕстьКурсор"] is True
    assert mcp.requests[1].params["ИмяКурсора"] == "Одинаковое наименование"
    assert mcp.requests[1].params["СсылкаКурсора"] == all_rows[19]["Номенклатура"]
    assert mcp.requests[2].params["ЕстьКурсор"] is True
    assert mcp.requests[2].params["ИмяКурсора"] == "Одинаковое наименование"
    assert mcp.requests[2].params["СсылкаКурсора"] == all_rows[39]["Номенклатура"]
    assert len(refs) == len(set(refs)) == 43
    assert continuation is None


def test_barcode_distinct_projection_prevents_duplicate_item_identity() -> None:
    catalog = _catalog()
    skill = catalog.skills["ut115.ref.item.resolve-barcode-exact"]
    assert isinstance(skill.operation, DataQueryOperation)
    assert skill.operation.query_template.text.startswith("ВЫБРАТЬ РАЗЛИЧНЫЕ\n")
    duplicate = copy.deepcopy(_fixture_envelope(skill).data[0])
    mcp = QueueMcp([_envelope(skill, [duplicate, copy.deepcopy(duplicate)])])

    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(
            _executor(mcp)._execute_data(  # noqa: SLF001
                _call(skill),
                skill,
                {"barcode": "4600000000001"},
                _context(catalog, _marker(catalog)),
                datetime.now(UTC),
            )
        )

    assert rejected.value.code == "RESULT_ROW_IDENTITY_DUPLICATE"


def test_required_empty_producers_cannot_publish_conflicting_reasons() -> None:
    catalog = _catalog()
    skill = catalog.skills["ut115.ref.item.resolve-name-contains"]
    now = datetime.now(UTC)
    results = (
        StepResult(
            "s1",
            skill,
            Outcome.SUCCESS_EMPTY,
            (),
            (),
            0,
            now,
            now,
            1,
            empty_reason="not_found",
        ),
        StepResult(
            "s2",
            skill,
            Outcome.SUCCESS_EMPTY,
            (),
            (),
            0,
            now,
            now,
            1,
            empty_reason="no_rows",
        ),
    )

    with pytest.raises(ApplicationError) as rejected:
        _empty_reason_for_outcome(results, Outcome.SUCCESS_EMPTY)

    assert rejected.value.code == "EMPTY_REASON_CONFLICT"


def test_terminal_keyset_page_does_not_satisfy_complete_set_obligation() -> None:
    base_catalog = _catalog()
    base_skill = base_catalog.skills["ut115.ref.item.resolve-name-contains"]
    sufficiency = base_skill.output_contract.sufficiency.model_copy(
        update={"truncation_policy": "partial_until_all_pages"}
    )
    output_contract = base_skill.output_contract.model_copy(
        update={"sufficiency": sufficiency}
    )
    skill = base_skill.model_copy(update={"output_contract": output_contract})
    skills = dict(base_catalog.skills)
    skills[skill.skill_id] = skill
    catalog = PinnedCatalog.create(uuid4(), 3, skills)
    plan = _plan(
        catalog,
        requirements=[
            {
                "requirement_id": "r1",
                "semantic_type": "catalog.item",
                "value_type": "entity_ref",
                "cardinality": "many",
                "required": True,
            }
        ],
        steps=[
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": skill.skill_id,
                "skill_version": skill.version,
                "arguments": [
                    {
                        "parameter": "name_fragment",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "товар",
                        },
                    }
                ],
                "required_output_fact_ids": [
                    fact.fact_id
                    for fact in skill.output_contract.facts
                    if fact.required
                ],
                "on_empty": "stop_not_found",
            }
        ],
        finals=[{"step_id": "s1", "fact_id": "item.ref"}],
    )
    rows = _item_rows(skill, 23)
    mcp = QueueMcp(
        [
            _envelope(skill, rows[:21]),
            _envelope(skill, rows[20:]),
        ]
    )
    context = _context(catalog, _marker(catalog))
    executor = _executor(mcp)
    first = asyncio.run(executor.execute(plan, context))
    assert first.outcome is Outcome.PARTIAL
    assert first.evidence.coverage.requirements[0].status is CoverageStatus.COVERED
    assert first.evidence.coverage.sufficient is False

    continuation = _continuation(context, skill, first.steps[0])
    next_context = replace(
        context,
        trace_id=uuid4(),
        request_id=uuid4(),
        turn_id=uuid4(),
    )
    terminal = asyncio.run(
        executor.execute_continuation(plan, next_context, continuation)
    )

    assert terminal.steps[0].has_more is False
    assert terminal.steps[0].collection_scope == "visible_page"
    assert terminal.evidence.coverage.requirements[0].status is CoverageStatus.COVERED
    assert terminal.evidence.coverage.sufficient is False
    assert terminal.outcome is Outcome.PARTIAL

    payload = wire_dict(terminal.evidence)
    validated = ContractHarness.discover(ROOT).validate_document(
        payload,
        available_skills=tuple(catalog.skills.values()),
        verify_integrity=False,
    )
    assert isinstance(validated, EvidenceBundle)
    assert validated.schema_version == "1.1.0"


def _plan(
    catalog: PinnedCatalog,
    *,
    steps: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    finals: list[dict[str, str]],
) -> PlannerOutput:
    return PlannerOutput.model_validate(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(uuid4()),
            "session_context_version": 1,
            "catalog_snapshot_id": str(catalog.snapshot_id),
            "catalog_revision": catalog.revision,
            "decision": "execute",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": "Проверить generic composition.",
                "required_facts": requirements,
                "slots": [],
            },
            "result": {
                "kind": "execute",
                "plan_id": str(uuid4()),
                "steps": steps,
                "final_outputs": finals,
            },
        }
    )


def _q054_plan(catalog: PinnedCatalog) -> PlannerOutput:
    warehouse = catalog.skills["ut115.ref.warehouse.resolve"]
    stock = catalog.skills["ut115.stock.balance"]
    return _plan(
        catalog,
        requirements=[
            {
                "requirement_id": "r1",
                "semantic_type": "measure.stock_balance",
                "value_type": "quantity",
                "cardinality": "many",
                "required": True,
                "unit_dimension": "quantity_unit",
                "time_semantics": "moment",
            }
        ],
        steps=[
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": warehouse.skill_id,
                "skill_version": warehouse.version,
                "arguments": [
                    {
                        "parameter": "retail_only",
                        "binding": {
                            "source": "literal",
                            "value_type": "boolean",
                            "value": True,
                        },
                    }
                ],
                "required_output_fact_ids": [
                    "warehouse.ref",
                    "warehouse.name",
                    "warehouse.type",
                ],
                "on_empty": "stop_not_found",
            },
            {
                "step_id": "s2",
                "kind": "skill_call",
                "skill_id": stock.skill_id,
                "skill_version": stock.version,
                "arguments": [
                    {
                        "parameter": "warehouses",
                        "binding": {
                            "source": "step",
                            "step_id": "s1",
                            "fact_id": "warehouse.ref",
                            "cardinality": "many",
                        },
                    },
                    {
                        "parameter": "moment",
                        "binding": {"source": "system", "name": "turn_time"},
                    },
                ],
                "required_output_fact_ids": [
                    fact.fact_id for fact in stock.output_contract.facts
                ],
                "on_empty": "stop_not_found",
            },
        ],
        finals=[{"step_id": "s2", "fact_id": "stock.balance"}],
    )


def test_q054_generic_dag_defaults_and_foreign_warehouse_rejection() -> None:
    catalog = _catalog()
    warehouse = catalog.skills["ut115.ref.warehouse.resolve"]
    stock = catalog.skills["ut115.stock.balance"]
    plan = _q054_plan(catalog)
    ContractHarness.discover(ROOT).semantics.validate(
        plan, available_skills=tuple(catalog.skills.values())
    )
    warehouse_response = _fixture_envelope(warehouse)
    stock_response = _fixture_envelope(stock, 1)
    mcp = QueueMcp([warehouse_response, stock_response])
    context = _context(catalog, _marker(catalog))
    execution = asyncio.run(_executor(mcp).execute(plan, context))

    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert mcp.requests[0].params == {
        "Шаблон": "%%",
        "ТолькоРозничные": True,
        "Подразделение": None,
        "ЕстьКурсор": False,
        "ИмяКурсора": None,
        "СсылкаКурсора": None,
    }
    produced_warehouse = warehouse_response.data[0]["Склад"]
    assert mcp.requests[1].params == {
        "Номенклатура": None,
        "Склады": [produced_warehouse],
        "Момент": context.turn_time.isoformat(),
        "ЕстьКурсор": False,
        "СкладКурсора": None,
        "ПомещениеКурсора": None,
        "НоменклатураКурсора": None,
        "ХарактеристикаКурсора": None,
        "НазначениеКурсора": None,
    }

    foreign = stock_response.model_dump(mode="json", by_alias=True)
    foreign_ref = foreign["data"][0]["Склад"]
    foreign_ref["УникальныйИдентификатор"] = "00000000-0000-4000-8000-000000009999"
    rejected_mcp = QueueMcp(
        [warehouse_response, ExecuteQueryEnvelope.model_validate(foreign)]
    )
    rejected = asyncio.run(_executor(rejected_mcp).execute(plan, context))
    assert rejected.outcome is Outcome.CONTRACT_ERROR
    assert rejected.context_facts == ()
    assert any(
        error.code == "ENTITY_REF_BINDING_MISMATCH"
        for error in rejected.evidence.errors
    )


def _count_plan(catalog: PinnedCatalog) -> PlannerOutput:
    item = catalog.skills["ut115.ref.item.resolve-name-contains"]
    return _plan(
        catalog,
        requirements=[
            {
                "requirement_id": "r1",
                "semantic_type": "measure.count",
                "value_type": "integer",
                "cardinality": "aggregate",
                "required": True,
            }
        ],
        steps=[
            {
                "step_id": "s1",
                "kind": "skill_call",
                "skill_id": item.skill_id,
                "skill_version": item.version,
                "arguments": [
                    {
                        "parameter": "name_fragment",
                        "binding": {
                            "source": "literal",
                            "value_type": "normalized_text",
                            "value": "куртка",
                        },
                    }
                ],
                "required_output_fact_ids": ["item.ref", "item.code", "item.name"],
                "on_empty": "continue_as_empty_set",
            },
            {
                "step_id": "s2",
                "kind": "operator_call",
                "operator": "count",
                "input_step_id": "s1",
                "distinct_by_fact_ids": ["item.ref"],
                "result_fact_id": "item.count",
            },
        ],
        finals=[{"step_id": "s2", "fact_id": "item.count"}],
    )


def test_count_operator_rejects_page_scoped_total_before_mcp() -> None:
    catalog = _catalog()
    item = catalog.skills["ut115.ref.item.resolve-name-contains"]
    plan = _count_plan(catalog)
    with pytest.raises(ContractValidationError) as rejected_plan:
        ContractHarness.discover(ROOT).semantics.validate(
            plan, available_skills=tuple(catalog.skills.values())
        )
    assert "PLAN_COUNT_SCOPE_MISMATCH" in {
        issue.code for issue in rejected_plan.value.issues
    }

    context = _context(catalog, _marker(catalog))
    count_step = next(
        step for step in plan.result.steps if isinstance(step, CountOperator)
    )
    source = StepResult(
        "s1",
        item,
        Outcome.SUCCESS_WITH_ROWS,
        (),
        (),
        20,
        datetime.now(UTC),
        datetime.now(UTC),
        1,
        collection_scope="visible_page",
    )
    with pytest.raises(ApplicationError) as rejected:
        _executor(QueueMcp([]))._execute_count(  # noqa: SLF001
            count_step, context, {"s1": source}
        )
    assert rejected.value.code == "OPERATOR_COLLECTION_SCOPE_MISMATCH"


def test_runtime_exported_warehouse_resolver_uses_keyset(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        auto_import=True,
    )
    exported = json.loads(
        runtime.catalog_service.export_skill("ut115.ref.warehouse.resolve")
    )
    pagination = exported["operation"]["pagination"]

    assert pagination["strategy"] == "keyset"
    assert [item["fact_id"] for item in pagination["sort"]] == [
        "warehouse.name",
        "warehouse.ref",
    ]
    assert "&ЕстьКурсор" in exported["operation"]["query_template"]["text"]
    asyncio.run(runtime.close())


def test_database_marker_system_binding_uses_captured_provider_digest() -> None:
    catalog = _catalog()
    marker = _marker(catalog, digest="e" * 64)
    context = _context(catalog, marker)
    executor = _executor(QueueMcp([]))
    resolved = executor._resolve_binding(  # noqa: SLF001
        _q054_plan(catalog),
        SystemBinding(source="system", name="database_state_marker"),
        context,
        {},
        {},
    )
    assert resolved.value == marker.digest
    assert canonicalize(resolved.value) != canonicalize(str(catalog.snapshot_id))
