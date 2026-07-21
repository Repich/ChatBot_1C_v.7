"""Closed typed model for ``schemas/planner-output.schema.json``."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.types import (
    ContextHandle,
    FactId,
    PaginationValue,
    Period,
    RequirementId,
    SemanticType,
    SemVer,
    SkillId,
    StepId,
)


class PlanValueType(StrEnum):
    STRING = "string"
    NORMALIZED_TEXT = "normalized_text"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"
    PERIOD = "period"
    ENUM = "enum"
    ENTITY_REF = "entity_ref"
    ENTITY_REF_LIST = "entity_ref_list"
    MONEY = "money"
    QUANTITY = "quantity"
    PERCENTAGE = "percentage"
    DOCUMENT_FRAGMENT = "document_fragment"
    SOURCE_CITATION = "source_citation"
    PAGINATION = "pagination"


class FactRequirement(ClosedModel):
    requirement_id: RequirementId
    semantic_type: SemanticType
    value_type: PlanValueType
    cardinality: Literal["one", "zero_or_one", "many", "aggregate"]
    required: bool
    unit_dimension: (
        Literal["none", "currency", "quantity_unit", "percentage"] | None
    ) = None
    time_semantics: Literal["none", "moment", "period"] | None = None


class LiteralBinding(ClosedModel):
    source: Literal["literal"]
    value_type: Literal[
        "string",
        "normalized_text",
        "boolean",
        "integer",
        "decimal",
        "date",
        "datetime",
        "period",
        "enum",
        "pagination",
    ]
    value: (
        Annotated[str, Field(max_length=2000)]
        | bool
        | int
        | float
        | Period
        | PaginationValue
    )

    @model_validator(mode="after")
    def value_matches_declared_type(self) -> "LiteralBinding":
        value = self.value
        valid = {
            "string": isinstance(value, str),
            "normalized_text": isinstance(value, str),
            "enum": isinstance(value, str),
            "date": isinstance(value, str),
            "datetime": isinstance(value, str),
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "decimal": type(value) is float,
            "period": isinstance(value, Period),
            "pagination": isinstance(value, PaginationValue),
        }
        if not valid[self.value_type]:
            raise ValueError("literal value does not match value_type")
        return self


class SlotBinding(ClosedModel):
    source: Literal["slot"]
    slot_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class ContextBinding(ClosedModel):
    source: Literal["context"]
    context_handle: ContextHandle
    expected_semantic_type: SemanticType


class StepBinding(ClosedModel):
    source: Literal["step"]
    step_id: StepId
    fact_id: FactId
    cardinality: Literal["one", "many"]


class SystemBinding(ClosedModel):
    source: Literal["system"]
    name: Literal[
        "turn_time", "default_list_limit", "page_cursor", "database_state_marker"
    ]


Binding = Annotated[
    LiteralBinding | SlotBinding | ContextBinding | StepBinding | SystemBinding,
    Field(discriminator="source"),
]


class Slot(ClosedModel):
    slot_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    semantic_type: SemanticType
    value_type: PlanValueType
    status: Literal[
        "resolved_literal",
        "resolved_context",
        "needs_resolution",
        "missing",
        "ambiguous",
    ]
    mentions: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=300)], ...],
        Field(max_length=20),
    ]
    binding: Binding | None = None


class Interpretation(ClosedModel):
    intent_kind: Literal[
        "data",
        "documentation",
        "mixed",
        "product_meta",
        "read_only_violation",
        "out_of_scope",
    ]
    goal_ru: Annotated[str, Field(min_length=3, max_length=1000)]
    required_facts: Annotated[tuple[FactRequirement, ...], Field(max_length=40)]
    slots: Annotated[tuple[Slot, ...], Field(max_length=40)]


class SkillArgument(ClosedModel):
    parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    binding: Binding


class SkillCall(ClosedModel):
    step_id: StepId
    kind: Literal["skill_call"]
    skill_id: SkillId
    skill_version: SemVer
    arguments: Annotated[tuple[SkillArgument, ...], Field(max_length=40)]
    required_output_fact_ids: Annotated[
        tuple[FactId, ...], Field(min_length=1, max_length=100)
    ]
    on_empty: Literal[
        "stop_not_found",
        "continue_as_empty_set",
        "continue_as_zero_only_if_contract_allows",
    ]


class NormalizePeriodOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["normalize_period"]
    expression: Binding
    timezone: Literal["Europe/Moscow"]
    result_fact_id: FactId


class CountOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["count"]
    input_step_id: StepId
    distinct_by_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=20)]
    result_fact_id: FactId


class AggregateOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["aggregate"]
    input_step_id: StepId
    function: Literal["sum", "average", "minimum", "maximum"]
    measure_fact_id: FactId
    group_by_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=20)]
    result_fact_id: FactId


class RankOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["rank"]
    input_step_id: StepId
    sort_fact_id: FactId
    direction: Literal["ascending", "descending"]
    limit: Binding
    ties: Literal["stable_first", "include_all"]


class FilterOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["filter"]
    input_step_id: StepId
    fact_id: FactId
    predicate: Literal[
        "equal",
        "not_equal",
        "greater_than",
        "greater_or_equal",
        "less_than",
        "less_or_equal",
        "is_zero",
        "is_positive",
        "is_negative",
        "is_null",
        "is_not_null",
    ]
    operand: Binding | None = None


class JoinKey(ClosedModel):
    left_fact_id: FactId
    right_fact_id: FactId


class JoinOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["join"]
    left_step_id: StepId
    right_step_id: StepId
    join_type: Literal["inner", "left", "full"]
    keys: Annotated[tuple[JoinKey, ...], Field(min_length=1, max_length=10)]


class InputFactOperand(ClosedModel):
    source: Literal["input_fact"]
    fact_id: FactId


CalculationOperand = Annotated[
    InputFactOperand | LiteralBinding,
    Field(discriminator="source"),
]


class CalculateOperator(ClosedModel):
    step_id: StepId
    kind: Literal["operator_call"]
    operator: Literal["calculate"]
    input_step_id: StepId
    calculation: Literal["add", "subtract", "multiply", "divide", "percentage_change"]
    operands: Annotated[
        tuple[CalculationOperand, ...], Field(min_length=2, max_length=10)
    ]
    result_fact_id: FactId
    result_semantic_type: SemanticType


OperatorStep = Annotated[
    NormalizePeriodOperator
    | CountOperator
    | AggregateOperator
    | RankOperator
    | FilterOperator
    | JoinOperator
    | CalculateOperator,
    Field(discriminator="operator"),
]
PlanStep = SkillCall | OperatorStep


class StepFactRef(ClosedModel):
    step_id: StepId
    fact_id: FactId


class ExecuteResult(ClosedModel):
    kind: Literal["execute"]
    plan_id: UUID
    steps: Annotated[tuple[PlanStep, ...], Field(min_length=1, max_length=16)]
    final_outputs: Annotated[
        tuple[StepFactRef, ...], Field(min_length=1, max_length=40)
    ]


class ClarificationChoice(ClosedModel):
    choice_id: Annotated[str, Field(pattern=r"^c[1-9][0-9]{0,2}$")]
    label_ru: Annotated[str, Field(min_length=1, max_length=160)]
    slot_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    binding: Binding


class ClarifyResult(ClosedModel):
    kind: Literal["clarify"]
    question_ru: Annotated[str, Field(min_length=3, max_length=500)]
    missing_requirement_ids: Annotated[
        tuple[RequirementId, ...], Field(min_length=1, max_length=40)
    ]
    choices: Annotated[tuple[ClarificationChoice, ...], Field(max_length=5)]


class RefuseResult(ClosedModel):
    kind: Literal["refuse"]
    reason_code: Literal["read_only_request", "out_of_scope"]
    message_ru: Annotated[str, Field(min_length=3, max_length=500)]


class CapabilityGapResult(ClosedModel):
    kind: Literal["capability_gap"]
    missing_fact_types: Annotated[
        tuple[SemanticType, ...], Field(min_length=1, max_length=40)
    ]
    message_ru: Annotated[str, Field(min_length=3, max_length=500)]


PlannerResult = Annotated[
    ExecuteResult | ClarifyResult | RefuseResult | CapabilityGapResult,
    Field(discriminator="kind"),
]


class PlannerOutput(ClosedModel):
    schema_version: Literal["1.0.0"]
    document_type: Literal["planner_output"]
    request_id: UUID
    session_context_version: Annotated[int, Field(ge=0)]
    catalog_snapshot_id: UUID
    catalog_revision: Annotated[int, Field(ge=1)]
    decision: Literal["execute", "clarify", "refuse", "capability_gap"]
    interpretation: Interpretation
    result: PlannerResult

    @model_validator(mode="after")
    def decision_matches_result(self) -> "PlannerOutput":
        if self.decision != self.result.kind:
            raise ValueError("decision must match result.kind")
        return self
