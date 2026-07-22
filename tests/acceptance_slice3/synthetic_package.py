from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_SKILL = "qa.synthetic.asset.resolve"
DETAIL_SKILL = "qa.synthetic.asset.snapshot"
SET_SKILL = "qa.synthetic.asset.batch"
PHYSICAL_TYPE = "СправочникСсылка.СинтетическийАктив"
FIXED_MOMENT = "2037-11-19T08:17:43.123456+03:00"
METADATA_DIGEST = hashlib.sha256(b"slice3-synthetic-metadata-v1").hexdigest()


def package_bytes() -> bytes:
    skills = [_resolver(), _detail(), _batch()]
    signed_skills = [_signed_document(skill) for skill in skills]
    package: dict[str, Any] = {
        "schema_version": "1.1.0",
        "document_type": "skill_package",
        "package_id": "qa.synthetic.entity-context",
        "version": "1.1.0",
        "display": {
            "name_ru": "Синтетическая приемка entity/context",
            "description_ru": (
                "Переносимый пакет с неизвестной core сущностью, resolver, "
                "точным consumer и retained snapshot."
            ),
        },
        "target": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "configuration_name": ("1С:Управление торговлей (базовая), редакция 11"),
            "release": "11.5.27.56",
            "compatibility_mode": "8.3.27",
        },
        "skills": signed_skills,
        "dependency_lock": [
            {
                "skill_id": skill["skill_id"],
                "version": skill["version"],
                "digest": skill["integrity"]["digest"],
            }
            for skill in signed_skills
        ],
        "provenance": {
            "author": "Independent slice 3 acceptance",
            "created_at": "2026-07-21T00:00:00Z",
            "release_note_ru": (
                "Black-box portable proof for typed generic resolver/context core."
            ),
            "source_references": [
                {
                    "kind": "configuration_metadata",
                    "uri": "test-evidence://slice3/synthetic-metadata",
                    "sha256": METADATA_DIGEST,
                }
            ],
        },
    }
    return _encode(_signed_document(package))


def legacy_package_bytes() -> bytes:
    package = json.loads(package_bytes())
    package.pop("integrity")
    package["schema_version"] = "1.0.0"
    package["version"] = "1.0.0"
    converted = []
    for original in package["skills"]:
        skill = copy.deepcopy(original)
        skill.pop("integrity")
        skill["schema_version"] = "1.0.0"
        for parameter in skill["parameters"]:
            parameter.pop("context_slot_keys", None)
        skill["output_contract"].pop("resolution", None)
        skill["output_contract"].pop("context_export_policy", None)
        converted.append(_signed_document(skill))
    package["skills"] = converted
    package["dependency_lock"] = [
        {
            "skill_id": skill["skill_id"],
            "version": skill["version"],
            "digest": skill["integrity"]["digest"],
        }
        for skill in converted
    ]
    return _encode(_signed_document(package))


def invalid_policy_package(mutation: str) -> bytes:
    package = json.loads(package_bytes())
    package.pop("integrity")
    skill = next(item for item in package["skills"] if item["skill_id"] == ASSET_SKILL)
    skill.pop("integrity")
    policy = skill["output_contract"]["context_export_policy"][0]
    if mutation == "entity_as_scalar":
        policy["mode"] = "confirmed_filter"
        policy.pop("max_members")
        policy["semantic_type"] = "synthetic.asset"
        policy["value_type"] = "datetime"
    elif mutation == "unknown_fact":
        policy["fact_id"] = "asset.missing"
    else:
        raise ValueError(mutation)
    signed = _signed_document(skill)
    package["skills"] = [
        signed if item["skill_id"] == ASSET_SKILL else item
        for item in package["skills"]
    ]
    package["dependency_lock"] = [
        {
            "skill_id": item["skill_id"],
            "version": item["version"],
            "digest": item["integrity"]["digest"],
        }
        for item in package["skills"]
    ]
    return _encode(_signed_document(package))


