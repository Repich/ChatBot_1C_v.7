"""Test-owned portable skill mutations built only from the public export API."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

import rfc8785

WAREHOUSE_SKILL = "ut115.ref.warehouse.resolve"
STOCK_SKILL = "ut115.stock.balance"
SHIPMENT_SKILL = "ut115.sales.shipment-list"
BARCODE_SKILL = "ut115.ref.item.resolve-barcode-exact"
ORDER_SKILL = "ut115.sales.order-header-status-by-number"

EMPTY_SKILLS = {
    "confirmed_not_found": "qa.slice2.empty-confirmed-not-found",
    "confirmed_no_rows": "qa.slice2.empty-confirmed-no-rows",
    "not_applicable": "qa.slice2.empty-not-applicable",
    "error_if_empty": "qa.slice2.empty-error-if-empty",
}
ZERO_SKILL = "qa.slice2.zero-aggregate"
PARTIAL_SKILL = "qa.slice2.partial-pagination"
TRUNCATION_ERROR_SKILL = "qa.slice2.error-if-truncated"
CATALOG_PROBE_SKILL = "qa.slice2.catalog-probe"

EmptySemantics = Literal[
    "confirmed_not_found",
    "confirmed_no_rows",
    "not_applicable",
    "error_if_empty",
]
KeysetMutation = Literal[
    "cursor_reordered",
    "cursor_missing",
    "cursor_duplicate",
    "nullable_sort_identity",
    "cursor_parameter_absent",
    "sort_not_ending_identity",
]


def empty_variant(base: dict[str, Any], semantics: EmptySemantics) -> bytes:
    skill = _identity(base, EMPTY_SKILLS[semantics], f"empty {semantics}")
    for fact in skill["output_contract"]["facts"]:
        fact["nullable"] = True
    candidate_ids = {
        fact_id
        for fact_set in skill["output_contract"]["sufficiency"]["required_fact_sets"]
        for fact_id in fact_set
    }
    identity_ids = set(skill["output_contract"]["row_identity_fact_ids"])
    proof_fact = next(
        fact
        for fact in skill["output_contract"]["facts"]
        if fact["fact_id"] not in candidate_ids | identity_ids
    )
    proof_fact["required"] = True
    proof_fact["nullable"] = False
    skill["output_contract"]["sufficiency"]["empty_semantics"] = semantics
    return sign(skill)


def zero_aggregate_variant(base: dict[str, Any]) -> bytes:
    skill = _identity(base, ZERO_SKILL, "typed zero aggregate")
    skill["version"] = base["version"]
    skill["display"]["purpose_ru"] = (
        "Возвращает полный агрегированный фактический остаток на указанный момент."
    )
    skill["selection"]["aliases_ru"] = [
        "slice2 полный агрегированный остаток",
    ]
    skill["parameters"] = [
        copy.deepcopy(
            next(
                parameter
                for parameter in base["parameters"]
                if parameter["name"] == "moment"
            )
        )
    ]
    skill["provides"] = {
        "capability_ids": ["CAP-STOCK-BALANCE"],
        "fact_types": ["measure.stock_balance"],
    }
    skill["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{ZERO_SKILL}.v1",
            "language": "1c-query",
            "text": (
                "ВЫБРАТЬ\n"
                "  СУММА(Остатки.ВНаличииОстаток) КАК ВНаличииОстаток\n"
                "ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки(&Момент) КАК Остатки"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [],
            "include_schema": True,
            "mcp_limit": {"default": 2, "maximum": 2},
        },
        "parameter_bindings": [
            {
                "parameter": "moment",
                "query_parameter": "Момент",
                "encoding": "datetime",
            }
        ],
        "column_bindings": [
            {
                "column": "ВНаличииОстаток",
                "fact_id": "stock.balance",
                "accepted_mcp_types": ["Число"],
                "converter": "decimal",
            }
        ],
        "pagination": {"strategy": "none"},
    }
    skill["output_contract"] = {
        "contract_id": f"{ZERO_SKILL}.v1",
        "contract_version": "1.0.0",
        "cardinality": "aggregate",
        "facts": [
            {
                "fact_id": "stock.balance",
                "semantic_type": "measure.stock_balance",
                "value_type": "decimal",
                "role": "measure",
                "required": True,
                "nullable": False,
                "title_ru": "Полный фактический остаток",
                "unit_contract": {"mode": "not_applicable"},
            }
        ],
        "sufficiency": {
            "required_fact_sets": [["stock.balance"]],
            "empty_semantics": "error_if_empty",
            "zero_fact_ids": ["stock.balance"],
            "truncation_policy": "page_is_complete",
        },
        "renderer": {
            "kind": "scalar",
            "primary_fact_ids": ["stock.balance"],
            "column_fact_ids": ["stock.balance"],
        },
    }
    skill["result_constraints"] = []
    skill["examples"] = [
        {
            "question_ru": "каков полный фактический остаток на текущий момент?",
            "applicability": "applicable",
            "reason_ru": "Вопрос требует полного агрегата фактического остатка.",
        },
        {
            "question_ru": "сколько товара доступно с учетом резервов?",
            "applicability": "not_applicable",
            "reason_ru": "Доступность с резервами не равна фактическому остатку.",
        },
    ]
    fixture_schema = {"columns": [{"name": "ВНаличииОстаток", "types": ["Число"]}]}
    moment_binding = {
        "parameter": "moment",
        "value": "2026-07-21T12:00:00+03:00",
    }
    skill["tests"] = [
        {
            "test_id": f"{ZERO_SKILL}.fixture-positive",
            "case_kind": "positive",
            "bindings": [moment_binding],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": {
                    "success": True,
                    "data": [{"ВНаличииОстаток": 0.0}],
                    "schema": fixture_schema,
                    "count": 1,
                },
            },
            "expected": {
                "status": "zero_aggregate",
                "required_fact_ids": ["stock.balance"],
            },
        },
        {
            "test_id": f"{ZERO_SKILL}.fixture-negative",
            "case_kind": "negative",
            "bindings": [moment_binding],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": {
                    "success": False,
                    "error": "synthetic aggregate query failure",
                },
            },
            "expected": {
                "status": "query_error",
                "required_fact_ids": [],
                "error_code": "QUERY_ERROR",
            },
        },
    ]
    return sign(skill)


def truncation_variant(
    base: dict[str, Any],
    *,
    policy: Literal["partial_until_all_pages", "error_if_truncated"],
) -> bytes:
    skill_id = (
        PARTIAL_SKILL if policy == "partial_until_all_pages" else TRUNCATION_ERROR_SKILL
    )
    skill = _identity(base, skill_id, policy.replace("_", " "))
    skill["output_contract"]["sufficiency"]["truncation_policy"] = policy
    return sign(skill)


def catalog_probe_variant(base: dict[str, Any], *, suffix: str = "default") -> bytes:
    skill_id = (
        CATALOG_PROBE_SKILL
        if suffix == "default"
        else f"{CATALOG_PROBE_SKILL}-{suffix}"
    )
    return sign(_identity(base, skill_id, f"catalog probe {suffix}"))


def replacement_variant(base: dict[str, Any]) -> bytes:
    skill = copy.deepcopy(base)
    skill.pop("integrity", None)
    skill["version"] = "1.0.1"
    skill["display"]["purpose_ru"] += " Проверочная ревизия hot reload."
    skill["provenance"]["change_note_ru"] = (
        "Независимая приемочная ревизия для проверки snapshot pinning."
    )
    return sign(skill)


def invalid_keyset_variant(base: dict[str, Any], mutation: KeysetMutation) -> bytes:
    skill = _identity(
        base,
        f"qa.slice2.invalid-keyset-{mutation.replace('_', '-')}",
        f"invalid keyset {mutation}",
    )
    skill["version"] = base["version"]
    pagination = skill["operation"]["pagination"]
    cursor_bindings = pagination["cursor_bindings"]

    if mutation == "cursor_reordered":
        cursor_bindings.reverse()
    elif mutation == "cursor_missing":
        cursor_bindings.pop()
    elif mutation == "cursor_duplicate":
        cursor_bindings[-1] = copy.deepcopy(cursor_bindings[0])
    elif mutation == "nullable_sort_identity":
        identity_id = skill["output_contract"]["row_identity_fact_ids"][-1]
        identity = next(
            fact
            for fact in skill["output_contract"]["facts"]
            if fact["fact_id"] == identity_id
        )
        identity["nullable"] = True
    elif mutation == "cursor_parameter_absent":
        cursor_bindings[0]["query_parameter"] = "MissingCursorParameter"
    elif mutation == "sort_not_ending_identity":
        pagination["sort"].reverse()
        cursor_bindings.reverse()
    else:  # pragma: no cover - closed Literal protects callers
        raise AssertionError(f"unknown keyset mutation {mutation}")
    return sign(skill)


def sign(document: dict[str, Any]) -> bytes:
    unsigned = copy.deepcopy(document)
    unsigned.pop("integrity", None)
    digest = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    signed = {
        **unsigned,
        "integrity": {
            "algorithm": "sha256",
            "canonicalization": "RFC8785",
            "scope": "document_without_integrity",
            "digest": digest,
        },
    }
    return json.dumps(
        signed, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _identity(base: dict[str, Any], skill_id: str, label: str) -> dict[str, Any]:
    skill = copy.deepcopy(base)
    skill.pop("integrity", None)
    skill["skill_id"] = skill_id
    skill["version"] = "1.0.0"
    skill["display"]["name_ru"] = f"Slice 2 acceptance: {label}"
    skill["display"]["purpose_ru"] = (
        f"Синтетический black-box контракт slice 2: {label}."
    )
    skill["selection"]["aliases_ru"] = [f"slice2 {label}"]
    skill["operation"]["query_template"]["template_id"] = f"{skill_id}.v1"
    skill["output_contract"]["contract_id"] = f"{skill_id}.v1"
    for index, test in enumerate(skill.get("tests", []), start=1):
        test["test_id"] = f"{skill_id}.fixture-{index}"
    skill["provenance"]["author"] = "Independent slice 2 acceptance"
    skill["provenance"]["change_note_ru"] = (
        "Синтетическая переносимая вариация только для black-box приемки."
    )
    return skill
