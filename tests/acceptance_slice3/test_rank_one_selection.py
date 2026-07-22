from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue, ValidationError

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import (
    ExecutionContext,
    ExecutionResult,
    PlanExecutor,
)
from chatbot1c.application.models import (
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    HelpSearchRequest,
    MetadataEnvelope,
    PinnedCatalog,
)
from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.semantic import (
    build_plan_coverage_proof,
    context_proof_evidence_issues,
)
from chatbot1c.domain.evidence import DatabaseStateMarker, EntityIdentity, Fact
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput, RankOperator
from chatbot1c.domain.skill import Skill
from chatbot1c.domain.types import EntityRef

from .synthetic_package import package_bytes


@dataclass(frozen=True, slots=True)
class _SemanticCase:
    token: str
    semantic_type: str
    physical_type: str
    slot_key: str

    @property
    def resolver_id(self) -> str:
        return f"qa.rank.{self.token}.resolve"

    @property
    def consumer_id(self) -> str:
        return f"qa.rank.{self.token}.inspect"

    @property
    def label_semantic_type(self) -> str:
        return f"{self.semantic_type}.label"

    @property
    def rank_semantic_type(self) -> str:
        return f"{self.semantic_type}.rank_value"

    @property
    def payload_semantic_type(self) -> str:
        return f"{self.semantic_type}.inspection"


CASES = (
    _SemanticCase(
        token="quasar",
        semantic_type="qa.unseen.quasar",
        physical_type="SyntheticRef.UnseenQuasar",
        slot_key="selection.qa_quasar",
    ),
    _SemanticCase(
        token="nebula",
        semantic_type="qa.unseen.nebula",
        physical_type="SyntheticRef.UnseenNebula",
        slot_key="selection.qa_nebula",
    ),
)

REF_FACT_ID = "candidate.ref"
LABEL_FACT_ID = "candidate.label"
RANK_FACT_ID = "candidate.rank"
RANK_UNIT_FACT_ID = "candidate.rank_unit"
PAYLOAD_FACT_ID = "inspection.payload"
REF_COLUMN = "Сущность"
LABEL_COLUMN = "Метка"
RANK_COLUMN = "Ранг"
RANK_UNIT_COLUMN = "ЕдиницаРанга"
PAYLOAD_COLUMN = "Результат"


class _QueueMcp:
    def __init__(self, responses: list[ExecuteQueryEnvelope]) -> None:
        self.responses = list(responses)
        self.requests: list[ExecuteQueryRequest] = []

    async def execute_query(self, request: ExecuteQueryRequest) -> ExecuteQueryEnvelope:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected MCP call")
        return self.responses.pop(0)

    async def get_metadata(self, request: object) -> MetadataEnvelope:
        del request
        raise AssertionError("metadata is not used by rank acceptance")


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


@dataclass(frozen=True, slots=True)
class _FailClosedResult:
    execution: ExecutionResult | None
    error: ApplicationError | None


def _base_skills() -> tuple[dict[str, Any], dict[str, Any]]:
    package = SkillPackage.model_validate_json(package_bytes())
    resolver = next(
        skill
        for skill in package.skills
        if skill.output_contract.resolution is not None
    )
    consumer = next(
        skill
        for skill in package.skills
        if skill.output_contract.resolution is None
        and any(
            parameter.value_type.value == "entity_ref" for parameter in skill.parameters
        )
    )
    return (
        resolver.model_dump(mode="json", by_alias=True),
        consumer.model_dump(mode="json", by_alias=True),
    )