def _resolver() -> dict[str, Any]:
    skill = _base_skill(
        ASSET_SKILL,
        "Разрешение ультрамаринового артефакта",
        "Ищет неизвестный core тип synthetic asset по переносимому контракту.",
    )
    skill["provides"] = {
        "capability_ids": ["CAP-QA-SYNTHETIC-ASSET-RESOLVE"],
        "fact_types": [
            "synthetic.asset",
            "synthetic.asset.name",
            "synthetic.asset.code",
        ],
    }
    skill["compatibility"]["required_metadata"] = [
        {
            "object_name": "Справочник.СинтетическиеАктивы",
            "attributes": ["Ссылка", "Наименование", "Код"],
        }
    ]
    skill["selection"] = {
        "intent_kinds": ["data"],
        "aliases_ru": [
            "найти ультрамариновый артефакт",
            "показать лазурные активы",
        ],
        "anti_examples_ru": ["изменить ультрамариновый артефакт"],
        "required_context_fact_types": [],
    }
    skill["parameters"] = [
        {
            "name": "name_fragment",
            "title_ru": "Фрагмент имени",
            "description_ru": "Типизированный фрагмент имени synthetic asset.",
            "value_type": "normalized_text",
            "required": True,
            "allowed_sources": ["user_slot"],
            "normalization": "like_contains",
            "context_slot_keys": [],
        }
    ]
    skill["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{ASSET_SKILL}.v1",
            "language": "1c-query",
            "text": (
                "ВЫБРАТЬ\n"
                "  Активы.Ссылка КАК Актив,\n"
                "  Активы.Наименование КАК Наименование,\n"
                "  Активы.Код КАК Код\n"
                "ИЗ Справочник.СинтетическиеАктивы КАК Активы\n"
                'ГДЕ Активы.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"\n'
                "  И (НЕ &ЕстьКурсор\n"
                "    ИЛИ Активы.Наименование > &ИмяКурсора\n"
                "    ИЛИ (Активы.Наименование = &ИмяКурсора\n"
                "      И Активы.Ссылка > &СсылкаКурсора))\n"
                "УПОРЯДОЧИТЬ ПО Активы.Наименование, Активы.Ссылка"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [],
            "include_schema": True,
            "mcp_limit": {"default": 20, "maximum": 1000},
        },
        "parameter_bindings": [
            {
                "parameter": "name_fragment",
                "query_parameter": "Шаблон",
                "encoding": "like_contains",
            }
        ],
        "column_bindings": [
            {
                "column": "Актив",
                "fact_id": "asset.ref",
                "accepted_mcp_types": [PHYSICAL_TYPE],
                "converter": "object_ref",
            },
            {
                "column": "Наименование",
                "fact_id": "asset.name",
                "accepted_mcp_types": ["Строка"],
                "converter": "string",
            },
            {
                "column": "Код",
                "fact_id": "asset.code",
                "accepted_mcp_types": ["Строка"],
                "converter": "string",
            },
        ],
        "pagination": {
            "strategy": "keyset",
            "has_cursor_query_parameter": "ЕстьКурсор",
            "sort": [
                {"fact_id": "asset.name", "direction": "asc"},
                {"fact_id": "asset.ref", "direction": "asc"},
            ],
            "cursor_bindings": [
                {
                    "fact_id": "asset.name",
                    "query_parameter": "ИмяКурсора",
                    "encoding": "string",
                },
                {
                    "fact_id": "asset.ref",
                    "query_parameter": "СсылкаКурсора",
                    "encoding": "object_ref",
                },
            ],
        },
    }
    skill["output_contract"] = {
        "contract_id": f"{ASSET_SKILL}.v1",
        "contract_version": "1.1.0",
        "cardinality": "many",
        "facts": [
            _fact("asset.ref", "synthetic.asset", "entity_ref", "entity", "Актив"),
            _fact("asset.name", "synthetic.asset.name", "string", "attribute", "Имя"),
            _fact("asset.code", "synthetic.asset.code", "string", "attribute", "Код"),
        ],
        "sufficiency": {
            "required_fact_sets": [["asset.ref", "asset.name", "asset.code"]],
            "empty_semantics": "confirmed_not_found",
            "zero_fact_ids": [],
            "truncation_policy": "page_is_complete",
        },
        "renderer": {
            "kind": "table",
            "primary_fact_ids": ["asset.name"],
            "column_fact_ids": ["asset.ref", "asset.name", "asset.code"],
        },
        "row_identity_fact_ids": ["asset.ref"],
        "resolution": {
            "protocol": "typed_entity_resolver_v1",
            "identity_fact_id": "asset.ref",
            "candidate_label_fact_ids": ["asset.name", "asset.code"],
            "role_proof_fact_ids": [],
            "default_slot_key": "selection.synthetic_asset",
        },
        "context_export_policy": [
            {
                "fact_id": "asset.ref",
                "slot_key": "selection.synthetic_asset",
                "mode": "selected_only",
                "lifetime": {"mode": "session"},
                "max_members": 100,
            }
        ],
    }
    skill["result_constraints"] = []
    skill["dependencies"] = _dependencies([])
    skill["examples"] = _examples(
        "где лазурный ультрамариновый артефакт?",
        "изменить лазурный артефакт?",
    )
    skill["tests"] = [
        _positive_test(
            ASSET_SKILL,
            [{"parameter": "name_fragment", "value": "лазур"}],
            {
                "Актив": _ref(1),
                "Наименование": "Лазурный актив 1",
                "Код": "SYN-001",
            },
            ["asset.ref", "asset.name", "asset.code"],
            [
                {"name": "Актив", "types": [PHYSICAL_TYPE]},
                {"name": "Наименование", "types": ["Строка"]},
                {"name": "Код", "types": ["Строка"]},
            ],
        ),
        _negative_test(ASSET_SKILL, [{"parameter": "name_fragment", "value": "x"}]),
    ]
    return skill


