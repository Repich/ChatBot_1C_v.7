"""Closed typed model for ``schemas/skill.schema.json``."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.types import (
    FactId,
    FourPartVersion,
    Integrity,
    SemanticType,
    SemVer,
    Sha256,
    SkillId,
)


class ParameterValueType(StrEnum):
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
    PAGINATION = "pagination"


class FactValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    PERIOD = "period"
    ENUM = "enum"
    ENTITY_REF = "entity_ref"
    MONEY = "money"
    QUANTITY = "quantity"
    PERCENTAGE = "percentage"
    DOCUMENT_FRAGMENT = "document_fragment"
    SOURCE_CITATION = "source_citation"


class SkillDisplay(ClosedModel):
    name_ru: Annotated[str, Field(min_length=3, max_length=160)]
    purpose_ru: Annotated[str, Field(min_length=10, max_length=1000)]
    limitations_ru: Annotated[
        tuple[Annotated[str, Field(min_length=3, max_length=500)], ...],
        Field(max_length=20),
    ]


class Provides(ClosedModel):
    capability_ids: Annotated[
        tuple[Annotated[str, Field(pattern=r"^CAP-[A-Z0-9]+(?:-[A-Z0-9]+)+$")], ...],
        Field(min_length=1),
    ]
    fact_types: Annotated[tuple[SemanticType, ...], Field(min_length=1)]


class ReleaseRange(ClosedModel):
    minimum: FourPartVersion
    maximum: FourPartVersion
    include_minimum: bool
    include_maximum: bool

    @model_validator(mode="after")
    def ordered(self) -> "ReleaseRange":
        minimum = tuple(int(part) for part in self.minimum.split("."))
        maximum = tuple(int(part) for part in self.maximum.split("."))
        if minimum > maximum:
            raise ValueError("minimum release must not be greater than maximum")
        return self


class MetadataRequirement(ClosedModel):
    object_name: Annotated[str, Field(max_length=240, pattern=r"^[^.\s]+\.[^.\s]+$")]
    attributes: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=240)], ...],
        Field(max_length=100),
    ]


class Compatibility(ClosedModel):
    configuration_id: Annotated[
        str,
        Field(max_length=160, pattern=r"^[A-Za-zА-Яа-яЁё0-9_]+$"),
    ]
    configuration_name: Annotated[str, Field(min_length=3, max_length=300)]
    release_range: ReleaseRange
    compatibility_modes: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")], ...],
        Field(min_length=1),
    ]
    required_metadata: Annotated[tuple[MetadataRequirement, ...], Field(max_length=100)]
    metadata_snapshot_sha256: Sha256 | None = None


class Selection(ClosedModel):
    intent_kinds: Annotated[
        tuple[Literal["data", "documentation", "mixed"], ...], Field(min_length=1)
    ]
    aliases_ru: Annotated[
        tuple[Annotated[str, Field(min_length=2, max_length=160)], ...],
        Field(min_length=1, max_length=50),
    ]
    anti_examples_ru: Annotated[
        tuple[Annotated[str, Field(min_length=3, max_length=300)], ...],
        Field(min_length=1, max_length=30),
    ]
    required_context_fact_types: Annotated[
        tuple[SemanticType, ...], Field(max_length=20)
    ]


class ParameterConstraints(ClosedModel):
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: Annotated[int, Field(ge=0)] | None = None
    max_length: Annotated[int, Field(ge=1)] | None = None
    pattern: Annotated[str, Field(max_length=300)] | None = None
    max_items: Annotated[int, Field(ge=1, le=1000)] | None = None


class Parameter(ClosedModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    title_ru: Annotated[str, Field(min_length=2, max_length=120)]
    description_ru: Annotated[str, Field(min_length=3, max_length=500)]
    value_type: ParameterValueType
    required: bool
    allowed_sources: Annotated[
        tuple[Literal["user_slot", "session_context", "previous_step", "system"], ...],
        Field(min_length=1),
    ]
    semantic_type: SemanticType | None = None
    entity_types: Annotated[tuple[SemanticType, ...], Field(min_length=1)] | None = None
    allowed_values: (
        Annotated[
            tuple[Annotated[str, Field(max_length=160)], ...], Field(min_length=1)
        ]
        | None
    ) = None
    normalization: Literal[
        "none",
        "trim",
        "casefold",
        "like_contains",
        "start_of_day",
        "end_exclusive",
        "normalize_period",
        "object_ref",
        "page_request",
    ]
    constraints: ParameterConstraints | None = None
    default: JsonValue = None
    context_slot_keys: (
        Annotated[
            tuple[
                Annotated[
                    str,
                    Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"),
                ],
                ...,
            ],
            Field(max_length=20),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def conditional_contract(self) -> "Parameter":
        if self.value_type in {
            ParameterValueType.ENTITY_REF,
            ParameterValueType.ENTITY_REF_LIST,
        } and (self.semantic_type is None or not self.entity_types):
            raise ValueError("entity parameters require semantic_type and entity_types")
        if self.value_type is ParameterValueType.ENUM and not self.allowed_values:
            raise ValueError("enum parameters require allowed_values")
        return self


class McpLimit(ClosedModel):
    default: Annotated[int, Field(ge=1, le=1000)]
    maximum: Annotated[int, Field(ge=1, le=1000)]

    @model_validator(mode="after")
    def default_within_maximum(self) -> "McpLimit":
        if self.default > self.maximum:
            raise ValueError("default MCP limit must not exceed maximum")
        return self


class SingleSelectExecution(ClosedModel):
    kind: Literal["single_select"]
    statement_count: Literal[1]
    final_statement: Literal[1]


class TemporaryTableContract(ClosedModel):
    name: Annotated[
        str,
        Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]{0,127}$"),
    ]
    producer_statement: Annotated[int, Field(ge=1, le=15)]
    consumer_statements: Annotated[
        tuple[Annotated[int, Field(ge=2, le=16)], ...],
        Field(min_length=1, max_length=15),
    ]


class LinkedTempBatchExecution(ClosedModel):
    kind: Literal["linked_temp_batch"]
    statement_count: Annotated[int, Field(ge=2, le=16)]
    final_statement: Annotated[int, Field(ge=2, le=16)]
    temporary_tables: Annotated[
        tuple[TemporaryTableContract, ...], Field(min_length=1, max_length=15)
    ]


QueryExecution = Annotated[
    SingleSelectExecution | LinkedTempBatchExecution,
    Field(discriminator="kind"),
]


class ZeroBoundaryInvariant(ClosedModel):
    kind: Literal["zero_boundary"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: Literal[0]
    role: Literal[
        "zero_equality",
        "sign_boundary",
        "null_substitution",
        "arithmetic_identity",
    ]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class BooleanInvariant(ClosedModel):
    kind: Literal["boolean_literal"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: bool
    role: Literal["state_filter", "computed_flag", "null_substitution"]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class NullInvariant(ClosedModel):
    kind: Literal["null_literal"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: Literal["NULL", "НЕОПРЕДЕЛЕНО"]
    role: Literal["absence_filter", "null_substitution", "computed_value"]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class EmptyInvariant(ClosedModel):
    kind: Literal["empty_literal"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: Literal[""]
    role: Literal["absence_filter", "null_substitution", "computed_value"]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class MetadataConstantInvariant(ClosedModel):
    kind: Literal["metadata_constant"]
    statement: Annotated[int, Field(ge=1, le=16)]
    constant_kind: Literal["enum_member", "predefined_reference", "empty_reference"]
    symbol: Annotated[
        str,
        Field(
            min_length=3,
            max_length=300,
            pattern=(
                r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*"
                r"(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)+$"
            ),
        ),
    ]
    role: Literal[
        "state_filter", "type_discriminator", "absence_sentinel", "computed_value"
    ]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class StructuralIntegerInvariant(ClosedModel):
    kind: Literal["structural_integer"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: Annotated[int, Field(ge=1, le=1000)]
    role: Literal["top_limit", "rank_limit", "query_language_arity"]
    occurrences: Annotated[int, Field(ge=1, le=100)]


class UnitScaleInvariant(ClosedModel):
    kind: Literal["unit_scale"]
    statement: Annotated[int, Field(ge=1, le=16)]
    value: Annotated[int | float, Field(gt=0, le=1_000_000_000)]
    role: Literal["percentage_scale", "unit_conversion"]
    occurrences: Annotated[int, Field(ge=1, le=100)]


InvariantConstant = Annotated[
    ZeroBoundaryInvariant
    | BooleanInvariant
    | NullInvariant
    | EmptyInvariant
    | MetadataConstantInvariant
    | StructuralIntegerInvariant
    | UnitScaleInvariant,
    Field(discriminator="kind"),
]


class QueryTemplate(ClosedModel):
    template_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")]
    language: Literal["1c-query"]
    text: Annotated[str, Field(min_length=10, max_length=50000)]
    execution: QueryExecution
    invariant_constants: Annotated[tuple[InvariantConstant, ...], Field(max_length=64)]
    include_schema: Literal[True]
    mcp_limit: McpLimit


class ParameterBinding(ClosedModel):
    parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    query_parameter: Annotated[
        str, Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
    ]
    encoding: Literal[
        "string",
        "like_contains",
        "boolean",
        "integer",
        "decimal",
        "date",
        "datetime",
        "period_start",
        "period_end_exclusive",
        "object_ref",
        "object_ref_list",
    ]


class ColumnBinding(ClosedModel):
    column: Annotated[str, Field(min_length=1, max_length=160)]
    fact_id: FactId
    accepted_mcp_types: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=200)], ...],
        Field(min_length=1),
    ]
    converter: Literal[
        "identity",
        "string",
        "integer",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "object_ref",
    ]


class NoPagination(ClosedModel):
    strategy: Literal["none"]


class PrefixPagination(ClosedModel):
    strategy: Literal["prefix"]
    stable_order_fact_ids: Annotated[
        tuple[FactId, ...], Field(min_length=1, max_length=10)
    ]
    maximum_total: Annotated[int, Field(ge=1, le=1000)]


class SortItem(ClosedModel):
    fact_id: FactId
    direction: Literal["asc", "desc"]


class CursorBinding(ClosedModel):
    fact_id: FactId
    query_parameter: Annotated[
        str, Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
    ]
    encoding: Literal["string", "integer", "decimal", "date", "datetime", "object_ref"]


class KeysetPagination(ClosedModel):
    strategy: Literal["keyset"]
    has_cursor_query_parameter: Annotated[
        str, Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
    ]
    sort: Annotated[tuple[SortItem, ...], Field(min_length=1, max_length=5)]
    cursor_bindings: Annotated[
        tuple[CursorBinding, ...], Field(min_length=1, max_length=5)
    ]


PaginationPolicy = Annotated[
    NoPagination | PrefixPagination | KeysetPagination,
    Field(discriminator="strategy"),
]


class DataQueryOperation(ClosedModel):
    kind: Literal["data_query"]
    tool: Literal["execute_query"]
    read_only: Literal[True]
    query_template: QueryTemplate
    parameter_bindings: Annotated[tuple[ParameterBinding, ...], Field(max_length=60)]
    column_bindings: Annotated[
        tuple[ColumnBinding, ...], Field(min_length=1, max_length=100)
    ]
    pagination: PaginationPolicy


class RetrievalPolicy(ClosedModel):
    engine: Literal["fts5_bm25_ru_stem_v1"]
    top_k: Annotated[int, Field(ge=1, le=20)]
    max_chunks_per_source: Annotated[int, Field(ge=1, le=10)]


class DocumentationFilters(ClosedModel):
    source_kind: Literal["built_in_help"]
    language: Literal["ru"]
    metadata_kinds: Annotated[
        tuple[
            Literal[
                "configuration",
                "subsystem",
                "catalog",
                "document",
                "report",
                "data_processor",
                "form",
                "common_form",
                "other",
            ],
            ...,
        ],
        Field(max_length=9),
    ]
    path_prefixes: Annotated[
        tuple[
            Annotated[
                str,
                Field(
                    max_length=300,
                    pattern=r"^(?![A-Za-z]:)(?!/)(?!.*\.\.).+$",
                ),
            ],
            ...,
        ],
        Field(max_length=100),
    ]


class DocumentationOutputBinding(ClosedModel):
    chunk_field: Literal["text", "title", "heading", "citation", "role"]
    fact_id: FactId


class DocumentationRetrievalOperation(ClosedModel):
    kind: Literal["documentation_retrieval"]
    index: Literal["ut_built_in_help"]
    query_parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    retrieval: RetrievalPolicy
    filters: DocumentationFilters
    chunk_roles: Annotated[
        tuple[
            Literal[
                "definition",
                "procedure",
                "prerequisite",
                "restriction",
                "error_cause",
                "verification_action",
                "status_meaning",
                "navigation",
            ],
            ...,
        ],
        Field(min_length=1, max_length=8),
    ]
    output_bindings: Annotated[
        tuple[DocumentationOutputBinding, ...], Field(min_length=2, max_length=5)
    ]


SkillOperation = Annotated[
    DataQueryOperation | DocumentationRetrievalOperation,
    Field(discriminator="kind"),
]


class UnitNotApplicable(ClosedModel):
    mode: Literal["not_applicable"]


class UnitFixed(ClosedModel):
    mode: Literal["fixed"]
    code: Annotated[str, Field(min_length=1, max_length=40)]


class UnitFromFact(ClosedModel):
    mode: Literal["from_fact"]
    fact_id: FactId


UnitContract = Annotated[
    UnitNotApplicable | UnitFixed | UnitFromFact,
    Field(discriminator="mode"),
]


class FactDefinition(ClosedModel):
    fact_id: FactId
    semantic_type: SemanticType
    value_type: FactValueType
    role: Literal["entity", "dimension", "measure", "attribute", "time", "provenance"]
    required: bool
    nullable: bool
    title_ru: Annotated[str, Field(min_length=1, max_length=120)]
    unit_contract: UnitContract
    allowed_values: (
        Annotated[
            tuple[Annotated[str, Field(max_length=160)], ...], Field(min_length=1)
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def enum_domain(self) -> "FactDefinition":
        if (self.value_type is FactValueType.ENUM) != (self.allowed_values is not None):
            raise ValueError("enum facts require an exact allowed_values domain")
        return self


class Sufficiency(ClosedModel):
    required_fact_sets: Annotated[
        tuple[
            Annotated[tuple[FactId, ...], Field(min_length=1, max_length=100)],
            ...,
        ],
        Field(min_length=1, max_length=20),
    ]
    empty_semantics: Literal[
        "confirmed_not_found", "confirmed_no_rows", "not_applicable", "error_if_empty"
    ]
    zero_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=100)]
    truncation_policy: Literal[
        "page_is_complete", "partial_until_all_pages", "error_if_truncated"
    ]


class Renderer(ClosedModel):
    kind: Literal[
        "scalar", "table", "ranked_list", "timeline", "procedure", "explanation"
    ]
    primary_fact_ids: Annotated[tuple[FactId, ...], Field(min_length=1, max_length=100)]
    column_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=100)]


class TypedEntityResolution(ClosedModel):
    protocol: Literal["typed_entity_resolver_v1"]
    identity_fact_id: FactId
    candidate_label_fact_ids: Annotated[
        tuple[FactId, ...], Field(min_length=1, max_length=10)
    ]
    role_proof_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=10)]
    default_slot_key: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    ]


class SessionLifetime(ClosedModel):
    mode: Literal["session"]


class TurnLifetime(ClosedModel):
    mode: Literal["turn"]


class UntilLifetime(ClosedModel):
    mode: Literal["until"]
    expires_at_fact_id: FactId


ContextLifetime = Annotated[
    SessionLifetime | TurnLifetime | UntilLifetime,
    Field(discriminator="mode"),
]


class SelectedOnlyContextPolicy(ClosedModel):
    fact_id: FactId
    slot_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")]
    mode: Literal["selected_only"]
    lifetime: ContextLifetime
    max_members: Annotated[int, Field(ge=1, le=100)]


class ConfirmedFilterContextPolicy(ClosedModel):
    fact_id: FactId
    slot_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")]
    mode: Literal["confirmed_filter"]
    semantic_type: SemanticType
    value_type: Literal["datetime", "period", "enum"]
    lifetime: ContextLifetime


ContextExportPolicy = Annotated[
    SelectedOnlyContextPolicy | ConfirmedFilterContextPolicy,
    Field(discriminator="mode"),
]


class OutputContract(ClosedModel):
    contract_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{2,159}$")]
    contract_version: SemVer
    cardinality: Literal["exactly_one", "zero_or_one", "many", "aggregate"]
    row_identity_fact_ids: (
        Annotated[tuple[FactId, ...], Field(max_length=10)] | None
    ) = None
    facts: Annotated[tuple[FactDefinition, ...], Field(min_length=1, max_length=100)]
    sufficiency: Sufficiency
    renderer: Renderer
    resolution: TypedEntityResolution | None = None
    context_export_policy: (
        Annotated[tuple[ContextExportPolicy, ...], Field(max_length=20)] | None
    ) = None


class FactEqualsParameterConstraint(ClosedModel):
    kind: Literal["fact_equals_parameter"]
    fact_id: FactId
    parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class FactInParameterConstraint(ClosedModel):
    kind: Literal["fact_in_parameter"]
    fact_id: FactId
    parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


ResultConstraint = Annotated[
    FactEqualsParameterConstraint | FactInParameterConstraint,
    Field(discriminator="kind"),
]


class RuntimeContract(ClosedModel):
    contract: Literal["skill-runtime", "mcp.execute_query", "help-index"]
    version_range: Annotated[
        str,
        Field(
            pattern=(
                r"^(\^|~|>=|<=|>|<|=)?[0-9]+\.[0-9]+\.[0-9]+"
                r"(?:\s+(?:>=|<=|>|<)[0-9]+\.[0-9]+\.[0-9]+)?$"
            )
        ),
    ]


class SkillDependency(ClosedModel):
    skill_id: SkillId
    version_range: Annotated[str, Field(min_length=2, max_length=80)]
    required_fact_types: Annotated[tuple[SemanticType, ...], Field(max_length=100)]


class Dependencies(ClosedModel):
    runtime_contracts: Annotated[
        tuple[RuntimeContract, ...], Field(min_length=1, max_length=3)
    ]
    skills: Annotated[tuple[SkillDependency, ...], Field(max_length=20)]


class Example(ClosedModel):
    question_ru: Annotated[str, Field(min_length=3, max_length=500)]
    applicability: Literal["applicable", "not_applicable"]
    reason_ru: Annotated[str, Field(min_length=3, max_length=500)]


class FixtureBinding(ClosedModel):
    parameter: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    value: JsonValue


class McpFixture(ClosedModel):
    kind: Literal["mcp_execute_query"]
    response: dict[str, JsonValue]


class DocumentationChunk(ClosedModel):
    chunk_id: Annotated[str, Field(min_length=3, max_length=160)]
    title: str
    heading: str
    text: str
    source_uri: Annotated[str, Field(pattern=r"^ut-help://")]
    role: str


class DocumentationFixture(ClosedModel):
    kind: Literal["documentation_chunks"]
    chunks: Annotated[tuple[DocumentationChunk, ...], Field(max_length=100)]


PortableFixture = Annotated[
    McpFixture | DocumentationFixture,
    Field(discriminator="kind"),
]


class ExpectedFixtureResult(ClosedModel):
    status: Literal[
        "success_with_rows",
        "success_empty",
        "zero_aggregate",
        "partial",
        "query_error",
        "documentation_found",
        "documentation_empty",
    ]
    required_fact_ids: Annotated[tuple[FactId, ...], Field(max_length=100)]
    error_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]+$")] | None = None


class SkillTestCase(ClosedModel):
    test_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")]
    case_kind: Literal["positive", "negative"]
    bindings: Annotated[tuple[FixtureBinding, ...], Field(max_length=40)]
    fixture: PortableFixture
    expected: ExpectedFixtureResult


class SourceConfiguration(ClosedModel):
    configuration_id: Annotated[str, Field(min_length=1, max_length=160)]
    release: FourPartVersion
    compatibility_mode: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    metadata_snapshot_sha256: Sha256


class SourceReference(ClosedModel):
    kind: Literal[
        "configuration_metadata",
        "configuration_source",
        "built_in_help",
        "mcp_contract",
        "test_evidence",
    ]
    uri: Annotated[
        str,
        Field(
            max_length=500,
            pattern=r"^(ut-config|ut-help|mcp-contract|test-evidence)://",
        ),
    ]
    sha256: Sha256 | None = None


class SkillProvenance(ClosedModel):
    author: Annotated[str, Field(min_length=2, max_length=160)]
    created_at: AwareDatetime
    reviewed_by: Annotated[str, Field(min_length=2, max_length=160)] | None = None
    reviewed_at: AwareDatetime | None = None
    source_configuration: SourceConfiguration
    source_references: Annotated[
        tuple[SourceReference, ...], Field(min_length=1, max_length=100)
    ]
    change_note_ru: Annotated[str, Field(min_length=3, max_length=1000)]


class Skill(ClosedModel):
    schema_version: Literal["1.0.0", "1.1.0"]
    document_type: Literal["skill"]
    skill_id: SkillId
    version: SemVer
    display: SkillDisplay
    provides: Provides
    compatibility: Compatibility
    selection: Selection
    parameters: Annotated[tuple[Parameter, ...], Field(max_length=40)]
    operation: SkillOperation
    output_contract: OutputContract
    result_constraints: Annotated[
        tuple[ResultConstraint, ...], Field(max_length=40)
    ] = ()
    dependencies: Dependencies
    examples: Annotated[tuple[Example, ...], Field(min_length=2, max_length=20)]
    tests: Annotated[tuple[SkillTestCase, ...], Field(min_length=2, max_length=50)]
    provenance: SkillProvenance
    integrity: Integrity

    @model_validator(mode="after")
    def versioned_context_contract(self) -> "Skill":
        parameters_have_field = tuple(
            "context_slot_keys" in parameter.model_fields_set
            for parameter in self.parameters
        )
        output = self.output_contract
        output_has_resolution = "resolution" in output.model_fields_set
        output_has_policy = "context_export_policy" in output.model_fields_set
        if self.schema_version == "1.0.0":
            if any(parameters_have_field) or output_has_resolution or output_has_policy:
                raise ValueError("skill 1.0.0 cannot declare slice-3 context fields")
            if any(
                fact.value_type is FactValueType.ENUM
                for fact in self.output_contract.facts
            ):
                raise ValueError("skill 1.0.0 cannot declare enum facts")
            return self

        if not all(parameters_have_field):
            raise ValueError("skill 1.1.0 requires parameters[*].context_slot_keys")
        if not output_has_resolution or not output_has_policy:
            raise ValueError(
                "skill 1.1.0 requires explicit resolution and context_export_policy"
            )
        if output.context_export_policy is None:
            raise ValueError("skill 1.1.0 context_export_policy cannot be null")
        for parameter in self.parameters:
            keys = parameter.context_slot_keys or ()
            allows_context = "session_context" in parameter.allowed_sources
            if allows_context != bool(keys):
                raise ValueError(
                    "session_context source and context_slot_keys must be declared together"
                )
        return self


CollectionScope = Literal["visible_page", "complete_set"]


def collection_scope_for_skill(skill: Skill) -> CollectionScope:
    operation = skill.operation
    if not isinstance(operation, DataQueryOperation):
        return "visible_page"
    if skill.output_contract.cardinality == "aggregate":
        return "complete_set"
    if (
        operation.pagination.strategy != "none"
        or skill.output_contract.sufficiency.truncation_policy == "page_is_complete"
    ):
        return "visible_page"
    return "complete_set"