def _resolver_skill(
    case: _SemanticCase,
    *,
    stable_direction: str = "asc",
    prove_total_order: bool = True,
) -> Skill:
    resolver, _ = _base_skills()
    document = copy.deepcopy(resolver)
    document.pop("integrity", None)
    document["skill_id"] = case.resolver_id
    document["display"] = {
        "name_ru": f"Синтетический resolver {case.token}",
        "purpose_ru": "Возвращает полный набор неизвестных core сущностей для rank gate.",
        "limitations_ru": ["Только переносимые синтетические acceptance-данные."],
    }
    document["provides"] = {
        "capability_ids": [f"CAP-QA-RANK-{case.token.upper()}-RESOLVE"],
        "fact_types": [
            case.semantic_type,
            case.label_semantic_type,
            case.rank_semantic_type,
        ],
    }
    document["compatibility"]["required_metadata"] = [
        {
            "object_name": f"Справочник.Qa{case.token.title()}",
            "attributes": [REF_COLUMN, LABEL_COLUMN, RANK_COLUMN],
        }
    ]
    document["selection"] = {
        "intent_kinds": ["data"],
        "aliases_ru": [f"синтетический набор {case.token}"],
        "anti_examples_ru": [f"изменить синтетический набор {case.token}"],
        "required_context_fact_types": [],
    }
    document["parameters"] = [
        {
            "name": "criterion",
            "title_ru": "Синтетический критерий",
            "description_ru": "Непрозрачный критерий fixture resolver-а.",
            "value_type": "normalized_text",
            "required": True,
            "allowed_sources": ["user_slot"],
            "normalization": "trim",
            "context_slot_keys": [],
        }
    ]
    pagination: dict[str, Any]
    if prove_total_order:
        pagination = {
            "strategy": "keyset",
            "has_cursor_query_parameter": "ЕстьКурсор",
            "sort": [
                {"fact_id": RANK_FACT_ID, "direction": stable_direction},
                {"fact_id": REF_FACT_ID, "direction": "asc"},
            ],
            "cursor_bindings": [
                {
                    "fact_id": RANK_FACT_ID,
                    "query_parameter": "РангКурсора",
                    "encoding": "decimal",
                },
                {
                    "fact_id": REF_FACT_ID,
                    "query_parameter": "СсылкаКурсора",
                    "encoding": "object_ref",
                },
            ],
        }
    else:
        pagination = {"strategy": "none"}
    document["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{case.resolver_id}.v1",
            "language": "1c-query",
            "text": (
                f"ВЫБРАТЬ Q.Ссылка КАК {REF_COLUMN}, "
                f"Q.Наименование КАК {LABEL_COLUMN}, Q.Ранг КАК {RANK_COLUMN} "
                f"ИЗ Справочник.Qa{case.token.title()} КАК Q "
                "ГДЕ Q.Критерий = &Критерий "
                "И (НЕ &ЕстьКурсор ИЛИ Q.Ранг > &РангКурсора "
                "ИЛИ (Q.Ранг = &РангКурсора И Q.Ссылка > &СсылкаКурсора)) "
                "УПОРЯДОЧИТЬ ПО Q.Ранг, Q.Ссылка"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [],
            "include_schema": True,
            "mcp_limit": {"default": 20, "maximum": 21},
        },
        "parameter_bindings": [
            {
                "parameter": "criterion",
                "query_parameter": "Критерий",
                "encoding": "string",
            }
        ],
        "column_bindings": [
            {
                "column": REF_COLUMN,
                "fact_id": REF_FACT_ID,
                "accepted_mcp_types": [case.physical_type],
                "converter": "object_ref",
            },
            {
                "column": LABEL_COLUMN,
                "fact_id": LABEL_FACT_ID,
                "accepted_mcp_types": ["Строка"],
                "converter": "string",
            },
            {
                "column": RANK_COLUMN,
                "fact_id": RANK_FACT_ID,
                "accepted_mcp_types": ["Число"],
                "converter": "decimal",
            },
        ],
        "pagination": pagination,
    }
    document["output_contract"] = {
        "contract_id": f"{case.resolver_id}.v1",
        "contract_version": "1.1.0",
        "cardinality": "many",
        "facts": [
            {
                "fact_id": REF_FACT_ID,
                "semantic_type": case.semantic_type,
                "value_type": "entity_ref",
                "role": "entity",
                "required": True,
                "nullable": False,
                "title_ru": "Сущность",
                "unit_contract": {"mode": "not_applicable"},
            },
            {
                "fact_id": LABEL_FACT_ID,
                "semantic_type": case.label_semantic_type,
                "value_type": "string",
                "role": "attribute",
                "required": True,
                "nullable": False,
                "title_ru": "Метка",
                "unit_contract": {"mode": "not_applicable"},
            },
            {
                "fact_id": RANK_FACT_ID,
                "semantic_type": case.rank_semantic_type,
                "value_type": "decimal",
                "role": "measure",
                "required": True,
                "nullable": False,
                "title_ru": "Ранг",
                "unit_contract": {"mode": "not_applicable"},
            },
        ],
        "sufficiency": {
            "required_fact_sets": [[REF_FACT_ID, LABEL_FACT_ID, RANK_FACT_ID]],
            "empty_semantics": "confirmed_not_found",
            "zero_fact_ids": [],
            "truncation_policy": "page_is_complete",
        },
        "renderer": {
            "kind": "table",
            "primary_fact_ids": [LABEL_FACT_ID],
            "column_fact_ids": [REF_FACT_ID, LABEL_FACT_ID, RANK_FACT_ID],
        },
        "row_identity_fact_ids": [REF_FACT_ID],
        "resolution": {
            "protocol": "typed_entity_resolver_v1",
            "identity_fact_id": REF_FACT_ID,
            "candidate_label_fact_ids": [LABEL_FACT_ID],
            "role_proof_fact_ids": [],
            "default_slot_key": case.slot_key,
        },
        "context_export_policy": [
            {
                "fact_id": REF_FACT_ID,
                "slot_key": case.slot_key,
                "mode": "selected_only",
                "lifetime": {"mode": "session"},
                "max_members": 100,
            }
        ],
    }
    document["result_constraints"] = []
    document["dependencies"]["skills"] = []
    document["examples"] = [
        {
            "question_ru": f"найди лучший синтетический объект {case.token}",
            "applicability": "applicable",
            "reason_ru": "Проверяет generic rank-one composition.",
        },
        {
            "question_ru": f"измени синтетический объект {case.token}",
            "applicability": "not_applicable",
            "reason_ru": "Изменение исключено read-only контрактом.",
        },
    ]
    document["tests"] = [
        {
            "test_id": f"{case.resolver_id}.positive",
            "case_kind": "positive",
            "bindings": [{"parameter": "criterion", "value": "opaque"}],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": _resolver_envelope(case, [_row(case, 1, 1.0)]).model_dump(
                    mode="json", by_alias=True
                ),
            },
            "expected": {
                "status": "success_with_rows",
                "required_fact_ids": [REF_FACT_ID, LABEL_FACT_ID, RANK_FACT_ID],
            },
        },
        {
            "test_id": f"{case.resolver_id}.negative",
            "case_kind": "negative",
            "bindings": [{"parameter": "criterion", "value": "missing"}],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": {"success": False, "error": "synthetic failure"},
            },
            "expected": {
                "status": "query_error",
                "required_fact_ids": [],
                "error_code": "QUERY_ERROR",
            },
        },
    ]
    document["provenance"]["change_note_ru"] = (
        "Synthetic unseen resolver for generic rank-one acceptance."
    )
    return Skill.model_validate(generate_integrity(document))


