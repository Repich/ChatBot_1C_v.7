from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from .conftest import REPO_ROOT, AppFactory, RunningApp
from .support import (
    FixtureClient,
    deepseek_calls,
    execute_query_calls,
    fact_values,
    mcp_arguments,
)
from .synthetic_skills import (
    BARCODE_SKILL,
    KeysetMutation,
    invalid_keyset_variant,
)

MUTATION_ERROR_CODE: dict[KeysetMutation, str] = {
    "cursor_reordered": "PAGINATION_CURSOR_BIJECTION_MISMATCH",
    "cursor_missing": "PAGINATION_CURSOR_BIJECTION_MISMATCH",
    "cursor_duplicate": "PAGINATION_CURSOR_BIJECTION_MISMATCH",
    "nullable_sort_identity": "PAGINATION_SORT_COORDINATE_INVALID",
    "cursor_parameter_absent": "PAGINATION_QUERY_CONTRACT_UNPROVEN",
    "sort_not_ending_identity": "PAGINATION_IDENTITY_SUFFIX_MISMATCH",
}


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _schema_errors(
    validator: Draft202012Validator, document: dict[str, Any]
) -> list[str]:
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    return [f"/{'/'.join(map(str, error.path))}: {error.message}" for error in errors]


def test_exported_imported_barcode_resolver_deduplicates_register_rows_across_pages(
    app: RunningApp,
    app_factory: AppFactory,
    fixture_transport: FixtureClient,
) -> None:
    exported = app.api.export_skill(BARCODE_SKILL)
    assert exported["operation"]["pagination"]["strategy"] == "keyset"
    assert re.search(
        r"\bВЫБРАТЬ\s+РАЗЛИЧНЫЕ\b",
        exported["operation"]["query_template"]["text"],
        re.IGNORECASE,
    )

    clean = app_factory.start(auto_import=False)
    assert clean.api.list_skills()["skills"] == []
    response, imported = clean.api.import_package(_json_bytes(exported))
    assert response.status == 200, imported
    assert imported["status"] == "accepted"
    assert clean.api.export_skill(BARCODE_SKILL) == exported

    fixture_transport.configure(
        "barcode duplicate register dimensions",
        plan_kind="barcode",
        barcode_items=43,
    )
    proof = fixture_transport.state()["configuration_proof"]
    assert proof == {
        "barcode_register": "InformationRegister.BarcodeItems",
        "dimensions": ["barcode", "item", "characteristic", "series"],
        "register_rows_per_item": 2,
    }

    session = clean.api.create_session()
    turn, _ = clean.api.ask(
        session["session_id"], "Найди номенклатуру по штрихкоду 4600000000001"
    )
    pages: list[list[dict[str, Any]]] = []
    while True:
        assert turn["outcome"] == "success_with_rows", turn
        evidence = clean.api.evidence(turn["trace_id"])
        pages.append(fact_values(evidence, "item.ref"))
        continuation = turn["pagination"]["continuation"]
        if continuation is None:
            break
        response, accepted = clean.api.continue_list(
            session["session_id"],
            {"continuation_handle": continuation["handle"]},
        )
        assert response.status == 202, accepted
        turn = clean.api.wait_turn(accepted["turn_id"])

    assert [len(page) for page in pages] == [20, 20, 3]
    item_ids = [item["УникальныйИдентификатор"] for page in pages for item in page]
    assert item_ids == [
        f"00000000-0000-4000-8000-{600000 + index:012d}" for index in range(1, 44)
    ]
    assert len(item_ids) == len(set(item_ids)) == 43

    state = fixture_transport.state()
    assert len(deepseek_calls(state)) == 1
    calls = execute_query_calls(state)
    assert len(calls) == 3
    assert [mcp_arguments(call)["limit"] for call in calls] == [21, 21, 21]
    assert all(
        re.search(
            r"\bВЫБРАТЬ\s+РАЗЛИЧНЫЕ\b",
            mcp_arguments(call)["query"],
            re.IGNORECASE,
        )
        for call in calls
    )
    params = [mcp_arguments(call)["params"] for call in calls]
    assert params[0] == {
        "Штрихкод": "4600000000001",
        "ЕстьКурсор": False,
        "ИмяКурсора": None,
        "СсылкаКурсора": None,
    }
    assert params[1]["ЕстьКурсор"] is True
    assert params[1]["СсылкаКурсора"]["УникальныйИдентификатор"] == item_ids[19]
    assert params[2]["СсылкаКурсора"]["УникальныйИдентификатор"] == item_ids[39]


