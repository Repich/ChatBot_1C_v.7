"""Deterministically build the reviewed UT 11.5.27.56 starter catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.harness import ContractHarness

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "skills" / "ut-11.5.27.56"
CREATED = "2026-07-21T00:00:00Z"
CONFIG_NAME = "1С:Управление торговлей (базовая), редакция 11"
ORDER_SHA = "dbb8b95230a929de03c7af4534adea7550d6de72fdaca9e2defb9c38978d1a61"
HELP_SHA = "0a5de708506f8fd2d491e71a0ac30e106cb8705f241f883aacfa34b011810902"
ITEM_SHA = "5fd976edf70c1dd0c65aeabce8362e6767876bcc9e33985d0aa47bdbbe8a86f4"
STOCK_SHA = "40dd46f5fd7be824dbb68c317c961973c92a06c534943419e552f0ba8d8f3113"
BARCODE_SHA = "fdee46a99d1b44dfe590f6e4b1c1c468d92159e437b9285fcd3daa7a58521bd3"
WAREHOUSE_SHA = "f5ed42f385cbd1b4efeffa216178e20f7266e84a08bd10e3b1995874af01d768"
SHIPMENT_SHA = "1ebe12622345cbce608283575d39b8835013a61d817787c4e98c6ddbbb4d105d"
ORDER_STATE_SHA = "bc2d443177b72cac60286e96b170118bd1e007a9e1bb536989c4a251439c491d"
ORDER_PROFILE_SHA = hashlib.sha256(
    f"{ORDER_SHA}:{ORDER_STATE_SHA}".encode("ascii")
).hexdigest()


def ref(object_type: str, unique_id: str, presentation: str) -> dict[str, Any]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": unique_id,
        "ТипОбъекта": object_type,
        "Представление": presentation,
    }


ITEM = ref(
    "СправочникСсылка.Номенклатура",
    "00000000-0000-4000-8000-000000000101",
    "Куртка демисезонная",
)
WAREHOUSE = ref(
    "СправочникСсылка.Склады",
    "00000000-0000-4000-8000-000000000201",
    "Основной склад",
)
ROOM = ref(
    "СправочникСсылка.СкладскиеПомещения",
    "00000000-0000-4000-8000-000000000202",
    "Основное помещение",
)
CHARACTERISTIC = ref(
    "СправочникСсылка.ХарактеристикиНоменклатуры",
    "00000000-0000-4000-8000-000000000203",
    "Без характеристики",
)
ASSIGNMENT = ref(
    "СправочникСсылка.Назначения",
    "00000000-0000-4000-8000-000000000204",
    "Без назначения",
)
ORDER = ref(
    "ДокументСсылка.ЗаказКлиента",
    "00000000-0000-4000-8000-000000000301",
    "Заказ клиента 0000-000005 от 12.02.2025",
)
PARTNER = ref(
    "СправочникСсылка.Партнеры",
    "00000000-0000-4000-8000-000000000401",
    "Торговый дом Север",
)
ORGANIZATION = ref(
    "СправочникСсылка.Организации",
    "00000000-0000-4000-8000-000000000402",
    "ООО Торговая компания",
)
DEPARTMENT = ref(
    "СправочникСсылка.СтруктураПредприятия",
    "00000000-0000-4000-8000-000000000403",
    "Розничные продажи",
)
SHIPMENT = ref(
    "ДокументСсылка.РеализацияТоваровУслуг",
    "00000000-0000-4000-8000-000000000501",
    "Реализация товаров и услуг 0000-000031 от 15.07.2026",
)


def parameter(
    name: str,
    title: str,
    description: str,
    value_type: str,
    sources: list[str],
    normalization: str,
    *,
    semantic_type: str | None = None,
    entity_types: list[str] | None = None,
    required: bool = True,
    allowed_values: list[str] | None = None,
    default: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "title_ru": title,
        "description_ru": description,
        "value_type": value_type,
        "required": required,
        "allowed_sources": sources,
        "normalization": normalization,
    }
    if semantic_type is not None:
        result["semantic_type"] = semantic_type
    if entity_types is not None:
        result["entity_types"] = entity_types
    if allowed_values is not None:
        result["allowed_values"] = allowed_values
    if default is not None:
        result["default"] = default
    return result


def fact(
    fact_id: str,
    semantic_type: str,
    value_type: str,
    title: str,
    *,
    role: str = "attribute",
    required: bool = True,
    nullable: bool = False,
    unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "semantic_type": semantic_type,
        "value_type": value_type,
        "role": role,
        "required": required,
        "nullable": nullable,
        "title_ru": title,
        "unit_contract": unit or {"mode": "not_applicable"},
    }


def column(
    name: str, fact_id: str, accepted: list[str], converter: str
) -> dict[str, Any]:
    return {
        "column": name,
        "fact_id": fact_id,
        "accepted_mcp_types": accepted,
        "converter": converter,
    }


def compatibility(
    metadata_sha: str, requirements: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "configuration_id": "УправлениеТорговлейБазовая",
        "configuration_name": CONFIG_NAME,
        "release_range": {
            "minimum": "11.5.27.56",
            "maximum": "11.5.27.56",
            "include_minimum": True,
            "include_maximum": True,
        },
        "compatibility_modes": ["8.3.27"],
        "required_metadata": requirements,
        "metadata_snapshot_sha256": metadata_sha,
    }


def provenance(
    metadata_sha: str, references: list[dict[str, Any]], note: str
) -> dict[str, Any]:
    return {
        "author": "ChatBot 1C slice 1",
        "created_at": CREATED,
        "reviewed_by": "Architecture and PM source proof",
        "reviewed_at": CREATED,
        "source_configuration": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "release": "11.5.27.56",
            "compatibility_mode": "8.3.27",
            "metadata_snapshot_sha256": metadata_sha,
        },
        "source_references": references,
        "change_note_ru": note,
    }


def source(kind: str, uri: str, sha: str | None = None) -> dict[str, Any]:
    result = {"kind": kind, "uri": uri}
    if sha is not None:
        result["sha256"] = sha
    return result


def data_operation(
    template_id: str,
    query: str,
    parameter_bindings: list[dict[str, Any]],
    column_bindings: list[dict[str, Any]],
    *,
    default_limit: int = 20,
    invariant_constants: list[dict[str, Any]] | None = None,
    pagination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": template_id,
            "language": "1c-query",
            "text": query,
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": invariant_constants or [],
            "include_schema": True,
            "mcp_limit": {"default": default_limit, "maximum": 1000},
        },
        "parameter_bindings": parameter_bindings,
        "column_bindings": column_bindings,
        "pagination": pagination or {"strategy": "none"},
    }


def mcp_test(
    test_id: str,
    bindings: list[dict[str, Any]],
    data: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    required: list[str],
    *,
    status: str = "success_with_rows",
    case_kind: str = "positive",
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "case_kind": case_kind,
        "bindings": bindings,
        "fixture": {
            "kind": "mcp_execute_query",
            "response": {
                "success": True,
                "data": data,
                "schema": {"columns": columns},
                "count": len(data),
            },
        },
        "expected": {"status": status, "required_fact_ids": required},
    }


def empty_test(test_id: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    return mcp_test(
        test_id,
        [],
        [],
        columns,
        [],
        status="success_empty",
        case_kind="negative",
    )


def output_contract(
    contract_id: str,
    cardinality: str,
    facts: list[dict[str, Any]],
    required: list[str],
    columns: list[str],
    *,
    identities: list[str] | None = None,
    renderer: str = "table",
    empty: str = "confirmed_no_rows",
    zeros: list[str] | None = None,
    truncation: str = "page_is_complete",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract_id": contract_id,
        "contract_version": "1.0.0",
        "cardinality": cardinality,
        "facts": facts,
        "sufficiency": {
            "required_fact_sets": [required],
            "empty_semantics": empty,
            "zero_fact_ids": zeros or [],
            "truncation_policy": truncation,
        },
        "renderer": {
            "kind": renderer,
            "primary_fact_ids": [required[-1]],
            "column_fact_ids": columns,
        },
    }
    if identities is not None:
        result["row_identity_fact_ids"] = identities
    return result


def base_skill(
    *,
    skill_id: str,
    name: str,
    purpose: str,
    limitations: list[str],
    capabilities: list[str],
    fact_types: list[str],
    metadata_sha: str,
    metadata_requirements: list[dict[str, Any]],
    intent: str,
    aliases: list[str],
    anti_examples: list[str],
    context_types: list[str],
    parameters: list[dict[str, Any]],
    operation: dict[str, Any],
    output: dict[str, Any],
    dependencies: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    references: list[dict[str, Any]],
    result_constraints: list[dict[str, Any]] | None = None,
    version: str = "1.0.0",
) -> dict[str, Any]:
    source_runtime = (
        "mcp.execute_query" if operation["kind"] == "data_query" else "help-index"
    )
    document = {
        "schema_version": "1.0.0",
        "document_type": "skill",
        "skill_id": skill_id,
        "version": version,
        "display": {
            "name_ru": name,
            "purpose_ru": purpose,
            "limitations_ru": limitations,
        },
        "provides": {"capability_ids": capabilities, "fact_types": fact_types},
        "compatibility": compatibility(metadata_sha, metadata_requirements),
        "selection": {
            "intent_kinds": [intent],
            "aliases_ru": aliases,
            "anti_examples_ru": anti_examples,
            "required_context_fact_types": context_types,
        },
        "parameters": parameters,
        "operation": operation,
        "output_contract": output,
        "result_constraints": result_constraints or [],
        "dependencies": {
            "runtime_contracts": [
                {"contract": "skill-runtime", "version_range": "^1.0.0"},
                {"contract": source_runtime, "version_range": "^1.0.0"},
            ],
            "skills": dependencies,
        },
        "examples": [
            {
                "question_ru": aliases[0] + "?",
                "applicability": "applicable",
                "reason_ru": "Вопрос соответствует назначению и typed outputs навыка.",
            },
            {
                "question_ru": anti_examples[0] + "?",
                "applicability": "not_applicable",
                "reason_ru": "Вопрос находится за явно объявленной границей навыка.",
            },
        ],
        "tests": tests,
        "provenance": provenance(
            metadata_sha,
            references,
            "Навык построен по проверенным metadata/help исходникам УТ 11.5.27.56.",
        ),
    }
    return generate_integrity(document)


def item_facts() -> list[dict[str, Any]]:
    return [
        fact("item.ref", "catalog.item", "entity_ref", "Номенклатура", role="entity"),
        fact("item.code", "catalog.item.code", "string", "Код"),
        fact(
            "item.article",
            "catalog.item.article",
            "string",
            "Артикул",
            required=False,
            nullable=True,
        ),
        fact("item.name", "catalog.item.name", "string", "Наименование"),
    ]


def item_schema() -> list[dict[str, Any]]:
    return [
        {"name": "Номенклатура", "types": ["СправочникСсылка.Номенклатура"]},
        {"name": "Код", "types": ["Строка"]},
        {"name": "Артикул", "types": ["Строка"]},
        {"name": "Наименование", "types": ["Строка"]},
    ]


def item_columns() -> list[dict[str, Any]]:
    return [
        column(
            "Номенклатура",
            "item.ref",
            ["СправочникСсылка.Номенклатура"],
            "object_ref",
        ),
        column("Код", "item.code", ["Строка"], "string"),
        column("Артикул", "item.article", ["Строка"], "string"),
        column("Наименование", "item.name", ["Строка"], "string"),
    ]


def item_keyset_predicate(name_expression: str, ref_expression: str) -> str:
    return (
        "  И (НЕ &ЕстьКурсор\n"
        f"    ИЛИ {name_expression} > &ИмяКурсора\n"
        f"    ИЛИ ({name_expression} = &ИмяКурсора\n"
        f"      И {ref_expression} > &СсылкаКурсора))\n"
    )


def item_keyset_pagination() -> dict[str, Any]:
    return {
        "strategy": "keyset",
        "has_cursor_query_parameter": "ЕстьКурсор",
        "sort": [
            {"fact_id": "item.name", "direction": "asc"},
            {"fact_id": "item.ref", "direction": "asc"},
        ],
        "cursor_bindings": [
            {
                "fact_id": "item.name",
                "query_parameter": "ИмяКурсора",
                "encoding": "string",
            },
            {
                "fact_id": "item.ref",
                "query_parameter": "СсылкаКурсора",
                "encoding": "object_ref",
            },
        ],
    }


def build_item_by_article() -> dict[str, Any]:
    facts = item_facts()
    query = (
        "ВЫБРАТЬ\n"
        "  Номенклатура.Ссылка КАК Номенклатура,\n"
        "  Номенклатура.Код КАК Код,\n"
        "  Номенклатура.Артикул КАК Артикул,\n"
        "  Номенклатура.Наименование КАК Наименование\n"
        "ИЗ Справочник.Номенклатура КАК Номенклатура\n"
        "ГДЕ НЕ Номенклатура.ЭтоГруппа\n"
        "  И Номенклатура.Артикул = &Артикул\n"
        + item_keyset_predicate(
            "Номенклатура.Наименование", "Номенклатура.Ссылка"
        )
        + "УПОРЯДОЧИТЬ ПО Номенклатура.Наименование, Номенклатура.Ссылка"
    )
    data = [
        {
            "Номенклатура": ITEM,
            "Код": "000000001",
            "Артикул": "КР-01",
            "Наименование": "Куртка демисезонная",
        }
    ]
    return base_skill(
        skill_id="ut115.ref.item.resolve-article-exact",
        name="Поиск номенклатуры по точному артикулу",
        purpose="Находит номенклатуру только по точному совпадению артикула и возвращает exact ссылки.",
        limitations=["Не выполняет поиск по фрагменту артикула или наименованию."],
        capabilities=["CAP-REF-ITEM-FIND"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=ITEM_SHA,
        metadata_requirements=[
            {
                "object_name": "Справочник.Номенклатура",
                "attributes": [
                    "Ссылка",
                    "Код",
                    "Артикул",
                    "Наименование",
                    "ЭтоГруппа",
                ],
            }
        ],
        intent="data",
        aliases=["найти номенклатуру по точному артикулу", "товар с артикулом"],
        anti_examples=["найти товар по части названия"],
        context_types=[],
        parameters=[
            parameter(
                "article",
                "Артикул",
                "Точный артикул номенклатуры из вопроса.",
                "string",
                ["user_slot"],
                "trim",
            )
        ],
        operation=data_operation(
            "ut115.ref.item.resolve-article-exact.v1",
            query,
            [
                {
                    "parameter": "article",
                    "query_parameter": "Артикул",
                    "encoding": "string",
                }
            ],
            item_columns(),
            invariant_constants=[],
            pagination=item_keyset_pagination(),
        ),
        output=output_contract(
            "ut115.ref.item.resolve-article-exact.v1",
            "many",
            facts,
            ["item.ref", "item.code", "item.name"],
            [item["fact_id"] for item in facts],
            identities=["item.ref"],
        ),
        dependencies=[],
        tests=[
            mcp_test(
                "ut.item-by-article.positive",
                [{"parameter": "article", "value": "КР-01"}],
                data,
                item_schema(),
                ["item.ref", "item.code", "item.name"],
            ),
            empty_test("ut.item-by-article.empty", item_schema()),
        ],
        references=[
            source(
                "configuration_metadata",
                "ut-config://11.5.27.56/Catalogs/Номенклатура.xml",
                ITEM_SHA,
            )
        ],
        version="1.1.0",
    )


def build_item_by_code() -> dict[str, Any]:
    facts = item_facts()
    query = (
        "ВЫБРАТЬ\n"
        "  Номенклатура.Ссылка КАК Номенклатура,\n"
        "  Номенклатура.Код КАК Код,\n"
        "  Номенклатура.Артикул КАК Артикул,\n"
        "  Номенклатура.Наименование КАК Наименование\n"
        "ИЗ Справочник.Номенклатура КАК Номенклатура\n"
        "ГДЕ НЕ Номенклатура.ЭтоГруппа\n"
        "  И Номенклатура.Код = &Код\n"
        + item_keyset_predicate(
            "Номенклатура.Наименование", "Номенклатура.Ссылка"
        )
        + "УПОРЯДОЧИТЬ ПО Номенклатура.Наименование, Номенклатура.Ссылка"
    )
    data = [
        {
            "Номенклатура": ITEM,
            "Код": "000000001",
            "Артикул": "КР-01",
            "Наименование": "Куртка демисезонная",
        }
    ]
    return base_skill(
        skill_id="ut115.ref.item.resolve-code-exact",
        name="Поиск номенклатуры по точному коду",
        purpose="Находит номенклатуру только по точному совпадению кода и возвращает exact ссылки.",
        limitations=["Не выполняет поиск по фрагменту кода, артикула или наименования."],
        capabilities=["CAP-REF-ITEM-FIND"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=ITEM_SHA,
        metadata_requirements=[
            {
                "object_name": "Справочник.Номенклатура",
                "attributes": [
                    "Ссылка",
                    "Код",
                    "Артикул",
                    "Наименование",
                    "ЭтоГруппа",
                ],
            }
        ],
        intent="data",
        aliases=["найти номенклатуру по точному коду", "товар с кодом"],
        anti_examples=["найти товар по артикулу или части названия"],
        context_types=[],
        parameters=[
            parameter(
                "catalog_code",
                "Код номенклатуры",
                "Точный код номенклатуры из вопроса.",
                "string",
                ["user_slot"],
                "trim",
            )
        ],
        operation=data_operation(
            "ut115.ref.item.resolve-code-exact.v1",
            query,
            [
                {
                    "parameter": "catalog_code",
                    "query_parameter": "Код",
                    "encoding": "string",
                }
            ],
            item_columns(),
            invariant_constants=[],
            pagination=item_keyset_pagination(),
        ),
        output=output_contract(
            "ut115.ref.item.resolve-code-exact.v1",
            "many",
            facts,
            ["item.ref", "item.code", "item.name"],
            [item["fact_id"] for item in facts],
            identities=["item.ref"],
        ),
        dependencies=[],
        tests=[
            mcp_test(
                "ut.item-by-code.positive",
                [{"parameter": "catalog_code", "value": "000000001"}],
                data,
                item_schema(),
                ["item.ref", "item.code", "item.name"],
            ),
            empty_test("ut.item-by-code.empty", item_schema()),
        ],
        references=[
            source(
                "configuration_metadata",
                "ut-config://11.5.27.56/Catalogs/Номенклатура.xml",
                ITEM_SHA,
            )
        ],
        version="1.1.0",
    )


def build_item_by_barcode() -> dict[str, Any]:
    facts = item_facts()
    query = (
        "ВЫБРАТЬ РАЗЛИЧНЫЕ\n"
        "  Штрихкоды.Номенклатура КАК Номенклатура,\n"
        "  Штрихкоды.Номенклатура.Код КАК Код,\n"
        "  Штрихкоды.Номенклатура.Артикул КАК Артикул,\n"
        "  Штрихкоды.Номенклатура.Наименование КАК Наименование\n"
        "ИЗ РегистрСведений.ШтрихкодыНоменклатуры КАК Штрихкоды\n"
        "ГДЕ Штрихкоды.Штрихкод = &Штрихкод\n"
        + item_keyset_predicate(
            "Штрихкоды.Номенклатура.Наименование", "Штрихкоды.Номенклатура"
        )
        + "УПОРЯДОЧИТЬ ПО Штрихкоды.Номенклатура.Наименование, Штрихкоды.Номенклатура"
    )
    data = [
        {
            "Номенклатура": ITEM,
            "Код": "000000001",
            "Артикул": "КР-01",
            "Наименование": "Куртка демисезонная",
        }
    ]
    return base_skill(
        skill_id="ut115.ref.item.resolve-barcode-exact",
        name="Поиск номенклатуры по точному штрихкоду",
        purpose="Находит связанную номенклатуру только по точному штрихкоду регистра УТ.",
        limitations=["Не выполняет prefix/contains поиск по штрихкоду."],
        capabilities=["CAP-REF-ITEM-FIND"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=BARCODE_SHA,
        metadata_requirements=[
            {
                "object_name": "РегистрСведений.ШтрихкодыНоменклатуры",
                "attributes": [
                    "Штрихкод",
                    "Номенклатура",
                    "Характеристика",
                    "Серия",
                    "Упаковка",
                ],
            },
            {
                "object_name": "Справочник.Номенклатура",
                "attributes": ["Ссылка", "Код", "Артикул", "Наименование"],
            },
        ],
        intent="data",
        aliases=["найти номенклатуру по точному штрихкоду", "товар со штрихкодом"],
        anti_examples=["найти товар по части штрихкода или названия"],
        context_types=[],
        parameters=[
            parameter(
                "barcode",
                "Штрихкод",
                "Точное значение штрихкода номенклатуры.",
                "string",
                ["user_slot"],
                "trim",
            )
        ],
        operation=data_operation(
            "ut115.ref.item.resolve-barcode-exact.v1",
            query,
            [
                {
                    "parameter": "barcode",
                    "query_parameter": "Штрихкод",
                    "encoding": "string",
                }
            ],
            item_columns(),
            invariant_constants=[],
            pagination=item_keyset_pagination(),
        ),
        output=output_contract(
            "ut115.ref.item.resolve-barcode-exact.v1",
            "many",
            facts,
            ["item.ref", "item.code", "item.name"],
            [item["fact_id"] for item in facts],
            identities=["item.ref"],
        ),
        dependencies=[],
        tests=[
            mcp_test(
                "ut.item-by-barcode.positive",
                [{"parameter": "barcode", "value": "4600000000001"}],
                data,
                item_schema(),
                ["item.ref", "item.code", "item.name"],
            ),
            empty_test("ut.item-by-barcode.empty", item_schema()),
        ],
        references=[
            source(
                "configuration_metadata",
                "ut-config://11.5.27.56/InformationRegisters/ШтрихкодыНоменклатуры.xml",
                BARCODE_SHA,
            )
        ],
        version="1.1.0",
    )


def build_item_by_name() -> dict[str, Any]:
    facts = [
        *item_facts(),
    ]
    query = (
        "ВЫБРАТЬ\n"
        "  Номенклатура.Ссылка КАК Номенклатура,\n"
        "  Номенклатура.Код КАК Код,\n"
        "  Номенклатура.Артикул КАК Артикул,\n"
        "  Номенклатура.Наименование КАК Наименование\n"
        "ИЗ Справочник.Номенклатура КАК Номенклатура\n"
        "ГДЕ НЕ Номенклатура.ЭтоГруппа\n"
        '  И Номенклатура.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"\n'
        + item_keyset_predicate(
            "Номенклатура.Наименование", "Номенклатура.Ссылка"
        )
        + "УПОРЯДОЧИТЬ ПО Номенклатура.Наименование, Номенклатура.Ссылка"
    )
    bindings = [
        {"parameter": "name_fragment", "value": "куртка"},
    ]
    data = [{"Номенклатура": ITEM, "Код": "000000001", "Артикул": "КР-01", "Наименование": "Куртка демисезонная"}]
    return base_skill(
        skill_id="ut115.ref.item.resolve-name-contains",
        name="Поиск номенклатуры по наименованию",
        purpose="Находит номенклатуру по параметризованному фрагменту наименования и возвращает exact ссылки.",
        limitations=["Не использует этот поиск для точного совпадения артикула."],
        capabilities=["CAP-REF-ITEM-FIND"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=ITEM_SHA,
        metadata_requirements=[{"object_name": "Справочник.Номенклатура", "attributes": ["Ссылка", "Код", "Артикул", "Наименование", "ЭтоГруппа"]}],
        intent="data",
        aliases=["поиск товара по названию", "найти номенклатуру по части наименования"],
        anti_examples=["найти товар по точному артикулу"],
        context_types=[],
        parameters=[parameter("name_fragment", "Фрагмент наименования", "Часть наименования номенклатуры.", "normalized_text", ["user_slot"], "like_contains")],
        operation=data_operation(
            "ut115.ref.item.resolve-name-contains.v1",
            query,
            [{"parameter": "name_fragment", "query_parameter": "Шаблон", "encoding": "like_contains"}],
            item_columns(),
            invariant_constants=[],
            pagination=item_keyset_pagination(),
        ),
        output=output_contract("ut115.ref.item.resolve-name-contains.v1", "many", facts, ["item.ref", "item.code", "item.name"], [item["fact_id"] for item in facts], identities=["item.ref"]),
        dependencies=[],
        tests=[mcp_test("ut.item-by-name.positive", bindings, data, item_schema(), ["item.ref", "item.code", "item.name"]), empty_test("ut.item-by-name.empty", item_schema())],
        references=[source("configuration_metadata", "ut-config://11.5.27.56/Catalogs/Номенклатура.xml", ITEM_SHA)],
        version="1.1.0",
    )


def build_warehouse_resolver() -> dict[str, Any]:
    facts = [
        fact(
            "warehouse.ref",
            "catalog.warehouse",
            "entity_ref",
            "Склад",
            role="entity",
        ),
        fact("warehouse.name", "catalog.warehouse.name", "string", "Наименование"),
        fact("warehouse.type", "catalog.warehouse.type", "string", "Тип склада"),
        fact(
            "warehouse.department",
            "catalog.department",
            "entity_ref",
            "Подразделение",
            role="dimension",
            required=False,
            nullable=True,
        ),
    ]
    query = (
        "ВЫБРАТЬ\n"
        "  Склады.Ссылка КАК Склад,\n"
        "  Склады.Наименование КАК Наименование,\n"
        "  ПРЕДСТАВЛЕНИЕ(Склады.ТипСклада) КАК ТипСклада,\n"
        "  Склады.Подразделение КАК Подразделение\n"
        "ИЗ Справочник.Склады КАК Склады\n"
        "ГДЕ Склады.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ \"~\"\n"
        "  И (НЕ &ТолькоРозничные\n"
        "    ИЛИ Склады.ТипСклада = "
        "ЗНАЧЕНИЕ(Перечисление.ТипыСкладов.РозничныйМагазин))\n"
        "  И (&Подразделение ЕСТЬ NULL\n"
        "    ИЛИ Склады.Подразделение = &Подразделение)\n"
        "  И (НЕ &ЕстьКурсор\n"
        "    ИЛИ Склады.Наименование > &ИмяКурсора\n"
        "    ИЛИ (Склады.Наименование = &ИмяКурсора\n"
        "      И Склады.Ссылка > &СсылкаКурсора))\n"
        "УПОРЯДОЧИТЬ ПО Склады.Наименование, Склады.Ссылка"
    )
    schema = [
        {"name": "Склад", "types": ["СправочникСсылка.Склады"]},
        {"name": "Наименование", "types": ["Строка"]},
        {"name": "ТипСклада", "types": ["Строка"]},
        {
            "name": "Подразделение",
            "types": ["СправочникСсылка.СтруктураПредприятия"],
        },
    ]
    row = {
        "Склад": WAREHOUSE,
        "Наименование": "Основной склад",
        "ТипСклада": "Розничный магазин",
        "Подразделение": DEPARTMENT,
    }
    required = ["warehouse.ref", "warehouse.name", "warehouse.type"]
    return base_skill(
        skill_id="ut115.ref.warehouse.resolve",
        name="Поиск склада по проверенным реквизитам",
        purpose=(
            "Находит склады по наименованию, точному признаку розничного типа и "
            "опциональному подразделению."
        ),
        limitations=[
            "Не фильтрует склад по прямым Организации или Назначению: таких "
            "реквизитов источник metadata не подтверждает."
        ],
        capabilities=["CAP-REF-WAREHOUSE-FIND"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=WAREHOUSE_SHA,
        metadata_requirements=[
            {
                "object_name": "Справочник.Склады",
                "attributes": [
                    "Ссылка",
                    "Наименование",
                    "ТипСклада",
                    "Подразделение",
                ],
            }
        ],
        intent="data",
        aliases=["найти склад", "показать розничные склады"],
        anti_examples=["найти склад по организации или назначению"],
        context_types=[],
        parameters=[
            parameter(
                "name_fragment",
                "Наименование склада",
                "Безопасно экранируемый фрагмент наименования; пустая строка означает все.",
                "normalized_text",
                ["user_slot", "system"],
                "like_contains",
                required=False,
                default="",
            ),
            parameter(
                "retail_only",
                "Тип склада",
                "При true выбирает exact metadata enum РозничныйМагазин.",
                "boolean",
                ["user_slot", "system"],
                "none",
                required=False,
                default=False,
            ),
            parameter(
                "department",
                "Подразделение",
                "Опциональная подтвержденная exact ссылка подразделения.",
                "entity_ref",
                ["session_context", "previous_step"],
                "object_ref",
                semantic_type="catalog.department",
                entity_types=["catalog.department"],
                required=False,
            ),
        ],
        operation=data_operation(
            "ut115.ref.warehouse.resolve.v1",
            query,
            [
                {
                    "parameter": "name_fragment",
                    "query_parameter": "Шаблон",
                    "encoding": "like_contains",
                },
                {
                    "parameter": "retail_only",
                    "query_parameter": "ТолькоРозничные",
                    "encoding": "boolean",
                },
                {
                    "parameter": "department",
                    "query_parameter": "Подразделение",
                    "encoding": "object_ref",
                },
            ],
            [
                column(
                    "Склад",
                    "warehouse.ref",
                    ["СправочникСсылка.Склады"],
                    "object_ref",
                ),
                column("Наименование", "warehouse.name", ["Строка"], "string"),
                column("ТипСклада", "warehouse.type", ["Строка"], "string"),
                column(
                    "Подразделение",
                    "warehouse.department",
                    ["СправочникСсылка.СтруктураПредприятия"],
                    "object_ref",
                ),
            ],
            invariant_constants=[
                {
                    "kind": "metadata_constant",
                    "statement": 1,
                    "constant_kind": "enum_member",
                    "symbol": "Перечисление.ТипыСкладов.РозничныйМагазин",
                    "role": "state_filter",
                    "occurrences": 1,
                },
                {
                    "kind": "null_literal",
                    "statement": 1,
                    "value": "NULL",
                    "role": "absence_filter",
                    "occurrences": 1,
                },
            ],
            pagination={
                "strategy": "keyset",
                "has_cursor_query_parameter": "ЕстьКурсор",
                "sort": [
                    {"fact_id": "warehouse.name", "direction": "asc"},
                    {"fact_id": "warehouse.ref", "direction": "asc"},
                ],
                "cursor_bindings": [
                    {
                        "fact_id": "warehouse.name",
                        "query_parameter": "ИмяКурсора",
                        "encoding": "string",
                    },
                    {
                        "fact_id": "warehouse.ref",
                        "query_parameter": "СсылкаКурсора",
                        "encoding": "object_ref",
                    },
                ],
            },
        ),
        output=output_contract(
            "ut115.ref.warehouse.resolve.v1",
            "many",
            facts,
            required,
            [item["fact_id"] for item in facts],
            identities=["warehouse.ref"],
            empty="confirmed_not_found",
        ),
        dependencies=[],
        tests=[
            mcp_test(
                "ut.warehouse.retail-positive",
                [
                    {"parameter": "name_fragment", "value": ""},
                    {"parameter": "retail_only", "value": True},
                ],
                [row],
                schema,
                required,
            ),
            empty_test("ut.warehouse.empty", schema),
        ],
        references=[
            source(
                "configuration_metadata",
                "ut-config://11.5.27.56/Catalogs/Склады.xml",
                WAREHOUSE_SHA,
            )
        ],
    )


def build_stock() -> dict[str, Any]:
    facts = [
        fact("item.ref", "catalog.item", "entity_ref", "Номенклатура", role="entity"),
        fact("warehouse.ref", "catalog.warehouse", "entity_ref", "Склад", role="dimension"),
        fact("storage_bin.ref", "catalog.storage_bin", "entity_ref", "Помещение", role="dimension"),
        fact("characteristic.ref", "catalog.item.characteristic", "entity_ref", "Характеристика", role="dimension"),
        fact("assignment.ref", "analytics.assignment", "entity_ref", "Назначение", role="dimension"),
        fact("item.unit", "catalog.item.unit", "string", "Единица хранения", role="dimension"),
        fact("stock.balance", "measure.stock_balance", "quantity", "Фактический остаток", role="measure", unit={"mode": "from_fact", "fact_id": "item.unit"}),
        fact("stock.moment", "time.moment", "datetime", "Момент остатка", role="time"),
    ]
    query = (
        "ВЫБРАТЬ\n"
        "  Остатки.Номенклатура КАК Номенклатура,\n"
        "  Остатки.Склад КАК Склад,\n"
        "  Остатки.Помещение КАК Помещение,\n"
        "  Остатки.Характеристика КАК Характеристика,\n"
        "  Остатки.Назначение КАК Назначение,\n"
        "  Остатки.Номенклатура.ЕдиницаИзмерения.Наименование КАК Единица,\n"
        "  Остатки.ВНаличииОстаток КАК ВНаличииОстаток,\n"
        "  &Момент КАК Момент\n"
        "ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки(\n"
        "  &Момент,\n"
        "  (&Номенклатура ЕСТЬ NULL ИЛИ Номенклатура = &Номенклатура)\n"
        "  И (&Склады ЕСТЬ NULL ИЛИ Склад В (&Склады))) КАК Остатки\n"
        "ГДЕ НЕ &ЕстьКурсор\n"
        "  ИЛИ Остатки.Склад > &СкладКурсора\n"
        "  ИЛИ (Остатки.Склад = &СкладКурсора\n"
        "    И Остатки.Помещение > &ПомещениеКурсора)\n"
        "  ИЛИ (Остатки.Склад = &СкладКурсора\n"
        "    И Остатки.Помещение = &ПомещениеКурсора\n"
        "    И Остатки.Номенклатура > &НоменклатураКурсора)\n"
        "  ИЛИ (Остатки.Склад = &СкладКурсора\n"
        "    И Остатки.Помещение = &ПомещениеКурсора\n"
        "    И Остатки.Номенклатура = &НоменклатураКурсора\n"
        "    И Остатки.Характеристика > &ХарактеристикаКурсора)\n"
        "  ИЛИ (Остатки.Склад = &СкладКурсора\n"
        "    И Остатки.Помещение = &ПомещениеКурсора\n"
        "    И Остатки.Номенклатура = &НоменклатураКурсора\n"
        "    И Остатки.Характеристика = &ХарактеристикаКурсора\n"
        "    И Остатки.Назначение > &НазначениеКурсора)\n"
        "УПОРЯДОЧИТЬ ПО Остатки.Склад, Остатки.Помещение, "
        "Остатки.Номенклатура, Остатки.Характеристика, Остатки.Назначение"
    )
    row = {
        "Номенклатура": ITEM,
        "Склад": WAREHOUSE,
        "Помещение": ROOM,
        "Характеристика": CHARACTERISTIC,
        "Назначение": ASSIGNMENT,
        "Единица": "шт",
        "ВНаличииОстаток": 7.0,
        "Момент": "2026-07-21T12:00:00+03:00",
    }
    schema = [
        {"name": "Номенклатура", "types": ["СправочникСсылка.Номенклатура"]},
        {"name": "Склад", "types": ["СправочникСсылка.Склады"]},
        {"name": "Помещение", "types": ["СправочникСсылка.СкладскиеПомещения"]},
        {"name": "Характеристика", "types": ["СправочникСсылка.ХарактеристикиНоменклатуры"]},
        {"name": "Назначение", "types": ["СправочникСсылка.Назначения"]},
        {"name": "Единица", "types": ["Строка"]},
        {"name": "ВНаличииОстаток", "types": ["Число"]},
        {"name": "Момент", "types": ["Дата"]},
    ]
    bindings = [{"parameter": "item", "value": ITEM}, {"parameter": "moment", "value": "2026-07-21T12:00:00+03:00"}]
    return base_skill(
        skill_id="ut115.stock.balance",
        name="Фактический остаток подтвержденной номенклатуры",
        purpose="Возвращает физический ВНаличииОстаток регистра ТоварыНаСкладах по моменту и optional exact номенклатуре/списку складов.",
        limitations=["Показывает физическое наличие, а не доступность с учетом резервов."],
        capabilities=[
            "CAP-STOCK-BALANCE",
            "CAP-STOCK-BY-WAREHOUSE",
            "CAP-STOCK-BY-ITEM",
        ],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=STOCK_SHA,
        metadata_requirements=[{"object_name": "РегистрНакопления.ТоварыНаСкладах", "attributes": ["Номенклатура", "Склад", "Помещение", "Характеристика", "Назначение", "ВНаличии"]}],
        intent="data",
        aliases=["фактический остаток товара", "сколько товара в наличии на складе"],
        anti_examples=["сколько товара доступно с учетом резервов"],
        context_types=[],
        parameters=[
            parameter("item", "Номенклатура", "Опциональная подтвержденная exact ссылка номенклатуры.", "entity_ref", ["session_context", "previous_step"], "object_ref", semantic_type="catalog.item", entity_types=["catalog.item"], required=False),
            parameter("warehouses", "Склады", "Опциональный список подтвержденных exact ссылок складов.", "entity_ref_list", ["session_context", "previous_step"], "object_ref", semantic_type="catalog.warehouse", entity_types=["catalog.warehouse"], required=False),
            parameter("moment", "Момент", "Момент расчета остатка с часовым поясом.", "datetime", ["user_slot", "system"], "none"),
        ],
        operation=data_operation(
            "ut115.stock.balance.v1",
            query,
            [
                {"parameter": "item", "query_parameter": "Номенклатура", "encoding": "object_ref"},
                {"parameter": "warehouses", "query_parameter": "Склады", "encoding": "object_ref_list"},
                {"parameter": "moment", "query_parameter": "Момент", "encoding": "datetime"},
            ],
            [
                column("Номенклатура", "item.ref", ["СправочникСсылка.Номенклатура"], "object_ref"),
                column("Склад", "warehouse.ref", ["СправочникСсылка.Склады"], "object_ref"),
                column("Помещение", "storage_bin.ref", ["СправочникСсылка.СкладскиеПомещения"], "object_ref"),
                column("Характеристика", "characteristic.ref", ["СправочникСсылка.ХарактеристикиНоменклатуры"], "object_ref"),
                column("Назначение", "assignment.ref", ["СправочникСсылка.Назначения"], "object_ref"),
                column("Единица", "item.unit", ["Строка"], "string"),
                column("ВНаличииОстаток", "stock.balance", ["Число"], "decimal"),
                column("Момент", "stock.moment", ["Дата"], "datetime"),
            ],
            invariant_constants=[
                {
                    "kind": "null_literal",
                    "statement": 1,
                    "value": "NULL",
                    "role": "computed_value",
                    "occurrences": 2,
                }
            ],
            pagination={
                "strategy": "keyset",
                "has_cursor_query_parameter": "ЕстьКурсор",
                "sort": [
                    {"fact_id": "warehouse.ref", "direction": "asc"},
                    {"fact_id": "storage_bin.ref", "direction": "asc"},
                    {"fact_id": "item.ref", "direction": "asc"},
                    {"fact_id": "characteristic.ref", "direction": "asc"},
                    {"fact_id": "assignment.ref", "direction": "asc"},
                ],
                "cursor_bindings": [
                    {
                        "fact_id": "warehouse.ref",
                        "query_parameter": "СкладКурсора",
                        "encoding": "object_ref",
                    },
                    {
                        "fact_id": "storage_bin.ref",
                        "query_parameter": "ПомещениеКурсора",
                        "encoding": "object_ref",
                    },
                    {
                        "fact_id": "item.ref",
                        "query_parameter": "НоменклатураКурсора",
                        "encoding": "object_ref",
                    },
                    {
                        "fact_id": "characteristic.ref",
                        "query_parameter": "ХарактеристикаКурсора",
                        "encoding": "object_ref",
                    },
                    {
                        "fact_id": "assignment.ref",
                        "query_parameter": "НазначениеКурсора",
                        "encoding": "object_ref",
                    },
                ],
            },
        ),
        output=output_contract("ut115.stock.balance.v1", "many", facts, [item["fact_id"] for item in facts], [item["fact_id"] for item in facts], identities=["item.ref", "warehouse.ref", "storage_bin.ref", "characteristic.ref", "assignment.ref"], zeros=["stock.balance"]),
        dependencies=[],
        tests=[
            mcp_test("ut.stock.positive", bindings, [row], schema, [item["fact_id"] for item in facts]),
            mcp_test(
                "ut.stock.retail-warehouses",
                [
                    {"parameter": "warehouses", "value": [WAREHOUSE]},
                    {"parameter": "moment", "value": "2026-07-21T12:00:00+03:00"},
                ],
                [row],
                schema,
                [item["fact_id"] for item in facts],
            ),
            empty_test("ut.stock.empty", schema),
        ],
        references=[source("configuration_metadata", "ut-config://11.5.27.56/AccumulationRegisters/ТоварыНаСкладах.xml", STOCK_SHA), source("configuration_source", "ut-config://11.5.27.56/Reports/СправочноеРазмещениеНоменклатуры/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml")],
        result_constraints=[
            {
                "kind": "fact_equals_parameter",
                "fact_id": "item.ref",
                "parameter": "item",
            },
            {
                "kind": "fact_in_parameter",
                "fact_id": "warehouse.ref",
                "parameter": "warehouses",
            },
        ],
        version="1.1.0",
    )


def build_order_header() -> dict[str, Any]:
    facts = [
        fact("order.ref", "document.sales_order", "entity_ref", "Заказ клиента", role="entity"),
        fact("order.number", "document.number", "string", "Номер"),
        fact("order.date", "time.document_moment", "datetime", "Дата", role="time"),
        fact("order.posted", "document.posted", "boolean", "Проведен"),
        fact("order.customer", "party.customer", "entity_ref", "Клиент", role="dimension"),
        fact("order.organization", "catalog.organization", "entity_ref", "Организация", role="dimension"),
        fact("order.warehouse", "catalog.warehouse", "entity_ref", "Склад", role="dimension"),
        fact("order.status", "document.sales_order.status", "string", "Статус"),
        fact(
            "order.amount",
            "measure.document_amount",
            "money",
            "Сумма документа",
            role="measure",
            unit={"mode": "from_fact", "fact_id": "order.currency"},
        ),
        fact("order.currency", "currency.code", "string", "Валюта", role="dimension"),
        fact(
            "order.execution_state",
            "document.sales_order.execution_state",
            "string",
            "Состояние исполнения",
            required=False,
            nullable=True,
        ),
        fact(
            "order.payment_percent",
            "measure.sales_order.payment_percent",
            "percentage",
            "Оплачено",
            role="measure",
            required=False,
            unit={"mode": "fixed", "code": "%"},
        ),
        fact(
            "order.shipment_percent",
            "measure.sales_order.shipment_percent",
            "percentage",
            "Отгружено",
            role="measure",
            required=False,
            unit={"mode": "fixed", "code": "%"},
        ),
        fact(
            "order.debt_percent",
            "measure.sales_order.debt_percent",
            "percentage",
            "Долг",
            role="measure",
            required=False,
            unit={"mode": "fixed", "code": "%"},
        ),
        fact(
            "order.state_event_date",
            "time.sales_order_state_moment",
            "datetime",
            "Момент состояния",
            role="time",
            required=False,
        ),
    ]
    query = (
        "ВЫБРАТЬ ПЕРВЫЕ 2\n"
        "  Заказ.Ссылка КАК Заказ,\n"
        "  Заказ.Номер КАК Номер,\n"
        "  Заказ.Дата КАК Дата,\n"
        "  Заказ.Проведен КАК Проведен,\n"
        "  Заказ.Партнер КАК Клиент,\n"
        "  Заказ.Организация КАК Организация,\n"
        "  Заказ.Склад КАК Склад,\n"
        "  ПРЕДСТАВЛЕНИЕ(Заказ.Статус) КАК Статус,\n"
        "  Заказ.СуммаДокумента КАК СуммаДокумента,\n"
        "  Заказ.Валюта.Код КАК Валюта,\n"
        "  ПРЕДСТАВЛЕНИЕ(Состояния.Состояние) КАК СостояниеИсполнения,\n"
        "  ЕСТЬNULL(Состояния.ПроцентОплаты, 0) КАК ПроцентОплаты,\n"
        "  ЕСТЬNULL(Состояния.ПроцентОтгрузки, 0) КАК ПроцентОтгрузки,\n"
        "  ЕСТЬNULL(Состояния.ПроцентДолга, 0) КАК ПроцентДолга,\n"
        "  ЕСТЬNULL(Состояния.ДатаСобытия, Заказ.Дата) КАК ДатаСобытия\n"
        "ИЗ Документ.ЗаказКлиента КАК Заказ\n"
        "  ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.СостоянияЗаказовКлиентов КАК Состояния\n"
        "  ПО Состояния.Заказ = Заказ.Ссылка\n"
        "ГДЕ Заказ.Номер = &Номер\n"
        "УПОРЯДОЧИТЬ ПО Заказ.Дата УБЫВ, Заказ.Ссылка"
    )
    row = {
        "Заказ": ORDER,
        "Номер": "0000-000005",
        "Дата": "2025-02-12T10:00:00+03:00",
        "Проведен": True,
        "Клиент": PARTNER,
        "Организация": ORGANIZATION,
        "Склад": WAREHOUSE,
        "Статус": "К выполнению",
        "СуммаДокумента": 3000.0,
        "Валюта": "RUB",
        "СостояниеИсполнения": "Ожидается обеспечение",
        "ПроцентОплаты": 100.0,
        "ПроцентОтгрузки": 0.0,
        "ПроцентДолга": 0.0,
        "ДатаСобытия": "2025-02-12T10:05:00+03:00",
    }
    schema = [
        {"name": "Заказ", "types": ["ДокументСсылка.ЗаказКлиента"]},
        {"name": "Номер", "types": ["Строка"]},
        {"name": "Дата", "types": ["Дата"]},
        {"name": "Проведен", "types": ["Булево"]},
        {"name": "Клиент", "types": ["СправочникСсылка.Партнеры"]},
        {"name": "Организация", "types": ["СправочникСсылка.Организации"]},
        {"name": "Склад", "types": ["СправочникСсылка.Склады"]},
        {"name": "Статус", "types": ["Строка"]},
        {"name": "СуммаДокумента", "types": ["Число"]},
        {"name": "Валюта", "types": ["Строка"]},
        {"name": "СостояниеИсполнения", "types": ["Строка"]},
        {"name": "ПроцентОплаты", "types": ["Число"]},
        {"name": "ПроцентОтгрузки", "types": ["Число"]},
        {"name": "ПроцентДолга", "types": ["Число"]},
        {"name": "ДатаСобытия", "types": ["Дата"]},
    ]
    required = [item["fact_id"] for item in facts if item["required"]]
    return base_skill(
        skill_id="ut115.sales.order-header-status-by-number",
        version="1.1.0",
        name="Заголовок и статус заказа клиента по номеру",
        purpose="Находит уникальный заказ по typed номеру и возвращает полный заголовок, статус, сумму, валюту и подтвержденные показатели исполнения.",
        limitations=["При нескольких совпадениях требует уточнение и не выбирает заказ автоматически."],
        capabilities=[
            "CAP-COMMON-ENTITY",
            "CAP-SALES-ORDER-HEADER",
            "CAP-SALES-ORDER-STATUS",
        ],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=ORDER_PROFILE_SHA,
        metadata_requirements=[
            {
                "object_name": "Документ.ЗаказКлиента",
                "attributes": [
                    "Ссылка",
                    "Номер",
                    "Дата",
                    "Проведен",
                    "Партнер",
                    "Организация",
                    "Склад",
                    "Статус",
                    "СуммаДокумента",
                    "Валюта",
                ],
            },
            {
                "object_name": "РегистрСведений.СостоянияЗаказовКлиентов",
                "attributes": [
                    "Заказ",
                    "Состояние",
                    "ДатаСобытия",
                    "ПроцентОплаты",
                    "ПроцентОтгрузки",
                    "ПроцентДолга",
                ],
            },
        ],
        intent="data",
        aliases=["найти заказ клиента по номеру", "статус заказа клиента"],
        anti_examples=["изменить статус заказа клиента"],
        context_types=[],
        parameters=[parameter("document_number", "Номер заказа", "Точный номер заказа клиента из вопроса.", "string", ["user_slot"], "trim")],
        operation=data_operation(
            "ut115.sales.order-header-status-by-number.v1",
            query,
            [{"parameter": "document_number", "query_parameter": "Номер", "encoding": "string"}],
            [
                column("Заказ", "order.ref", ["ДокументСсылка.ЗаказКлиента"], "object_ref"),
                column("Номер", "order.number", ["Строка"], "string"),
                column("Дата", "order.date", ["Дата"], "datetime"),
                column("Проведен", "order.posted", ["Булево"], "boolean"),
                column("Клиент", "order.customer", ["СправочникСсылка.Партнеры"], "object_ref"),
                column("Организация", "order.organization", ["СправочникСсылка.Организации"], "object_ref"),
                column("Склад", "order.warehouse", ["СправочникСсылка.Склады"], "object_ref"),
                column("Статус", "order.status", ["Строка"], "string"),
                column("СуммаДокумента", "order.amount", ["Число"], "decimal"),
                column("Валюта", "order.currency", ["Строка"], "string"),
                column("СостояниеИсполнения", "order.execution_state", ["Строка"], "string"),
                column("ПроцентОплаты", "order.payment_percent", ["Число"], "decimal"),
                column("ПроцентОтгрузки", "order.shipment_percent", ["Число"], "decimal"),
                column("ПроцентДолга", "order.debt_percent", ["Число"], "decimal"),
                column("ДатаСобытия", "order.state_event_date", ["Дата"], "datetime"),
            ],
            default_limit=2,
            invariant_constants=[
                {"kind": "structural_integer", "statement": 1, "value": 2, "role": "top_limit", "occurrences": 1},
                {"kind": "zero_boundary", "statement": 1, "value": 0, "role": "null_substitution", "occurrences": 3},
            ],
        ),
        output=output_contract(
            "ut115.sales.order-header-status-by-number.v1",
            "zero_or_one",
            facts,
            required,
            [item["fact_id"] for item in facts],
            identities=["order.ref"],
            renderer="scalar",
            empty="confirmed_not_found",
        ),
        dependencies=[],
        tests=[mcp_test("ut.order-header.unique", [{"parameter": "document_number", "value": "0000-000005"}], [row], schema, required), empty_test("ut.order-header.empty", schema)],
        references=[
            source("configuration_metadata", "ut-config://11.5.27.56/Documents/ЗаказКлиента.xml", ORDER_SHA),
            source("configuration_metadata", "ut-config://11.5.27.56/InformationRegisters/СостоянияЗаказовКлиентов.xml", ORDER_STATE_SHA),
            source("configuration_source", "ut-config://11.5.27.56/CommonModules/ОбменССайтомПереопределяемый/Ext/Module.bsl"),
        ],
    )


def build_order_lines() -> dict[str, Any]:
    facts = [
        fact("order.ref", "document.sales_order", "entity_ref", "Заказ клиента", role="entity"),
        fact("order.line_number", "document.line_number", "integer", "Номер строки", role="dimension"),
        fact("line.item", "catalog.item", "entity_ref", "Номенклатура", role="entity"),
        fact("line.unit", "catalog.item.unit", "string", "Единица", role="dimension"),
        fact("line.quantity", "measure.ordered_quantity", "quantity", "Количество", role="measure", unit={"mode": "from_fact", "fact_id": "line.unit"}),
        fact("line.price", "measure.unit_price", "money", "Цена", role="measure", unit={"mode": "from_fact", "fact_id": "line.currency"}),
        fact("line.currency", "currency.code", "string", "Валюта", role="dimension"),
        fact("line.amount", "measure.line_amount", "money", "Сумма", role="measure", unit={"mode": "from_fact", "fact_id": "line.currency"}),
    ]
    query = (
        "ВЫБРАТЬ\n"
        "  Строки.Ссылка КАК Заказ,\n"
        "  Строки.НомерСтроки КАК НомерСтроки,\n"
        "  Строки.Номенклатура КАК Номенклатура,\n"
        "  Строки.Упаковка.Наименование КАК Единица,\n"
        "  Строки.Количество КАК Количество,\n"
        "  Строки.Цена КАК Цена,\n"
        "  Строки.Ссылка.Валюта.Код КАК Валюта,\n"
        "  Строки.Сумма КАК Сумма\n"
        "ИЗ Документ.ЗаказКлиента.Товары КАК Строки\n"
        "ГДЕ Строки.Ссылка = &Документ\n"
        "  И НЕ Строки.Отменено\n"
        "  И (НЕ &ЕстьКурсор\n"
        "    ИЛИ Строки.Ссылка > &ЗаказКурсора\n"
        "    ИЛИ (Строки.Ссылка = &ЗаказКурсора\n"
        "      И Строки.НомерСтроки > &НомерСтрокиКурсора))\n"
        "УПОРЯДОЧИТЬ ПО Строки.Ссылка, Строки.НомерСтроки"
    )
    row = {"Заказ": ORDER, "НомерСтроки": 1, "Номенклатура": ITEM, "Единица": "шт", "Количество": 2.0, "Цена": 1500.0, "Валюта": "RUB", "Сумма": 3000.0}
    schema = [
        {"name": "Заказ", "types": ["ДокументСсылка.ЗаказКлиента"]},
        {"name": "НомерСтроки", "types": ["Число"]},
        {"name": "Номенклатура", "types": ["СправочникСсылка.Номенклатура"]},
        {"name": "Единица", "types": ["Строка"]},
        {"name": "Количество", "types": ["Число"]},
        {"name": "Цена", "types": ["Число"]},
        {"name": "Валюта", "types": ["Строка"]},
        {"name": "Сумма", "types": ["Число"]},
    ]
    return base_skill(
        skill_id="ut115.sales.order-lines",
        name="Строки подтвержденного заказа клиента",
        purpose="Возвращает неотмененные строки только для переданной exact ссылки заказа клиента.",
        limitations=["Не ищет заказ повторно по номеру или представлению и не включает отмененные строки."],
        capabilities=["CAP-SALES-ORDER-LINES"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=ORDER_SHA,
        metadata_requirements=[{"object_name": "Документ.ЗаказКлиента", "attributes": ["Товары.НомерСтроки", "Товары.Номенклатура", "Товары.Упаковка", "Товары.Количество", "Товары.Цена", "Товары.Сумма", "Товары.Отменено", "Валюта"]}],
        intent="data",
        aliases=["какие товары входят в этот заказ", "строки заказа клиента"],
        anti_examples=["добавить товар в заказ клиента"],
        context_types=["document.sales_order"],
        parameters=[parameter("order", "Заказ клиента", "Exact _objectRef подтвержденного заказа клиента.", "entity_ref", ["session_context", "previous_step"], "object_ref", semantic_type="document.sales_order", entity_types=["document.sales_order"])],
        operation=data_operation(
            "ut115.sales.order-lines.v1",
            query,
            [{"parameter": "order", "query_parameter": "Документ", "encoding": "object_ref"}],
            [
                column("Заказ", "order.ref", ["ДокументСсылка.ЗаказКлиента"], "object_ref"),
                column("НомерСтроки", "order.line_number", ["Число"], "integer"),
                column("Номенклатура", "line.item", ["СправочникСсылка.Номенклатура"], "object_ref"),
                column("Единица", "line.unit", ["Строка"], "string"),
                column("Количество", "line.quantity", ["Число"], "decimal"),
                column("Цена", "line.price", ["Число"], "decimal"),
                column("Валюта", "line.currency", ["Строка"], "string"),
                column("Сумма", "line.amount", ["Число"], "decimal"),
            ],
            pagination={
                "strategy": "keyset",
                "has_cursor_query_parameter": "ЕстьКурсор",
                "sort": [
                    {"fact_id": "order.ref", "direction": "asc"},
                    {"fact_id": "order.line_number", "direction": "asc"},
                ],
                "cursor_bindings": [
                    {
                        "fact_id": "order.ref",
                        "query_parameter": "ЗаказКурсора",
                        "encoding": "object_ref",
                    },
                    {
                        "fact_id": "order.line_number",
                        "query_parameter": "НомерСтрокиКурсора",
                        "encoding": "integer",
                    },
                ],
            },
        ),
        output=output_contract("ut115.sales.order-lines.v1", "many", facts, [item["fact_id"] for item in facts], [item["fact_id"] for item in facts], identities=["order.ref", "order.line_number"]),
        dependencies=[{"skill_id": "ut115.sales.order-header-status-by-number", "version_range": "^1.0.0", "required_fact_types": ["document.sales_order"]}],
        tests=[mcp_test("ut.order-lines.positive", [{"parameter": "order", "value": ORDER}], [row], schema, [item["fact_id"] for item in facts]), empty_test("ut.order-lines.empty", schema)],
        references=[source("configuration_metadata", "ut-config://11.5.27.56/Documents/ЗаказКлиента.xml", ORDER_SHA), source("configuration_source", "ut-config://11.5.27.56/Reports/ОценкаРентабельностиПродаж2_5/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml")],
        result_constraints=[
            {
                "kind": "fact_equals_parameter",
                "fact_id": "order.ref",
                "parameter": "order",
            }
        ],
        version="1.1.0",
    )


def build_shipment_list() -> dict[str, Any]:
    facts = [
        fact(
            "shipment.ref",
            "document.sales_shipment",
            "entity_ref",
            "Реализация",
            role="entity",
        ),
        fact("shipment.number", "document.number", "string", "Номер"),
        fact("shipment.date", "time.document_moment", "datetime", "Дата", role="time"),
        fact("shipment.posted", "document.posted", "boolean", "Проведен"),
        fact(
            "shipment.customer",
            "party.customer",
            "entity_ref",
            "Клиент",
            role="dimension",
        ),
        fact(
            "shipment.organization",
            "catalog.organization",
            "entity_ref",
            "Организация",
            role="dimension",
        ),
        fact(
            "shipment.warehouse",
            "catalog.warehouse",
            "entity_ref",
            "Склад",
            role="dimension",
        ),
        fact("shipment.status", "document.sales_shipment.status", "string", "Статус"),
        fact(
            "shipment.amount",
            "measure.document_amount",
            "money",
            "Сумма документа",
            role="measure",
            unit={"mode": "from_fact", "fact_id": "shipment.currency"},
        ),
        fact(
            "shipment.currency",
            "currency.code",
            "string",
            "Валюта",
            role="dimension",
        ),
        fact(
            "shipment.order",
            "document.sales_order",
            "entity_ref",
            "Заказ клиента",
            role="dimension",
            required=False,
            nullable=True,
        ),
    ]
    query = (
        "ВЫБРАТЬ\n"
        "  Реализация.Ссылка КАК Реализация,\n"
        "  Реализация.Номер КАК Номер,\n"
        "  Реализация.Дата КАК Дата,\n"
        "  Реализация.Проведен КАК Проведен,\n"
        "  Реализация.Партнер КАК Клиент,\n"
        "  Реализация.Организация КАК Организация,\n"
        "  Реализация.Склад КАК Склад,\n"
        "  ПРЕДСТАВЛЕНИЕ(Реализация.Статус) КАК Статус,\n"
        "  Реализация.СуммаДокумента КАК СуммаДокумента,\n"
        "  Реализация.Валюта.Код КАК Валюта,\n"
        "  Реализация.ЗаказКлиента КАК ЗаказКлиента\n"
        "ИЗ Документ.РеализацияТоваровУслуг КАК Реализация\n"
        "ГДЕ Реализация.Дата >= &НачалоПериода\n"
        "  И Реализация.Дата < &КонецПериода\n"
        "  И (&Клиент ЕСТЬ NULL ИЛИ Реализация.Партнер = &Клиент)\n"
        "  И (&Организация ЕСТЬ NULL ИЛИ Реализация.Организация = &Организация)\n"
        "  И (&Склад ЕСТЬ NULL ИЛИ Реализация.Склад = &Склад)\n"
        "  И (НЕ &ЕстьКурсор\n"
        "    ИЛИ Реализация.Дата < &ДатаКурсора\n"
        "    ИЛИ (Реализация.Дата = &ДатаКурсора\n"
        "      И Реализация.Ссылка > &СсылкаКурсора))\n"
        "УПОРЯДОЧИТЬ ПО Реализация.Дата УБЫВ, Реализация.Ссылка"
    )
    schema = [
        {"name": "Реализация", "types": ["ДокументСсылка.РеализацияТоваровУслуг"]},
        {"name": "Номер", "types": ["Строка"]},
        {"name": "Дата", "types": ["Дата"]},
        {"name": "Проведен", "types": ["Булево"]},
        {"name": "Клиент", "types": ["СправочникСсылка.Партнеры"]},
        {"name": "Организация", "types": ["СправочникСсылка.Организации"]},
        {"name": "Склад", "types": ["СправочникСсылка.Склады"]},
        {"name": "Статус", "types": ["Строка"]},
        {"name": "СуммаДокумента", "types": ["Число"]},
        {"name": "Валюта", "types": ["Строка"]},
        {"name": "ЗаказКлиента", "types": ["ДокументСсылка.ЗаказКлиента"]},
    ]
    row = {
        "Реализация": SHIPMENT,
        "Номер": "0000-000031",
        "Дата": "2026-07-15T11:30:00+03:00",
        "Проведен": True,
        "Клиент": PARTNER,
        "Организация": ORGANIZATION,
        "Склад": WAREHOUSE,
        "Статус": "Реализовано",
        "СуммаДокумента": 4500.0,
        "Валюта": "RUB",
        "ЗаказКлиента": ORDER,
    }
    period_value = {
        "start": "2026-07-01T00:00:00+03:00",
        "end_exclusive": "2026-08-01T00:00:00+03:00",
        "timezone": "Europe/Moscow",
        "precision": "month",
    }
    required = [item["fact_id"] for item in facts if item["required"]]
    return base_skill(
        skill_id="ut115.sales.shipment-list",
        name="Список реализаций товаров и услуг",
        purpose=(
            "Возвращает реализации за typed период с exact ссылками, статусом, "
            "суммой, валютой и metadata-proven измерениями."
        ),
        limitations=[
            "Не ранжирует реализации и не вычисляет среднее; slice 2 закрывает "
            "только list component."
        ],
        capabilities=["CAP-SALES-SHIPMENT-LIST"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=SHIPMENT_SHA,
        metadata_requirements=[
            {
                "object_name": "Документ.РеализацияТоваровУслуг",
                "attributes": [
                    "Ссылка",
                    "Номер",
                    "Дата",
                    "Проведен",
                    "Партнер",
                    "Организация",
                    "Склад",
                    "Статус",
                    "СуммаДокумента",
                    "Валюта",
                    "ЗаказКлиента",
                ],
            }
        ],
        intent="data",
        aliases=["показать реализации за период", "список отгрузок клиентам"],
        anti_examples=["показать топ реализаций по сумме"],
        context_types=[],
        parameters=[
            parameter(
                "period",
                "Период",
                "Нормализованный полуинтервал дат реализации.",
                "period",
                ["user_slot", "previous_step"],
                "normalize_period",
            ),
            parameter(
                "customer",
                "Клиент",
                "Опциональная подтвержденная exact ссылка клиента.",
                "entity_ref",
                ["session_context", "previous_step"],
                "object_ref",
                semantic_type="party.customer",
                entity_types=["party.customer"],
                required=False,
            ),
            parameter(
                "organization",
                "Организация",
                "Опциональная подтвержденная exact ссылка организации.",
                "entity_ref",
                ["session_context", "previous_step"],
                "object_ref",
                semantic_type="catalog.organization",
                entity_types=["catalog.organization"],
                required=False,
            ),
            parameter(
                "warehouse",
                "Склад",
                "Опциональная подтвержденная exact ссылка склада.",
                "entity_ref",
                ["session_context", "previous_step"],
                "object_ref",
                semantic_type="catalog.warehouse",
                entity_types=["catalog.warehouse"],
                required=False,
            ),
        ],
        operation=data_operation(
            "ut115.sales.shipment-list.v1",
            query,
            [
                {
                    "parameter": "period",
                    "query_parameter": "НачалоПериода",
                    "encoding": "period_start",
                },
                {
                    "parameter": "period",
                    "query_parameter": "КонецПериода",
                    "encoding": "period_end_exclusive",
                },
                {
                    "parameter": "customer",
                    "query_parameter": "Клиент",
                    "encoding": "object_ref",
                },
                {
                    "parameter": "organization",
                    "query_parameter": "Организация",
                    "encoding": "object_ref",
                },
                {
                    "parameter": "warehouse",
                    "query_parameter": "Склад",
                    "encoding": "object_ref",
                },
            ],
            [
                column(
                    "Реализация",
                    "shipment.ref",
                    ["ДокументСсылка.РеализацияТоваровУслуг"],
                    "object_ref",
                ),
                column("Номер", "shipment.number", ["Строка"], "string"),
                column("Дата", "shipment.date", ["Дата"], "datetime"),
                column("Проведен", "shipment.posted", ["Булево"], "boolean"),
                column(
                    "Клиент",
                    "shipment.customer",
                    ["СправочникСсылка.Партнеры"],
                    "object_ref",
                ),
                column(
                    "Организация",
                    "shipment.organization",
                    ["СправочникСсылка.Организации"],
                    "object_ref",
                ),
                column(
                    "Склад",
                    "shipment.warehouse",
                    ["СправочникСсылка.Склады"],
                    "object_ref",
                ),
                column("Статус", "shipment.status", ["Строка"], "string"),
                column(
                    "СуммаДокумента",
                    "shipment.amount",
                    ["Число"],
                    "decimal",
                ),
                column("Валюта", "shipment.currency", ["Строка"], "string"),
                column(
                    "ЗаказКлиента",
                    "shipment.order",
                    ["ДокументСсылка.ЗаказКлиента"],
                    "object_ref",
                ),
            ],
            invariant_constants=[
                {
                    "kind": "null_literal",
                    "statement": 1,
                    "value": "NULL",
                    "role": "absence_filter",
                    "occurrences": 3,
                }
            ],
            pagination={
                "strategy": "keyset",
                "has_cursor_query_parameter": "ЕстьКурсор",
                "sort": [
                    {"fact_id": "shipment.date", "direction": "desc"},
                    {"fact_id": "shipment.ref", "direction": "asc"},
                ],
                "cursor_bindings": [
                    {
                        "fact_id": "shipment.date",
                        "query_parameter": "ДатаКурсора",
                        "encoding": "datetime",
                    },
                    {
                        "fact_id": "shipment.ref",
                        "query_parameter": "СсылкаКурсора",
                        "encoding": "object_ref",
                    },
                ],
            },
        ),
        output=output_contract(
            "ut115.sales.shipment-list.v1",
            "many",
            facts,
            required,
            [item["fact_id"] for item in facts],
            identities=["shipment.ref"],
            empty="confirmed_no_rows",
        ),
        dependencies=[],
        tests=[
            mcp_test(
                "ut.shipment-list.positive",
                [{"parameter": "period", "value": period_value}],
                [row],
                schema,
                required,
            ),
            empty_test("ut.shipment-list.empty", schema),
        ],
        references=[
            source(
                "configuration_metadata",
                "ut-config://11.5.27.56/Documents/РеализацияТоваровУслуг.xml",
                SHIPMENT_SHA,
            )
        ],
    )


def build_help() -> dict[str, Any]:
    facts = [
        fact("documentation.fragment", "documentation.fragment", "document_fragment", "Фрагмент справки"),
        fact("documentation.citation", "documentation.citation", "source_citation", "Источник", role="provenance"),
    ]
    operation = {
        "kind": "documentation_retrieval",
        "index": "ut_built_in_help",
        "query_parameter": "search_text",
        "retrieval": {"engine": "fts5_bm25_ru_stem_v1", "top_k": 8, "max_chunks_per_source": 3},
        "filters": {"source_kind": "built_in_help", "language": "ru", "metadata_kinds": ["configuration", "subsystem", "catalog", "document", "report", "data_processor", "form", "common_form", "other"], "path_prefixes": []},
        "chunk_roles": ["definition", "procedure", "restriction", "status_meaning"],
        "output_bindings": [
            {"chunk_field": "text", "fact_id": "documentation.fragment"},
            {"chunk_field": "citation", "fact_id": "documentation.citation"},
        ],
    }
    tests = [
        {
            "test_id": "ut.order-help.positive",
            "case_kind": "positive",
            "bindings": [{"parameter": "search_text", "value": "заказ клиента"}],
            "fixture": {
                "kind": "documentation_chunks",
                "chunks": [{
                    "chunk_id": "order-help-definition",
                    "title": "Заказ клиента",
                    "heading": "Заказ клиента",
                    "text": "Заказ клиента - это запрос клиента на поставку ему товаров или оказание услуг в установленные сроки.",
                    "source_uri": "ut-help://11.5.27.56/Documents/ЗаказКлиента/Ext/Help/ru.html#top",
                    "role": "definition",
                }],
            },
            "expected": {"status": "documentation_found", "required_fact_ids": ["documentation.fragment", "documentation.citation"]},
        },
        {
            "test_id": "ut.order-help.empty",
            "case_kind": "negative",
            "bindings": [],
            "fixture": {"kind": "documentation_chunks", "chunks": []},
            "expected": {"status": "documentation_empty", "required_fact_ids": []},
        },
    ]
    return base_skill(
        skill_id="ut115.doc.term",
        name="Определение термина из встроенной справки",
        purpose="Находит определения, ограничения и значения терминов только во встроенной справке УТ закрепленного релиза.",
        limitations=["Не переходит по ссылкам ИТС, v8help или внешним HTTP страницам и не дополняет найденный текст внешними знаниями."],
        capabilities=["CAP-DOC-TERM", "CAP-DOC-SOURCE"],
        fact_types=[item["semantic_type"] for item in facts],
        metadata_sha=HELP_SHA,
        metadata_requirements=[],
        intent="documentation",
        aliases=["что означает термин в УТ", "что такое заказ клиента"],
        anti_examples=["каков фактический статус объекта в базе"],
        context_types=[],
        parameters=[parameter("search_text", "Текст поиска", "Формулировка вопроса для встроенной справки.", "normalized_text", ["user_slot"], "casefold")],
        operation=operation,
        output=output_contract(
            "ut115.doc.term.v1",
            "many",
            facts,
            ["documentation.fragment", "documentation.citation"],
            ["documentation.citation"],
            identities=["documentation.citation"],
            renderer="explanation",
            empty="confirmed_not_found",
        ),
        dependencies=[],
        tests=tests,
        references=[source("built_in_help", "ut-help://11.5.27.56/Documents/ЗаказКлиента/Ext/Help/ru.html#top", HELP_SHA)],
    )


def build_package(
    skills: list[dict[str, Any]],
    *,
    package_id: str,
    author: str,
    description: str,
    release_note: str,
    source_references: list[dict[str, Any]],
) -> dict[str, Any]:
    lock = [
        {
            "skill_id": skill["skill_id"],
            "version": skill["version"],
            "digest": skill["integrity"]["digest"],
        }
        for skill in skills
    ]
    return generate_integrity(
        {
            "schema_version": "1.0.0",
            "document_type": "skill_package",
            "package_id": package_id,
            "version": "1.0.0",
            "display": {
                "name_ru": "Стартовые навыки УТ 11.5.27.56",
                "description_ru": description,
            },
            "target": {
                "configuration_id": "УправлениеТорговлейБазовая",
                "configuration_name": CONFIG_NAME,
                "release": "11.5.27.56",
                "compatibility_mode": "8.3.27",
            },
            "skills": skills,
            "dependency_lock": lock,
            "provenance": {
                "author": author,
                "created_at": CREATED,
                "release_note_ru": release_note,
                "source_references": source_references,
            },
        }
    )


def main() -> None:
    established_skills = [
        build_help(),
        build_item_by_article(),
        build_item_by_code(),
        build_item_by_barcode(),
        build_item_by_name(),
        build_stock(),
        build_order_header(),
        build_order_lines(),
    ]
    skills = [
        *established_skills[:5],
        build_warehouse_resolver(),
        *established_skills[5:],
        build_shipment_list(),
    ]
    package = build_package(
        skills,
        package_id="ut.starter.slice-two",
        author="ChatBot 1C slice 2",
        description=(
            "Замкнутый переносимый пакет slice 2: документация, справочники, "
            "остатки, заказы и реализации."
        ),
        release_note="Production catalog для outcomes, pagination и failure slice 2.",
        source_references=[
            source("configuration_metadata", "ut-config://11.5.27.56/Catalogs/Номенклатура.xml", ITEM_SHA),
            source("configuration_metadata", "ut-config://11.5.27.56/AccumulationRegisters/ТоварыНаСкладах.xml", STOCK_SHA),
            source("configuration_metadata", "ut-config://11.5.27.56/Documents/ЗаказКлиента.xml", ORDER_SHA),
            source("configuration_metadata", "ut-config://11.5.27.56/Catalogs/Склады.xml", WAREHOUSE_SHA),
            source("configuration_metadata", "ut-config://11.5.27.56/Documents/РеализацияТоваровУслуг.xml", SHIPMENT_SHA),
            source("built_in_help", "ut-help://11.5.27.56/Documents/ЗаказКлиента/Ext/Help/ru.html#top", HELP_SHA),
        ],
    )
    harness = ContractHarness.discover(ROOT)
    harness.validate_document(package)
    TARGET.mkdir(parents=True, exist_ok=True)
    expected_paths = {
        TARGET / f"{skill['skill_id']}.skill.json" for skill in skills
    }
    for stale_path in TARGET.glob("*.skill.json"):
        if stale_path not in expected_paths:
            stale_path.unlink()
    for skill in skills:
        path = TARGET / f"{skill['skill_id']}.skill.json"
        path.write_text(json.dumps(skill, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (TARGET / "ut.starter.slice-two.package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