def _unit_rank_resolver_skill(case: _SemanticCase) -> Skill:
    document = _resolver_skill(case).model_dump(mode="json", by_alias=True)
    document.pop("integrity", None)
    document["compatibility"]["required_metadata"][0]["attributes"].append(
        RANK_UNIT_COLUMN
    )
    query = document["operation"]["query_template"]["text"]
    document["operation"]["query_template"]["text"] = query.replace(
        f"Q.Ранг КАК {RANK_COLUMN} ИЗ",
        f"Q.Ранг КАК {RANK_COLUMN}, Q.Валюта КАК {RANK_UNIT_COLUMN} ИЗ",
    )
    document["operation"]["column_bindings"].append(
        {
            "column": RANK_UNIT_COLUMN,
            "fact_id": RANK_UNIT_FACT_ID,
            "accepted_mcp_types": ["Строка"],
            "converter": "string",
        }
    )
    rank_fact = next(
        item
        for item in document["output_contract"]["facts"]
        if item["fact_id"] == RANK_FACT_ID
    )
    rank_fact["value_type"] = "money"
    rank_fact["unit_contract"] = {
        "mode": "from_fact",
        "fact_id": RANK_UNIT_FACT_ID,
    }
    document["output_contract"]["facts"].append(
        {
            "fact_id": RANK_UNIT_FACT_ID,
            "semantic_type": f"{case.semantic_type}.rank_unit",
            "value_type": "string",
            "role": "dimension",
            "required": True,
            "nullable": False,
            "title_ru": "Единица ранга",
            "unit_contract": {"mode": "not_applicable"},
        }
    )
    document["output_contract"]["sufficiency"]["required_fact_sets"][0].append(
        RANK_UNIT_FACT_ID
    )
    document["output_contract"]["renderer"]["column_fact_ids"].append(
        RANK_UNIT_FACT_ID
    )
    return Skill.model_validate(generate_integrity(document))


