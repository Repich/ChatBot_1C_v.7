from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Callable, cast
from uuid import UUID, uuid4

from chatbot1c.application.chat import (
    _apply_context_binding_choice,
    _prepare_context_bindings,
)
from chatbot1c.application.models import ContextFact, PinnedCatalog
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import ContextBinding, PlannerOutput, SkillCall
from chatbot1c.domain.skill import FactValueType

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_BYTES = cast(
    Callable[[], bytes],
    run_path(str(ROOT / "tests/acceptance_slice3/synthetic_package.py"))[
        "package_bytes"
    ],
)


def _catalog() -> PinnedCatalog:
    document = ContractHarness.discover(ROOT).validate_json_bytes(PACKAGE_BYTES())
    assert isinstance(document, SkillPackage)
    return PinnedCatalog.create(
        uuid4(), 1, {skill.skill_id: skill for skill in document.skills}
    )


def _plan(catalog: PinnedCatalog, handle: str) -> PlannerOutput:
    skill = catalog.skills["qa.synthetic.asset.snapshot"]
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
                "goal_ru": "Проверить серверный выбор context binding.",
                "required_facts": [],
                "slots": [],
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
                                "parameter": "asset",
                                "binding": {
                                    "source": "context",
                                    "context_handle": handle,
                                    "expected_semantic_type": "synthetic.asset",
                                },
                            },
                            {
                                "parameter": "moment",
                                "binding": {
                                    "source": "literal",
                                    "value_type": "datetime",
                                    "value": "2037-11-19T08:17:43+03:00",
                                },
                            },
                        ],
                        "required_output_fact_ids": [
                            fact.fact_id
                            for fact in skill.output_contract.facts
                            if fact.required
                        ],
                        "on_empty": "stop_not_found",
                    }
                ],
                "final_outputs": [
                    {"step_id": "s1", "fact_id": "snapshot.value"}
                ],
            },
        }
    )


def _context(handle: str, presentation: str) -> ContextFact:
    return ContextFact.model_construct(
        handle=handle,
        semantic_type="synthetic.asset",
        value={"opaque": presentation},
        presentation=presentation,
        origin_turn_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        origin_fact_instance_id=uuid4(),
        origin=None,
        slot_key="selection.synthetic_asset",
        value_type=FactValueType.ENTITY_REF,
        policy_mode="selected_only",
        cardinality="one",
        member_index=0,
        lifetime_mode="session",
        expires_at=None,
    )


def _asset_binding(plan: PlannerOutput) -> ContextBinding:
    assert plan.result.kind == "execute"
    step = plan.result.steps[0]
    assert isinstance(step, SkillCall)
    binding = next(
        argument.binding for argument in step.arguments if argument.parameter == "asset"
    )
    assert isinstance(binding, ContextBinding)
    return binding


def test_zero_compatible_contexts_requires_reproducible_clarification() -> None:
    catalog = _catalog()
    plan = _plan(catalog, "ctx_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    prepared, decision = _prepare_context_bindings(plan, catalog, ())
    assert prepared == plan
    assert decision is not None
    assert decision.step_id == "s1"
    assert decision.parameter == "asset"
    assert decision.choices == ()
    assert decision.question_ru == (
        "Уточните значение «Актив»: в активном контексте нет подходящего "
        "подтвержденного выбора."
    )


def test_multiple_compatible_contexts_require_typed_choice_and_exact_resume() -> None:
    catalog = _catalog()
    plan = _plan(catalog, "ctx_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    contexts = (
        _context("ctx_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "Первый выбор"),
        _context("ctx_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC", "Второй выбор"),
    )
    prepared, decision = _prepare_context_bindings(plan, catalog, contexts)
    assert prepared == plan
    assert decision is not None
    assert [choice.label_ru for choice in decision.choices] == [
        "Второй выбор",
        "Первый выбор",
    ] or [choice.label_ru for choice in decision.choices] == [
        "Первый выбор",
        "Второй выбор",
    ]
    assert all(choice.target_step_id == "s1" for choice in decision.choices)
    assert all(choice.target_parameter == "asset" for choice in decision.choices)
    chosen = next(
        choice for choice in decision.choices if choice.label_ru == "Второй выбор"
    )
    resumed, target = _apply_context_binding_choice(prepared, chosen)
    assert target == ("s1", "asset")
    assert _asset_binding(resumed).context_handle == (
        "ctx_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
    )
    final, repeated = _prepare_context_bindings(
        resumed, catalog, contexts, pinned={target}
    )
    assert repeated is None
    assert _asset_binding(final) == _asset_binding(resumed)


def test_one_compatible_context_is_selected_by_contract_not_model_handle() -> None:
    catalog = _catalog()
    plan = _plan(catalog, "ctx_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    actual = "ctx_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
    prepared, decision = _prepare_context_bindings(
        plan, catalog, (_context(actual, "Единственный выбор"),)
    )
    assert decision is None
    assert _asset_binding(prepared).context_handle == actual
