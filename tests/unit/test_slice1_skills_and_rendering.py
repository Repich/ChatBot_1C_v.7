from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from chatbot1c.application.execution import (
    ExecutionResult,
    StepResult,
    _encode_parameter,
)
from chatbot1c.application.models import PinnedCatalog
from chatbot1c.application.rendering import render_execution, render_table
from chatbot1c.application.shortlist import LexicalSkillShortlist
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.evidence import Fact, SourceLocator, UnitNotApplicable
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import DataQueryOperation, FactValueType

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "skills/ut-11.5.27.56/ut.starter.slice-one.package.json"


def _package() -> SkillPackage:
    document = ContractHarness.discover(ROOT).validate_json_bytes(PACKAGE.read_bytes())
    assert isinstance(document, SkillPackage)
    return document


def test_slice_one_package_is_frozen_baseline_artifact() -> None:
    payload = PACKAGE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "4de313a696cf9bff478746b5d0fe9e779948b090ac3f277785c2f4818df01420"
    )
    assert len(_package().skills) == 8


def test_starter_catalog_uses_canonical_ids_and_exact_query_variants() -> None:
    package = _package()
    skills = {skill.skill_id: skill for skill in package.skills}
    assert set(skills) == {
        "ut115.doc.term",
        "ut115.ref.item.resolve-article-exact",
        "ut115.ref.item.resolve-code-exact",
        "ut115.ref.item.resolve-barcode-exact",
        "ut115.ref.item.resolve-name-contains",
        "ut115.stock.balance",
        "ut115.sales.order-header-status-by-number",
        "ut115.sales.order-lines",
    }

    article = skills["ut115.ref.item.resolve-article-exact"]
    name = skills["ut115.ref.item.resolve-name-contains"]
    assert isinstance(article.operation, DataQueryOperation)
    assert isinstance(name.operation, DataQueryOperation)
    assert "Артикул = &Артикул" in article.operation.query_template.text
    assert "Артикул ПОДОБНО" not in article.operation.query_template.text
    assert 'ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"' in name.operation.query_template.text
    assert _encode_parameter("A%_~[]^B", "like_contains") == "%A~%~_~~~[~]~^B%"

    header_capabilities = set(
        skills["ut115.sales.order-header-status-by-number"].provides.capability_ids
    )
    assert header_capabilities == {"CAP-COMMON-ENTITY", "CAP-SALES-ORDER-STATUS"}
    assert "CAP-STOCK-BALANCE" in skills["ut115.stock.balance"].provides.capability_ids


def test_shortlist_is_lexical_and_keeps_composite_dependency_inside_limit() -> None:
    package = _package()
    base = package.skills[1]
    canonical = {skill.skill_id: skill for skill in package.skills}
    decoys = {
        f"ut115.synthetic.decoy-{index:02d}": base.model_copy(
            update={"skill_id": f"ut115.synthetic.decoy-{index:02d}"}
        )
        for index in range(20)
    }
    catalog = PinnedCatalog.create(uuid4(), 2, {**decoys, **canonical})
    selected = LexicalSkillShortlist().select(
        question="Какие строки и товары входят в этот заказ?",
        context=(),
        catalog=catalog,
        limit=16,
    )
    selected_ids = {skill.skill_id for skill in selected}
    assert len(selected) <= 16
    assert "ut115.sales.order-lines" in selected_ids
    assert "ut115.sales.order-header-status-by-number" in selected_ids

    article_selected = LexicalSkillShortlist().select(
        question="Найди номенклатуру с точным артикулом V100123588",
        context=(),
        catalog=catalog,
        limit=4,
    )
    assert "ut115.ref.item.resolve-article-exact" in {
        skill.skill_id for skill in article_selected
    }


def test_generic_renderer_uses_russian_titles_and_echoes_safe_empty_filter() -> None:
    fact = Fact(
        fact_instance_id=uuid4(),
        row_id="row_render0001",
        fact_id="item.name",
        semantic_type="catalog.item.name",
        value_type=FactValueType.STRING,
        value="Тестовый товар",
        confirmation="confirmed",
        step_id="s1",
        source_locator=SourceLocator(kind="query_column_binding", reference="Имя"),
        unit=UnitNotApplicable(mode="not_applicable"),
    )
    rendered = render_table([fact], titles={"item.name": "Наименование"})
    assert rendered.startswith("Наименование\n")
    assert "item.name" not in rendered

    skill = next(
        item
        for item in _package().skills
        if item.skill_id == "ut115.ref.item.resolve-article-exact"
    )
    plan = PlannerOutput.model_validate(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(uuid4()),
            "session_context_version": 1,
            "catalog_snapshot_id": str(uuid4()),
            "catalog_revision": 2,
            "decision": "execute",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": "Найти товар по точному артикулу.",
                "required_facts": [],
                "slots": [
                    {
                        "slot_id": "article",
                        "semantic_type": "catalog.item.article",
                        "value_type": "string",
                        "status": "resolved_literal",
                        "mentions": ["TEST-NOT-EXISTS-999999"],
                        "binding": {
                            "source": "literal",
                            "value_type": "string",
                            "value": "TEST-NOT-EXISTS-999999",
                        },
                    }
                ],
            },
            "result": {
                "kind": "execute",
                "plan_id": str(uuid4()),
                "steps": [
                    {
                        "step_id": "s1",
                        "kind": "skill_call",
                        "skill_id": skill.skill_id,
                        "skill_version": skill.version,
                        "arguments": [
                            {
                                "parameter": "article",
                                "binding": {"source": "slot", "slot_id": "article"},
                            }
                        ],
                        "required_output_fact_ids": ["item.ref"],
                        "on_empty": "stop_not_found",
                    }
                ],
                "final_outputs": [{"step_id": "s1", "fact_id": "item.ref"}],
            },
        }
    )
    now = datetime.now(UTC)
    step = StepResult(
        "s1", skill, Outcome.SUCCESS_EMPTY, (), (), 0, now, now, 1
    )
    execution = ExecutionResult(
        outcome=Outcome.SUCCESS_EMPTY,
        evidence=SimpleNamespace(facts=()),  # type: ignore[arg-type]
        context_facts=(),
        steps=(step,),
    )
    empty = render_execution(plan, execution)
    assert "TEST-NOT-EXISTS-999999" in empty
    assert "Запрос выполнен успешно" in empty
    assert "query" not in empty.casefold()