def _consumer_skill(case: _SemanticCase) -> Skill:
    _, consumer = _base_skills()
    document = copy.deepcopy(consumer)
    document.pop("integrity", None)
    document["skill_id"] = case.consumer_id
    document["display"] = {
        "name_ru": f"Синтетический consumer {case.token}",
        "purpose_ru": "Принимает один exact winner с подтвержденным provenance.",
        "limitations_ru": ["Только exact entity_ref из previous step или context."],
    }
    document["provides"] = {
        "capability_ids": [f"CAP-QA-RANK-{case.token.upper()}-INSPECT"],
        "fact_types": [case.semantic_type, case.payload_semantic_type],
    }
    document["compatibility"]["required_metadata"] = [
        {
            "object_name": f"РегистрСведений.Qa{case.token.title()}Inspection",
            "attributes": [REF_COLUMN, PAYLOAD_COLUMN],
        }
    ]
    document["selection"] = {
        "intent_kinds": ["data"],
        "aliases_ru": [f"проверь выбранный объект {case.token}"],
        "anti_examples_ru": [f"проверь все объекты {case.token}"],
        "required_context_fact_types": [case.semantic_type],
    }
    document["parameters"] = [
        {
            "name": "subject",
            "title_ru": "Выбранная сущность",
            "description_ru": "Exact winner исходного typed resolver-а.",
            "value_type": "entity_ref",
            "required": True,
            "allowed_sources": ["session_context", "previous_step"],
            "normalization": "object_ref",
            "semantic_type": case.semantic_type,
            "entity_types": [case.semantic_type],
            "context_slot_keys": [case.slot_key],
        }
    ]
    document["operation"] = {
        "kind": "data_query",
        "tool": "execute_query",
        "read_only": True,
        "query_template": {
            "template_id": f"{case.consumer_id}.v1",
            "language": "1c-query",
            "text": (
                f"ВЫБРАТЬ &Сущность КАК {REF_COLUMN}, "
                f"ИСТИНА КАК {PAYLOAD_COLUMN} "
                f"ИЗ РегистрСведений.Qa{case.token.title()}Inspection КАК Q"
            ),
            "execution": {
                "kind": "single_select",
                "statement_count": 1,
                "final_statement": 1,
            },
            "invariant_constants": [
                {
                    "kind": "boolean_literal",
                    "statement": 1,
                    "value": True,
                    "role": "computed_flag",
                    "occurrences": 1,
                }
            ],
            "include_schema": True,
            "mcp_limit": {"default": 1, "maximum": 1},
        },
        "parameter_bindings": [
            {
                "parameter": "subject",
                "query_parameter": "Сущность",
                "encoding": "object_ref",
            }
        ],
        "column_bindings": [
            {
                "column": REF_COLUMN,
                "fact_id": REF_FACT_ID,
                "accepted_mcp_types": [case.physical_type],
                "converter": "object_ref",
            },
            {
                "column": PAYLOAD_COLUMN,
                "fact_id": PAYLOAD_FACT_ID,
                "accepted_mcp_types": ["Строка"],
                "converter": "string",
            },
        ],
        "pagination": {"strategy": "none"},
    }
    document["output_contract"] = {
        "contract_id": f"{case.consumer_id}.v1",
        "contract_version": "1.1.0",
        "cardinality": "zero_or_one",
        "facts": [
            {
                "fact_id": REF_FACT_ID,
                "semantic_type": case.semantic_type,
                "value_type": "entity_ref",
                "role": "entity",
                "required": True,
                "nullable": False,
                "title_ru": "Сущность",
                "unit_contract": {"mode": "not_applicable"},
            },
            {
                "fact_id": PAYLOAD_FACT_ID,
                "semantic_type": case.payload_semantic_type,
                "value_type": "string",
                "role": "attribute",
                "required": True,
                "nullable": False,
                "title_ru": "Результат проверки",
                "unit_contract": {"mode": "not_applicable"},
            },
        ],
        "sufficiency": {
            "required_fact_sets": [[REF_FACT_ID, PAYLOAD_FACT_ID]],
            "empty_semantics": "confirmed_no_rows",
            "zero_fact_ids": [],
            "truncation_policy": "error_if_truncated",
        },
        "renderer": {
            "kind": "table",
            "primary_fact_ids": [PAYLOAD_FACT_ID],
            "column_fact_ids": [REF_FACT_ID, PAYLOAD_FACT_ID],
        },
        "row_identity_fact_ids": [REF_FACT_ID],
        "resolution": None,
        "context_export_policy": [],
    }
    document["result_constraints"] = [
        {
            "kind": "fact_equals_parameter",
            "fact_id": REF_FACT_ID,
            "parameter": "subject",
        }
    ]
    document["dependencies"]["skills"] = [
        {
            "skill_id": case.resolver_id,
            "version_range": "^1.1.0",
            "required_fact_types": [case.semantic_type],
        }
    ]
    document["examples"] = [
        {
            "question_ru": f"проверь выбранный объект {case.token}",
            "applicability": "applicable",
            "reason_ru": "Проверяет exact-ref composition после rank.",
        },
        {
            "question_ru": f"измени выбранный объект {case.token}",
            "applicability": "not_applicable",
            "reason_ru": "Изменение исключено read-only контрактом.",
        },
    ]
    ref = _ref(case, 1)
    document["tests"] = [
        {
            "test_id": f"{case.consumer_id}.positive",
            "case_kind": "positive",
            "bindings": [{"parameter": "subject", "value": ref}],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": _consumer_envelope(case, ref).model_dump(
                    mode="json", by_alias=True
                ),
            },
            "expected": {
                "status": "success_with_rows",
                "required_fact_ids": [REF_FACT_ID, PAYLOAD_FACT_ID],
            },
        },
        {
            "test_id": f"{case.consumer_id}.negative",
            "case_kind": "negative",
            "bindings": [{"parameter": "subject", "value": ref}],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": {"success": False, "error": "synthetic failure"},
            },
            "expected": {
                "status": "query_error",
                "required_fact_ids": [],
                "error_code": "QUERY_ERROR",
            },
        },
    ]
    document["provenance"]["change_note_ru"] = (
        "Synthetic exact-ref consumer for generic rank-one acceptance."
    )
    return Skill.model_validate(generate_integrity(document))


def _ref(case: _SemanticCase, number: int) -> dict[str, JsonValue]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": f"00000000-0000-4000-8000-{number:012d}",
        "ТипОбъекта": case.physical_type,
        "Представление": f"Synthetic {case.token} {number}",
    }


def _row(case: _SemanticCase, number: int, rank: float) -> dict[str, JsonValue]:
    return {
        REF_COLUMN: _ref(case, number),
        LABEL_COLUMN: f"Synthetic {case.token} {number}",
        RANK_COLUMN: rank,
    }


def _resolver_envelope(
    case: _SemanticCase,
    rows: list[dict[str, JsonValue]],
    *,
    has_more: bool = False,
    truncated: bool = False,
) -> ExecuteQueryEnvelope:
    return ExecuteQueryEnvelope.model_validate(
        {
            "success": True,
            "data": rows,
            "schema": {
                "columns": [
                    {"name": REF_COLUMN, "types": [case.physical_type]},
                    {"name": LABEL_COLUMN, "types": ["Строка"]},
                    {"name": RANK_COLUMN, "types": ["Число"]},
                ]
            },
            "count": len(rows),
            "has_more": has_more,
            "truncated": truncated,
        }
    )