def _detail() -> dict[str, Any]:
    skill = _base_skill(
        DETAIL_SKILL,
        "Снимок ультрамаринового артефакта",
        "Получает значение exact synthetic asset в подтвержденный момент.",
    )
    skill["provides"] = {
        "capability_ids": ["CAP-QA-SYNTHETIC-ASSET-SNAPSHOT"],
        "fact_types": [
            "synthetic.asset",
            "synthetic.asset.name",
            "synthetic.snapshot",
            "synthetic.snapshot.value",
        ],
    }
    skill["compatibility"]["required_metadata"] = [
        {
            "object_name": "РегистрСведений.СинтетическиеСнимки",
            "attributes": ["Актив", "Момент", "Значение"],
        }
    ]
    skill["selection"] = {
        "intent_kinds": ["data"],
        "aliases_ru": ["значение этого ультрамаринового артефакта"],
        "anti_examples_ru": ["изменить значение артефакта"],
        "required_context_fact_types": ["synthetic.asset"],
    }
    skill["parameters"] = [
        {
            "name": "asset",
            "title_ru": "Актив",
            "description_ru": "Exact подтвержденный synthetic asset.",
            "value_type": "entity_ref",
            "required": True,
            "allowed_sources": ["session_context", "previous_step"],
            "normalization": "object_ref",
            "semantic_type": "synthetic.asset",
            "entity_types": ["synthetic.asset"],
            "context_slot_keys": ["selection.synthetic_asset"],
        },
        {
            "name": "moment",
            "title_ru": "Момент снимка",
            "description_ru": "Exact retained snapshot moment.",
            "value_type": "datetime",
            "required": True,
            "allowed_sources": ["user_slot", "session_context", "system"],
            "normalization": "none",
            "semantic_type": "synthetic.snapshot",
            "context_slot_keys": ["filter.synthetic_snapshot"],
        },
    ]
    skill["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{DETAIL_SKILL}.v1",
            "language": "1c-query",
            "text": (
                "ВЫБРАТЬ\n"
                "  Снимки.Актив КАК Актив,\n"
                "  Снимки.Актив.Наименование КАК Наименование,\n"
                "  &Момент КАК Момент,\n"
                "  Снимки.Значение КАК Значение\n"
                "ИЗ РегистрСведений.СинтетическиеСнимки КАК Снимки\n"
                "ГДЕ Снимки.Актив = &Актив\n"
                "  И Снимки.Момент = &Момент"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [],
            "include_schema": True,
            "mcp_limit": {"default": 1, "maximum": 1},
        },
        "parameter_bindings": [
            {
                "parameter": "asset",
                "query_parameter": "Актив",
                "encoding": "object_ref",
            },
            {
                "parameter": "moment",
                "query_parameter": "Момент",
                "encoding": "datetime",
            },
        ],
        "column_bindings": [
            {
                "column": "Актив",
                "fact_id": "asset.ref",
                "accepted_mcp_types": [PHYSICAL_TYPE],
                "converter": "object_ref",
            },
            {
                "column": "Наименование",
                "fact_id": "asset.name",
                "accepted_mcp_types": ["Строка"],
                "converter": "string",
            },
            {
                "column": "Момент",
                "fact_id": "snapshot.moment",
                "accepted_mcp_types": ["Дата"],
                "converter": "datetime",
            },
            {
                "column": "Значение",
                "fact_id": "snapshot.value",
                "accepted_mcp_types": ["Число"],
                "converter": "decimal",
            },
        ],
        "pagination": {"strategy": "none"},
    }
    skill["output_contract"] = {
        "contract_id": f"{DETAIL_SKILL}.v1",
        "contract_version": "1.1.0",
        "cardinality": "zero_or_one",
        "facts": [
            _fact("asset.ref", "synthetic.asset", "entity_ref", "entity", "Актив"),
            _fact("asset.name", "synthetic.asset.name", "string", "attribute", "Имя"),
            _fact(
                "snapshot.moment", "synthetic.snapshot", "datetime", "time", "Момент"
            ),
            _fact(
                "snapshot.value",
                "synthetic.snapshot.value",
                "decimal",
                "measure",
                "Значение",
            ),
        ],
        "sufficiency": {
            "required_fact_sets": [
                ["asset.ref", "asset.name", "snapshot.moment", "snapshot.value"]
            ],
            "empty_semantics": "confirmed_not_found",
            "zero_fact_ids": ["snapshot.value"],
            "truncation_policy": "page_is_complete",
        },
        "renderer": {
            "kind": "table",
            "primary_fact_ids": ["asset.name", "snapshot.value"],
            "column_fact_ids": [
                "asset.ref",
                "asset.name",
                "snapshot.moment",
                "snapshot.value",
            ],
        },
        "row_identity_fact_ids": ["asset.ref", "snapshot.moment"],
        "resolution": None,
        "context_export_policy": [
            {
                "fact_id": "snapshot.moment",
                "slot_key": "filter.synthetic_snapshot",
                "mode": "confirmed_filter",
                "semantic_type": "synthetic.snapshot",
                "value_type": "datetime",
                "lifetime": {"mode": "session"},
            }
        ],
    }
    skill["result_constraints"] = [
        {"kind": "fact_equals_parameter", "fact_id": "asset.ref", "parameter": "asset"}
    ]
    skill["dependencies"] = _dependencies([ASSET_SKILL])
    skill["examples"] = _examples(
        "каково значение этого лазурного артефакта?",
        "удалить этот артефакт?",
    )
    row = {
        "Актив": _ref(1),
        "Наименование": "Лазурный актив 1",
        "Момент": FIXED_MOMENT,
        "Значение": 17.25,
    }
    skill["tests"] = [
        _positive_test(
            DETAIL_SKILL,
            [
                {"parameter": "asset", "value": _ref(1)},
                {"parameter": "moment", "value": FIXED_MOMENT},
            ],
            row,
            ["asset.ref", "asset.name", "snapshot.moment", "snapshot.value"],
            [
                {"name": "Актив", "types": [PHYSICAL_TYPE]},
                {"name": "Наименование", "types": ["Строка"]},
                {"name": "Момент", "types": ["Дата"]},
                {"name": "Значение", "types": ["Число"]},
            ],
        ),
        _negative_test(
            DETAIL_SKILL,
            [
                {"parameter": "asset", "value": _ref(1)},
                {"parameter": "moment", "value": FIXED_MOMENT},
            ],
        ),
    ]
    return skill


