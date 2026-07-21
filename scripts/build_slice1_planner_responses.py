"""Generate deterministic planner outputs used by the fixture E2E runner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/chatbot1c/resources/slice1-planner-responses.json"
ECHO = {
    "schema_version": "1.0.0",
    "document_type": "planner_output",
    "request_id": "00000000-0000-4000-8000-000000009001",
    "session_context_version": 1,
    "catalog_snapshot_id": "00000000-0000-4000-8000-000000009002",
    "catalog_revision": 2,
}


def _literal_slot(
    slot_id: str, semantic_type: str, value: str
) -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "semantic_type": semantic_type,
        "value_type": "string",
        "status": "resolved_literal",
        "mentions": [value],
        "binding": {
            "source": "literal",
            "value_type": "string",
            "value": value,
        },
    }


def _execute(
    *,
    intent: str,
    goal: str,
    requirements: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    skill_id: str,
    arguments: list[dict[str, Any]],
    required_outputs: list[str],
    final_outputs: list[str],
    plan_number: int,
) -> dict[str, Any]:
    return {
        **ECHO,
        "decision": "execute",
        "interpretation": {
            "intent_kind": intent,
            "goal_ru": goal,
            "required_facts": requirements,
            "slots": slots,
        },
        "result": {
            "kind": "execute",
            "plan_id": f"00000000-0000-4000-8000-{plan_number:012d}",
            "steps": [
                {
                    "step_id": "s1",
                    "kind": "skill_call",
                    "skill_id": skill_id,
                    "skill_version": "1.0.0",
                    "arguments": arguments,
                    "required_output_fact_ids": required_outputs,
                    "on_empty": "stop_not_found",
                }
            ],
            "final_outputs": [
                {"step_id": "s1", "fact_id": fact_id}
                for fact_id in final_outputs
            ],
        },
    }


def _requirement(
    requirement_id: str,
    semantic_type: str,
    value_type: str,
    cardinality: str,
    *,
    unit_dimension: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requirement_id": requirement_id,
        "semantic_type": semantic_type,
        "value_type": value_type,
        "cardinality": cardinality,
        "required": True,
    }
    if unit_dimension is not None:
        result["unit_dimension"] = unit_dimension
    return result


def build() -> dict[str, dict[str, Any]]:
    q001 = _execute(
        intent="documentation",
        goal="Найти определение термина во встроенной справке УТ.",
        requirements=[
            _requirement(
                "r1", "documentation.fragment", "document_fragment", "many"
            )
        ],
        slots=[
            _literal_slot(
                "search_text", "documentation.search_text", "Что такое заказ клиента в УТ?"
            )
        ],
        skill_id="ut115.doc.term",
        arguments=[
            {
                "parameter": "search_text",
                "binding": {"source": "slot", "slot_id": "search_text"},
            }
        ],
        required_outputs=["documentation.fragment", "documentation.citation"],
        final_outputs=["documentation.fragment"],
        plan_number=9101,
    )
    q011 = _execute(
        intent="data",
        goal="Найти номенклатуру по точному артикулу.",
        requirements=[
            _requirement("r1", "catalog.item", "entity_ref", "many")
        ],
        slots=[_literal_slot("article", "catalog.item.article", "V100123588")],
        skill_id="ut115.ref.item.resolve-article-exact",
        arguments=[
            {
                "parameter": "article",
                "binding": {"source": "slot", "slot_id": "article"},
            }
        ],
        required_outputs=["item.ref", "item.code", "item.name"],
        final_outputs=["item.ref"],
        plan_number=9111,
    )
    q036 = _execute(
        intent="data",
        goal="Найти заказ по точному номеру и показать его статус.",
        requirements=[
            _requirement("r1", "document.sales_order", "entity_ref", "zero_or_one"),
            _requirement(
                "r2", "document.sales_order.status", "string", "zero_or_one"
            ),
        ],
        slots=[
            _literal_slot("document_number", "document.number", "0000-000005")
        ],
        skill_id="ut115.sales.order-header-status-by-number",
        arguments=[
            {
                "parameter": "document_number",
                "binding": {"source": "slot", "slot_id": "document_number"},
            }
        ],
        required_outputs=["order.ref", "order.status"],
        final_outputs=["order.ref", "order.status"],
        plan_number=9136,
    )
    context_binding = {
        "source": "context",
        "context_handle": "ctx_DYNAMICORDER0000000001",
        "expected_semantic_type": "document.sales_order",
    }
    q037 = _execute(
        intent="data",
        goal="Показать строки выбранного заказа без повторного поиска.",
        requirements=[
            _requirement("r1", "catalog.item", "entity_ref", "many"),
            _requirement(
                "r2",
                "measure.ordered_quantity",
                "quantity",
                "many",
                unit_dimension="quantity_unit",
            ),
        ],
        slots=[
            {
                "slot_id": "order",
                "semantic_type": "document.sales_order",
                "value_type": "entity_ref",
                "status": "resolved_context",
                "mentions": ["этот заказ"],
                "binding": context_binding,
            }
        ],
        skill_id="ut115.sales.order-lines",
        arguments=[{"parameter": "order", "binding": context_binding}],
        required_outputs=[
            "order.ref",
            "order.line_number",
            "line.item",
            "line.unit",
            "line.quantity",
        ],
        final_outputs=["line.item", "line.quantity"],
        plan_number=9137,
    )
    q102 = copy.deepcopy(q011)
    q102["result"]["plan_id"] = "00000000-0000-4000-8000-000000009102"
    q102["interpretation"]["slots"][0] = _literal_slot(
        "article", "catalog.item.article", "TEST-NOT-EXISTS-999999"
    )
    missing_context = {
        **ECHO,
        "decision": "clarify",
        "interpretation": {
            "intent_kind": "data",
            "goal_ru": "Уточнить заказ для просмотра его строк.",
            "required_facts": [
                _requirement("r1", "document.sales_order", "entity_ref", "one")
            ],
            "slots": [
                {
                    "slot_id": "order",
                    "semantic_type": "document.sales_order",
                    "value_type": "entity_ref",
                    "status": "missing",
                    "mentions": ["этот заказ"],
                }
            ],
        },
        "result": {
            "kind": "clarify",
            "question_ru": "Какой именно заказ требуется использовать?",
            "missing_requirement_ids": ["r1"],
            "choices": [],
        },
    }
    plans = {
        "q001": q001,
        "q011": q011,
        "q036": q036,
        "q036_ambiguous": copy.deepcopy(q036),
        "q037": q037,
        "q037_ref_mismatch": copy.deepcopy(q037),
        "q037_missing_context": missing_context,
        "q102": q102,
    }
    for alias in (
        "q011_real_mcp_minimal",
        "mcp_query_error",
        "mcp_real_error_only",
        "mcp_malformed",
        "slow_q011",
    ):
        plans[alias] = copy.deepcopy(q011)
    return plans


def main() -> None:
    OUTPUT.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