def _unit_rank_envelope(
    case: _SemanticCase,
    rows: list[tuple[int, float, str]],
) -> ExecuteQueryEnvelope:
    data = []
    for number, rank, unit in rows:
        row = _row(case, number, rank)
        row[RANK_UNIT_COLUMN] = unit
        data.append(row)
    return ExecuteQueryEnvelope.model_validate(
        {
            "success": True,
            "data": data,
            "schema": {
                "columns": [
                    {"name": REF_COLUMN, "types": [case.physical_type]},
                    {"name": LABEL_COLUMN, "types": ["Строка"]},
                    {"name": RANK_COLUMN, "types": ["Число"]},
                    {"name": RANK_UNIT_COLUMN, "types": ["Строка"]},
                ]
            },
            "count": len(data),
        }
    )


def _consumer_envelope(
    case: _SemanticCase, ref: dict[str, JsonValue]
) -> ExecuteQueryEnvelope:
    return ExecuteQueryEnvelope.model_validate(
        {
            "success": True,
            "data": [{REF_COLUMN: ref, PAYLOAD_COLUMN: "inspected"}],
            "schema": {
                "columns": [
                    {"name": REF_COLUMN, "types": [case.physical_type]},
                    {"name": PAYLOAD_COLUMN, "types": ["Строка"]},
                ]
            },
            "count": 1,
        }
    )


def _catalog(
    snapshot_id: UUID, resolver: Skill, consumer: Skill | None = None
) -> PinnedCatalog:
    skills = {resolver.skill_id: resolver}
    if consumer is not None:
        skills[consumer.skill_id] = consumer
    return PinnedCatalog.create(snapshot_id, 1, skills)


def _plan(
    case: _SemanticCase,
    resolver: Skill,
    *,
    consumer: Skill | None,
    direction: str = "descending",
    ties: str = "include_all",
    limit: int = 1,
    direct: bool = False,
) -> PlannerOutput:
    snapshot_id = uuid4()
    steps: list[dict[str, Any]] = [
        {
            "step_id": "s1",
            "kind": "skill_call",
            "skill_id": resolver.skill_id,
            "skill_version": resolver.version,
            "arguments": [
                {
                    "parameter": "criterion",
                    "binding": {
                        "source": "literal",
                        "value_type": "normalized_text",
                        "value": "opaque",
                    },
                }
            ],
            "required_output_fact_ids": [
                REF_FACT_ID,
                LABEL_FACT_ID,
                RANK_FACT_ID,
            ],
            "on_empty": "stop_not_found",
        }
    ]
    if direct:
        final_outputs = [{"step_id": "s1", "fact_id": REF_FACT_ID}]
        requirement = {
            "requirement_id": "r1",
            "semantic_type": case.semantic_type,
            "value_type": "entity_ref",
            "cardinality": "one",
            "required": True,
            "unit_dimension": "none",
            "time_semantics": "none",
        }
    else:
        steps.append(
            {
                "step_id": "s2",
                "kind": "operator_call",
                "operator": "rank",
                "input_step_id": "s1",
                "sort_fact_id": RANK_FACT_ID,
                "direction": direction,
                "limit": {
                    "source": "literal",
                    "value_type": "integer",
                    "value": limit,
                },
                "ties": ties,
            }
        )
        if consumer is None:
            final_outputs = [{"step_id": "s2", "fact_id": REF_FACT_ID}]
            requirement = {
                "requirement_id": "r1",
                "semantic_type": case.semantic_type,
                "value_type": "entity_ref",
                "cardinality": "one",
                "required": True,
                "unit_dimension": "none",
                "time_semantics": "none",
            }
        else:
            steps.append(
                {
                    "step_id": "s3",
                    "kind": "skill_call",
                    "skill_id": consumer.skill_id,
                    "skill_version": consumer.version,
                    "arguments": [
                        {
                            "parameter": "subject",
                            "binding": {
                                "source": "step",
                                "step_id": "s2",
                                "fact_id": REF_FACT_ID,
                                "cardinality": "one",
                            },
                        }
                    ],
                    "required_output_fact_ids": [REF_FACT_ID, PAYLOAD_FACT_ID],
                    "on_empty": "stop_not_found",
                }
            )
            final_outputs = [{"step_id": "s3", "fact_id": PAYLOAD_FACT_ID}]
            requirement = {
                "requirement_id": "r1",
                "semantic_type": case.payload_semantic_type,
                "value_type": "string",
                "cardinality": "zero_or_one",
                "required": True,
                "unit_dimension": "none",
                "time_semantics": "none",
            }
    return PlannerOutput.model_validate(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(uuid4()),
            "session_context_version": 1,
            "catalog_snapshot_id": str(snapshot_id),
            "catalog_revision": 1,
            "decision": "execute",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": "Synthetic unseen rank-one acceptance.",
                "required_facts": [requirement],
                "slots": [],
            },
            "result": {
                "kind": "execute",
                "plan_id": str(uuid4()),
                "steps": steps,
                "final_outputs": final_outputs,
            },
        }
    )


def _context(plan: PlannerOutput, catalog: PinnedCatalog) -> ExecutionContext:
    now = datetime(2038, 4, 5, 6, 7, 8, tzinfo=UTC)
    return ExecutionContext(
        trace_id=uuid4(),
        request_id=plan.request_id,
        session_id=uuid4(),
        turn_id=uuid4(),
        turn_time=now,
        default_list_limit=20,
        catalog=catalog,
        context_facts=(),
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
            documentation_revision="rank-fixture",
            documentation_manifest_digest="3" * 64,
            projection_manifest_digest="4" * 64,
        ),
        deadline_at=now + timedelta(seconds=30),
    )