def _batch() -> dict[str, Any]:
    skill = _base_skill(
        SET_SKILL,
        "Количество выбранных ультрамариновых артефактов",
        "Принимает только доказанное complete set synthetic assets.",
    )
    skill["provides"] = {
        "capability_ids": ["CAP-QA-SYNTHETIC-ASSET-BATCH"],
        "fact_types": ["synthetic.asset.count"],
    }
    skill["compatibility"]["required_metadata"] = [
        {
            "object_name": "РегистрСведений.СинтетическиеНаборы",
            "attributes": ["Актив", "Значение"],
        }
    ]
    skill["selection"] = {
        "intent_kinds": ["data"],
        "aliases_ru": ["посчитать выбранные ультрамариновые артефакты"],
        "anti_examples_ru": ["выбрать первый артефакт"],
        "required_context_fact_types": [],
    }
    skill["parameters"] = [
        {
            "name": "assets",
            "title_ru": "Активы",
            "description_ru": "Complete selected set synthetic assets.",
            "value_type": "entity_ref_list",
            "required": True,
            "allowed_sources": ["session_context", "previous_step"],
            "normalization": "object_ref",
            "semantic_type": "synthetic.asset",
            "entity_types": ["synthetic.asset"],
            "context_slot_keys": ["selection.synthetic_asset"],
            "constraints": {"max_items": 100},
        }
    ]
    skill["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{SET_SKILL}.v1",
            "language": "1c-query",
            "text": (
                "ВЫБРАТЬ\n  КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Наборы.Актив) КАК Количество\n"
                "ИЗ РегистрСведений.СинтетическиеНаборы КАК Наборы\n"
                "ГДЕ Наборы.Актив В (&Активы)"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [],
            "include_schema": True,
            "mcp_limit": {"default": 1, "maximum": 1},
        },
        "parameter_bindings": [
            {
                "parameter": "assets",
                "query_parameter": "Активы",
                "encoding": "object_ref_list",
            }
        ],
        "column_bindings": [
            {
                "column": "Количество",
                "fact_id": "asset.count",
                "accepted_mcp_types": ["Число"],
                "converter": "integer",
            }
        ],
        "pagination": {"strategy": "none"},
    }
    skill["output_contract"] = {
        "contract_id": f"{SET_SKILL}.v1",
        "contract_version": "1.1.0",
        "cardinality": "aggregate",
        "facts": [
            _fact(
                "asset.count",
                "synthetic.asset.count",
                "integer",
                "measure",
                "Количество",
            )
        ],
        "sufficiency": {
            "required_fact_sets": [["asset.count"]],
            "empty_semantics": "error_if_empty",
            "zero_fact_ids": ["asset.count"],
            "truncation_policy": "page_is_complete",
        },
        "renderer": {
            "kind": "scalar",
            "primary_fact_ids": ["asset.count"],
            "column_fact_ids": ["asset.count"],
        },
        "row_identity_fact_ids": [],
        "resolution": None,
        "context_export_policy": [],
    }
    skill["result_constraints"] = []
    skill["dependencies"] = _dependencies([ASSET_SKILL])
    skill["examples"] = _examples(
        "сколько лазурных артефактов выбрано?",
        "покажи один случайный артефакт?",
    )
    skill["tests"] = [
        _positive_test(
            SET_SKILL,
            [{"parameter": "assets", "value": [_ref(1), _ref(2)]}],
            {"Количество": 2},
            ["asset.count"],
            [{"name": "Количество", "types": ["Число"]}],
            status="success_with_rows",
        ),
        _negative_test(SET_SKILL, [{"parameter": "assets", "value": [_ref(1)]}]),
    ]
    return skill