@pytest.mark.parametrize(
    "mutation",
    [
        "cursor_reordered",
        "cursor_missing",
        "cursor_duplicate",
        "nullable_sort_identity",
        "cursor_parameter_absent",
        "sort_not_ending_identity",
    ],
)
def test_invalid_portable_keyset_mutation_is_rejected_atomically(
    app: RunningApp,
    app_factory: AppFactory,
    mutation: KeysetMutation,
) -> None:
    exported = app.api.export_skill(BARCODE_SKILL)
    clean = app_factory.start(auto_import=False)
    before = clean.api.list_skills()
    assert before["skills"] == []

    response, rejected = clean.api.import_package(
        invalid_keyset_variant(exported, mutation)
    )
    assert response.status == 422, rejected
    assert set(rejected) == {"status", "catalog_revision", "errors"}
    assert rejected["status"] == "rejected"
    assert rejected["catalog_revision"] == before["catalog_revision"]
    assert rejected["errors"]
    assert all(
        set(error) == {"code", "json_pointer", "message_ru", "keyword"}
        for error in rejected["errors"]
    )
    codes = {error["code"] for error in rejected["errors"]}
    assert MUTATION_ERROR_CODE[mutation] in codes
    assert "SKILL_INTEGRITY_MISMATCH" not in codes
    assert clean.api.list_skills() == before

    response, imported = clean.api.import_package(_json_bytes(exported))
    assert response.status == 200, imported
    assert imported["status"] == "accepted"
    assert clean.api.export_skill(BARCODE_SKILL) == exported


def test_legacy_v10_evidence_remains_valid_and_new_diagnostics_are_explicit_v11(
    app: RunningApp,
    fixture_transport: FixtureClient,
) -> None:
    schema = json.loads(
        (REPO_ROOT / "schemas/evidence.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    legacy = json.loads(
        (
            REPO_ROOT / "tests/fixtures/contracts/valid/data_evidence_rows.json"
        ).read_text(encoding="utf-8")
    )
    assert legacy["schema_version"] == "1.0.0"
    assert all("collection_scope" not in step for step in legacy["steps"])
    assert all(
        "required" not in requirement
        for requirement in legacy["coverage"]["requirements"]
    )
    assert _schema_errors(validator, legacy) == []
    assert json.loads(json.dumps(legacy, ensure_ascii=False)) == legacy

    promoted_without_fields = copy.deepcopy(legacy)
    promoted_without_fields["schema_version"] = "1.1.0"
    promoted_errors = _schema_errors(validator, promoted_without_fields)
    assert any("collection_scope" in error for error in promoted_errors)
    assert any(
        "'required' is a required property" in error for error in promoted_errors
    )

    fixture_transport.configure(
        "evidence v11 explicit fields", plan_kind="warehouse", warehouse_rows=1
    )
    session = app.api.create_session()
    turn, _ = app.api.ask(session["session_id"], "Покажи склады")
    assert turn["outcome"] == "success_with_rows", turn
    generated = json.loads(
        app.api.diagnostic_members(turn["trace_id"])["evidence.json"]
    )
    assert generated["schema_version"] == "1.1.0"
    assert generated["steps"]
    assert all(
        step.get("collection_scope") == "visible_page" for step in generated["steps"]
    )
    assert generated["coverage"]["requirements"]
    assert all(
        type(requirement.get("required")) is bool
        for requirement in generated["coverage"]["requirements"]
    )
    generated_errors = _schema_errors(validator, generated)
    assert not generated_errors, (
        "Generated evidence violates public schema:\n" + "\n".join(generated_errors)
    )