def _execute(
    plan: PlannerOutput,
    catalog: PinnedCatalog,
    mcp: _QueueMcp,
) -> ExecutionResult:
    executor = PlanExecutor(mcp, _EmptyDocumentation(), _MemoryTraces())
    return asyncio.run(executor.execute(plan, _context(plan, catalog)))


def _execute_fail_closed(
    plan: PlannerOutput,
    catalog: PinnedCatalog,
    mcp: _QueueMcp,
) -> _FailClosedResult:
    try:
        execution = _execute(plan, catalog, mcp)
    except ApplicationError as error:
        assert error.code != "OPERATOR_NOT_IMPLEMENTED"
        return _FailClosedResult(None, error)
    assert execution.selection_proofs == ()
    assert execution.context_facts == ()
    assert execution.evidence.context_exports == ()
    return _FailClosedResult(execution, None)


def _winner_fact(execution: ExecutionResult) -> Fact:
    proof = execution.selection_proofs[0]
    facts = {
        fact.fact_instance_id: fact
        for fact in execution.evidence.facts
        if fact.fact_id == REF_FACT_ID
    }
    return facts[proof.fact_instance_ids[0]]


def _selector_fields(proof: object) -> tuple[object, object]:
    return (
        getattr(proof, "selector_step_id", None),
        getattr(proof, "selector_digest", None),
    )


@pytest.mark.parametrize(
    ("direction", "expected_number"),
    (("descending", 2), ("ascending", 1)),
)
def test_3b_rank_001_002_executes_unique_complete_rank_one(
    direction: str, expected_number: int
) -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer, direction=direction)
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    rows = [_row(case, 3, 20.0), _row(case, 1, 10.0), _row(case, 2, 30.0)]
    expected = _ref(case, expected_number)
    mcp = _QueueMcp(
        [_resolver_envelope(case, rows), _consumer_envelope(case, expected)]
    )

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert len(mcp.requests) == 2
    assert mcp.requests[0].limit == 21
    assert mcp.requests[1].params["Сущность"] == expected
    assert len(execution.selection_proofs) == 1
    proof = execution.selection_proofs[0]
    assert proof.state == "selected_one"
    assert proof.resolver.step_id == "s1"
    assert _selector_fields(proof) == ("s2", getattr(proof, "selector_digest"))
    assert isinstance(getattr(proof, "selector_digest", None), str)
    assert len(getattr(proof, "selector_digest")) == 64
    winner = _winner_fact(execution)
    assert isinstance(winner.value, EntityRef)
    assert winner.value.model_dump(mode="json", by_alias=True) == expected
    assert winner.step_id == "s1"
    assert winner.source_locator.kind == "query_column_binding"
    source_step = next(step for step in execution.steps if step.step_id == "s1")
    rank_step = next(step for step in execution.steps if step.step_id == "s2")
    assert source_step.collection_scope == "complete_set"
    assert rank_step.collection_scope == "complete_set"
    rank_evidence = next(
        step for step in execution.evidence.steps if step.step_id == "s2"
    )
    assert rank_evidence.source_kind == "deterministic_operator"
    assert rank_evidence.operation_ref == "operator:rank"
    assert rank_evidence.produced_fact_instance_ids == (winner.fact_instance_id,)
    assert len(execution.evidence.facts) == len(
        {fact.fact_instance_id for fact in execution.evidence.facts}
    )
    assert execution.evidence.coverage.sufficient is True
    assert execution.evidence.coverage.requirements[0].status is CoverageStatus.COVERED
    assert len(execution.evidence.context_exports) == 1
    assert execution.context_facts[0].origin.skill_id == resolver.skill_id
    assert (
        execution.context_facts[0].origin.fact.fact_instance_id
        == winner.fact_instance_id
    )
    non_winner_ids = {
        fact.fact_instance_id
        for fact in execution.evidence.facts
        if fact.step_id == "s1"
        and fact.fact_id == REF_FACT_ID
        and fact.fact_instance_id != winner.fact_instance_id
    }
    assert non_winner_ids.isdisjoint(
        {item.fact_instance_id for item in execution.evidence.context_exports}
    )


def test_3b_rank_003_stable_first_is_permutation_stable_with_total_order() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case, stable_direction="asc")
    winners: list[dict[str, JsonValue]] = []
    for rows in (
        [_row(case, 2, 5.0), _row(case, 1, 5.0), _row(case, 3, 8.0)],
        [_row(case, 3, 8.0), _row(case, 1, 5.0), _row(case, 2, 5.0)],
    ):
        plan = _plan(
            case,
            resolver,
            consumer=None,
            direction="ascending",
            ties="stable_first",
        )
        catalog = _catalog(plan.catalog_snapshot_id, resolver)
        execution = _execute(plan, catalog, _QueueMcp([_resolver_envelope(case, rows)]))
        winner = _winner_fact(execution)
        assert isinstance(winner.value, EntityRef)
        winners.append(winner.value.model_dump(mode="json", by_alias=True))
    assert winners == [_ref(case, 1), _ref(case, 1)]