def _base_skill(skill_id: str, name: str, purpose: str) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "document_type": "skill",
        "skill_id": skill_id,
        "version": "1.1.0",
        "display": {
            "name_ru": name,
            "purpose_ru": purpose,
            "limitations_ru": ["Только read-only synthetic acceptance data."],
        },
        "compatibility": {
            "configuration_id": "УправлениеТорговлейБазовая",
            "configuration_name": ("1С:Управление торговлей (базовая), редакция 11"),
            "release_range": {
                "minimum": "11.5.27.56",
                "maximum": "11.5.27.56",
                "include_minimum": True,
                "include_maximum": True,
            },
            "compatibility_modes": ["8.3.27"],
            "required_metadata": [],
            "metadata_snapshot_sha256": METADATA_DIGEST,
        },
        "provenance": {
            "author": "Independent slice 3 acceptance",
            "created_at": "2026-07-21T00:00:00Z",
            "change_note_ru": "Portable synthetic acceptance contract.",
            "source_references": [
                {
                    "kind": "configuration_metadata",
                    "uri": "test-evidence://slice3/synthetic-metadata",
                    "sha256": METADATA_DIGEST,
                }
            ],
            "source_configuration": {
                "configuration_id": "УправлениеТорговлейБазовая",
                "release": "11.5.27.56",
                "compatibility_mode": "8.3.27",
                "metadata_snapshot_sha256": METADATA_DIGEST,
            },
        },
    }