def test_3b_rank_003_stable_first_without_total_order_fails_closed() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case, prove_total_order=False)
    plan = _plan(
        case,
        resolver,
        consumer=None,
        direction="ascending",
        ties="stable_first",
    )
    catalog = _catalog(plan.catalog_snapshot_id, resolver)
    mcp = _QueueMcp(
        [_resolver_envelope(case, [_row(case, 1, 5.0), _row(case, 2, 5.0)])]
    )

    result = _execute_fail_closed(plan, catalog, mcp)

    assert len(mcp.requests) == 1
    if result.execution is not None:
        assert result.execution.outcome is Outcome.CONTRACT_ERROR


def test_3b_rank_003_include_all_top_tie_never_selects_one() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(
        case,
        resolver,
        consumer=consumer,
        direction="descending",
        ties="include_all",
    )
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    mcp = _QueueMcp(
        [_resolver_envelope(case, [_row(case, 1, 9.0), _row(case, 2, 9.0)])]
    )

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.CLARIFICATION_REQUIRED
    assert execution.selection_proofs == ()
    assert execution.context_facts == ()
    assert execution.evidence.context_exports == ()
    assert len(mcp.requests) == 1


@pytest.mark.parametrize("flag", ("has_more", "truncated"))
def test_3b_rank_004_transport_incomplete_flags_block_rank_and_consumer(
    flag: str,
) -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer)
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    options = {flag: True}
    source = _resolver_envelope(
        case,
        [_row(case, 1, 100.0), _row(case, 2, 10.0)],
        **options,
    )
    mcp = _QueueMcp([source])

    result = _execute_fail_closed(plan, catalog, mcp)

    assert len(mcp.requests) == 1
    if result.execution is not None:
        source_step = next(
            step for step in result.execution.steps if step.step_id == "s1"
        )
        assert source_step.has_more is True
        assert source_step.truncated is True
        assert source_step.collection_scope == "visible_page"


def test_3b_rank_004_first_twenty_rows_are_not_a_global_universe() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case, stable_direction="asc")
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    visible_false_top = [_row(case, number, float(number)) for number in range(1, 21)]
    probe_global_top = _row(case, 21, 999.0)
    mcp = _QueueMcp([_resolver_envelope(case, [*visible_false_top, probe_global_top])])

    result = _execute_fail_closed(plan, catalog, mcp)

    assert len(mcp.requests) == 1
    assert mcp.requests[0].limit == 21
    if result.execution is not None:
        source_step = next(
            step for step in result.execution.steps if step.step_id == "s1"
        )
        assert source_step.row_count == 20
        assert source_step.has_more is True
        assert source_step.truncated is True
        assert source_step.collection_scope == "visible_page"
        exported_values = {
            item.value["УникальныйИдентификатор"]
            for item in result.execution.context_facts
            if isinstance(item.value, dict)
        }
        assert _ref(case, 20)["УникальныйИдентификатор"] not in exported_values


def test_3b_rank_004_zero_candidates_stop_without_proof_or_downstream() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer)
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    mcp = _QueueMcp([_resolver_envelope(case, [])])

    execution = _execute(plan, catalog, mcp)

    assert execution.outcome is Outcome.SUCCESS_EMPTY
    assert execution.selection_proofs == ()
    assert execution.context_facts == ()
    assert execution.evidence.context_exports == ()
    assert len(mcp.requests) == 1


@pytest.mark.parametrize(
    "mutation",
    ("missing_rank", "conflicting_duplicate", "limit_not_one"),
)
def test_3b_rank_005_invalid_rank_contract_fails_before_selection(
    mutation: str,
) -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    limit = 2 if mutation == "limit_not_one" else 1
    plan = _plan(case, resolver, consumer=consumer, limit=limit)
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    rows = [_row(case, 1, 10.0), _row(case, 2, 20.0)]
    if mutation == "missing_rank":
        rows[0][RANK_COLUMN] = None
    elif mutation == "conflicting_duplicate":
        rows[1][REF_COLUMN] = copy.deepcopy(rows[0][REF_COLUMN])
    mcp = _QueueMcp([_resolver_envelope(case, rows)])

    result = _execute_fail_closed(plan, catalog, mcp)

    assert len(mcp.requests) == 1
    if result.execution is not None:
        assert result.execution.outcome is Outcome.CONTRACT_ERROR


def test_3b_rank_005_plan_schema_rejects_invalid_direction_and_ties() -> None:
    operator = {
        "step_id": "s2",
        "kind": "operator_call",
        "operator": "rank",
        "input_step_id": "s1",
        "sort_fact_id": RANK_FACT_ID,
        "direction": "sideways",
        "limit": {"source": "literal", "value_type": "integer", "value": 1},
        "ties": "random",
    }
    with pytest.raises(ValidationError):
        RankOperator.model_validate(operator)


def test_3b_rank_005_matching_resolved_units_are_comparable() -> None:
    case = CASES[0]
    resolver = _unit_rank_resolver_skill(case)
    plan = _plan(case, resolver, consumer=None, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver)

    execution = _execute(
        plan,
        catalog,
        _QueueMcp([_unit_rank_envelope(case, [(1, 10.0, "RUB"), (2, 20.0, "RUB")])]),
    )

    winner = _winner_fact(execution)
    assert isinstance(winner.value, EntityRef)
    assert winner.value.model_dump(mode="json", by_alias=True) == _ref(case, 2)


def test_3b_rank_005_mixed_units_fail_before_selection() -> None:
    case = CASES[0]
    resolver = _unit_rank_resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    mcp = _QueueMcp(
        [_unit_rank_envelope(case, [(1, 10.0, "RUB"), (2, 20.0, "USD")])]
    )

    result = _execute_fail_closed(plan, catalog, mcp)

    assert len(mcp.requests) == 1
    if result.execution is not None:
        assert result.execution.outcome is Outcome.CONTRACT_ERROR


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.token)
def test_3b_rank_006_reuses_winner_for_two_unseen_semantics(
    case: _SemanticCase,
) -> None:
    resolver = _resolver_skill(case)
    consumer = _consumer_skill(case)
    plan = _plan(case, resolver, consumer=consumer, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver, consumer)
    expected = _ref(case, 7)
    mcp = _QueueMcp(
        [
            _resolver_envelope(
                case,
                [_row(case, 4, 1.0), _row(case, 7, 99.0), _row(case, 9, 3.0)],
            ),
            _consumer_envelope(case, expected),
        ]
    )

    execution = _execute(plan, catalog, mcp)

    assert len(mcp.requests) == 2
    assert mcp.requests[1].params["Сущность"] == expected
    assert len(execution.context_facts) == 1
    context = execution.context_facts[0]
    assert context.semantic_type == case.semantic_type
    assert context.slot_key == case.slot_key
    assert context.origin.skill_id == resolver.skill_id
    assert context.origin.column == REF_COLUMN
    assert context.origin.accepted_mcp_types == (case.physical_type,)


def test_3b_rank_006_final_required_entity_is_covered_through_rank() -> None:
    case = CASES[1]
    resolver = _resolver_skill(case)
    plan = _plan(case, resolver, consumer=None, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver)

    execution = _execute(
        plan,
        catalog,
        _QueueMcp([_resolver_envelope(case, [_row(case, 5, 4.0), _row(case, 6, 8.0)])]),
    )

    requirement = execution.evidence.coverage.requirements[0]
    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert execution.evidence.coverage.sufficient is True
    assert requirement.status is CoverageStatus.COVERED
    assert (
        requirement.fact_instance_ids == execution.selection_proofs[0].fact_instance_ids
    )
    rank_step = next(step for step in execution.evidence.steps if step.step_id == "s2")
    assert rank_step.produced_fact_instance_ids == requirement.fact_instance_ids


def test_3b_rank_005_selector_and_non_winner_proof_tampering_is_rejected() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    plan = _plan(case, resolver, consumer=None, direction="descending")
    catalog = _catalog(plan.catalog_snapshot_id, resolver)
    execution = _execute(
        plan,
        catalog,
        _QueueMcp([_resolver_envelope(case, [_row(case, 1, 2.0), _row(case, 2, 9.0)])]),
    )
    proof = execution.selection_proofs[0]
    source_refs = [
        fact
        for fact in execution.evidence.facts
        if fact.step_id == "s1" and fact.fact_id == REF_FACT_ID
    ]
    non_winner = next(
        fact
        for fact in source_refs
        if fact.fact_instance_id not in proof.fact_instance_ids
    )
    assert isinstance(non_winner.value, EntityRef)
    forged_identity = EntityIdentity(
        semantic_type=non_winner.semantic_type,
        physical_type=non_winner.value.object_type,
        unique_id=non_winner.value.unique_id,
    )
    tampered = (
        proof.model_copy(update={"selector_step_id": "s1"}),
        proof.model_copy(update={"selector_digest": "0" * 64}),
        proof.model_copy(
            update={
                "fact_instance_ids": (non_winner.fact_instance_id,),
                "identities": (forged_identity,),
            }
        ),
    )
    coverage = build_plan_coverage_proof(plan, tuple(catalog.skills.values()))
    for forged in tampered:
        codes = {
            issue.code
            for issue in context_proof_evidence_issues(
                coverage,
                execution.evidence,
                selection_proofs=(forged,),
                filter_retention_proofs=execution.filter_retention_proofs,
                available_skills=tuple(catalog.skills.values()),
            )
        }
        assert "CONTEXT_SELECTION_PROOF_INVALID" in codes


def test_direct_resolver_selection_non_regression() -> None:
    case = CASES[0]
    resolver = _resolver_skill(case)
    plan = _plan(case, resolver, consumer=None, direct=True)
    catalog = _catalog(plan.catalog_snapshot_id, resolver)

    execution = _execute(
        plan, catalog, _QueueMcp([_resolver_envelope(case, [_row(case, 1, 7.0)])])
    )

    assert execution.outcome is Outcome.SUCCESS_WITH_ROWS
    assert len(execution.selection_proofs) == 1
    assert _selector_fields(execution.selection_proofs[0]) == (None, None)
    assert len(execution.context_facts) == 1
    assert all(
        step.operation_ref != "operator:rank" for step in execution.evidence.steps
    )