def _fact(
    fact_id: str,
    semantic_type: str,
    value_type: str,
    role: str,
    title: str,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "semantic_type": semantic_type,
        "value_type": value_type,
        "role": role,
        "required": True,
        "nullable": False,
        "title_ru": title,
        "unit_contract": {"mode": "not_applicable"},
    }


def _dependencies(skills: list[str]) -> dict[str, Any]:
    return {
        "runtime_contracts": [
            {"contract": "skill-runtime", "version_range": "^1.0.0"},
            {"contract": "mcp.execute_query", "version_range": "^1.0.0"},
        ],
        "skills": [
            {
                "skill_id": skill_id,
                "version_range": "^1.1.0",
                "required_fact_types": ["synthetic.asset"],
            }
            for skill_id in skills
        ],
    }


def _examples(positive: str, negative: str) -> list[dict[str, Any]]:
    return [
        {
            "question_ru": positive,
            "applicability": "applicable",
            "reason_ru": "Проверяет portable synthetic contract.",
        },
        {
            "question_ru": negative,
            "applicability": "not_applicable",
            "reason_ru": "Операция изменения запрещена read-only контрактом.",
        },
    ]


def _positive_test(
    skill_id: str,
    bindings: list[dict[str, Any]],
    row: dict[str, Any],
    facts: list[str],
    columns: list[dict[str, Any]],
    *,
    status: str = "success_with_rows",
) -> dict[str, Any]:
    return {
        "test_id": f"{skill_id}.fixture-positive",
        "case_kind": "positive",
        "bindings": bindings,
        "fixture": {
            "kind": "mcp_execute_query",
            "response": {
                "success": True,
                "data": [row],
                "schema": {"columns": columns},
                "count": 1,
            },
        },
        "expected": {"status": status, "required_fact_ids": facts},
    }


def _negative_test(skill_id: str, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "test_id": f"{skill_id}.fixture-negative",
        "case_kind": "negative",
        "bindings": bindings,
        "fixture": {
            "kind": "mcp_execute_query",
            "response": {"success": False, "error": "synthetic query failure"},
        },
        "expected": {
            "status": "query_error",
            "required_fact_ids": [],
            "error_code": "QUERY_ERROR",
        },
    }


def _ref(index: int) -> dict[str, Any]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": f"00000000-0000-4000-8000-{index:012d}",
        "ТипОбъекта": PHYSICAL_TYPE,
        "Представление": f"Лазурный актив {index}",
    }


def _signed_document(document: dict[str, Any]) -> dict[str, Any]:
    unsigned = copy.deepcopy(document)
    unsigned.pop("integrity", None)
    digest = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    return {
        **unsigned,
        "integrity": {
            "algorithm": "sha256",
            "canonicalization": "RFC8785",
            "scope": "document_without_integrity",
            "digest": digest,
        },
    }


def _encode(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
