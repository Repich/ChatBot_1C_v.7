"""Closed DTOs and immutable records at application boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Literal, Mapping
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from chatbot1c.contracts.digest import canonicalize
from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.evidence import Fact
from chatbot1c.domain.skill import (
    DataQueryOperation,
    FactValueType,
    ParameterValueType,
    PrefixPagination,
    Skill,
    UnitFixed,
    UnitFromFact,
)


class SkillParameterConstraintsCard(ClosedModel):
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    max_items: int | None = None


class SkillParameterCard(ClosedModel):
    name: str
    title_ru: str
    description_ru: str
    value_type: ParameterValueType
    required: bool
    semantic_type: str | None = None
    entity_types: tuple[str, ...] | None = None
    allowed_sources: tuple[
        Literal["user_slot", "session_context", "previous_step", "system"], ...
    ]
    normalization: str
    constraints: SkillParameterConstraintsCard | None = None
    allowed_values: tuple[str, ...] | None = None
    default: JsonValue = None


class SkillUnitCard(ClosedModel):
    mode: Literal["not_applicable", "fixed", "from_fact"]
    code: str | None = None
    fact_id: str | None = None


class ProducedFactCard(ClosedModel):
    fact_id: str
    semantic_type: str
    value_type: FactValueType
    role: Literal["entity", "dimension", "measure", "attribute", "time", "provenance"]
    required: bool
    nullable: bool
    unit: SkillUnitCard
    time_semantics: Literal["none", "moment", "period"]
    row_identity: bool


class SkillOutputCard(ClosedModel):
    cardinality: Literal["exactly_one", "zero_or_one", "many", "aggregate"]
    facts: tuple[ProducedFactCard, ...]
    truncation_policy: Literal[
        "page_is_complete", "partial_until_all_pages", "error_if_truncated"
    ]


class SkillLimitsCard(ClosedModel):
    operation_kind: Literal["data_query", "documentation_retrieval"]
    default_rows: int | None = None
    maximum_rows: int | None = None
    pagination_strategy: Literal["none", "prefix", "keyset"] | None = None
    maximum_total: int | None = None
    top_k: int | None = None
    max_chunks_per_source: int | None = None


class SkillCompatibilityCard(ClosedModel):
    configuration_id: str
    release_minimum: str
    release_maximum: str
    include_minimum: bool
    include_maximum: bool
    compatibility_modes: tuple[str, ...]


class SkillDependencyCard(ClosedModel):
    skill_id: str
    version_range: str
    required_fact_types: tuple[str, ...]


class SkillCard(ClosedModel):
    skill_id: str
    version: str
    digest: str
    purpose_ru: str
    limitations_ru: tuple[str, ...]
    intent_kinds: tuple[Literal["data", "documentation", "mixed"], ...]
    capability_ids: tuple[str, ...]
    required_context_fact_types: tuple[str, ...]
    parameters: tuple[SkillParameterCard, ...]
    output: SkillOutputCard
    limits: SkillLimitsCard
    compatibility: SkillCompatibilityCard
    dependencies: tuple[SkillDependencyCard, ...]
    anti_examples_ru: Annotated[tuple[str, ...], Field(max_length=2)]
    applicability_examples_ru: Annotated[tuple[str, ...], Field(max_length=2)]


class EntityFactOrigin(ClosedModel):
    fact: Fact
    skill_id: str
    skill_version: str
    skill_digest: str
    column: str
    accepted_mcp_types: Annotated[tuple[str, ...], Field(min_length=1)]


class ContextFact(ClosedModel):
    handle: Annotated[str, Field(pattern=r"^ctx_[A-Za-z0-9_-]{8,100}$")]
    semantic_type: str
    value: JsonValue
    presentation: Annotated[str, Field(min_length=1, max_length=500)]
    origin_turn_id: UUID
    origin_fact_instance_id: UUID
    origin: EntityFactOrigin

    @model_validator(mode="after")
    def exact_origin(self) -> "ContextFact":
        from chatbot1c.domain.types import EntityRef

        fact = self.origin.fact
        if self.origin_fact_instance_id != fact.fact_instance_id:
            raise ValueError("context origin_fact_instance_id mismatch")
        if self.semantic_type != fact.semantic_type:
            raise ValueError("context semantic type differs from origin fact")
        if (
            fact.confirmation != "confirmed"
            or fact.value_type is not FactValueType.ENTITY_REF
            or not isinstance(fact.value, EntityRef)
        ):
            raise ValueError("context exports accept entity facts only")
        expected = fact.value.model_dump(mode="json", by_alias=True)
        if self.value != expected or self.presentation != fact.value.presentation:
            raise ValueError("context value differs from origin fact")
        if (
            fact.source_locator.kind != "query_column_binding"
            or fact.source_locator.reference != self.origin.column
        ):
            raise ValueError("context origin column mismatch")
        if fact.value.object_type not in self.origin.accepted_mcp_types:
            raise ValueError("context physical type is not proven by producer")
        return self


class PlannerRequest(ClosedModel):
    request_id: UUID
    session_id: UUID
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    turn_time: datetime
    context_version: Annotated[int, Field(ge=1)]
    catalog_snapshot_id: UUID
    catalog_revision: Annotated[int, Field(ge=1)]
    confirmed_facts: tuple[ContextFact, ...]
    recent_user_messages: Annotated[tuple[str, ...], Field(max_length=8)]
    skill_cards: Annotated[tuple[SkillCard, ...], Field(max_length=16)]


class ExecuteQueryRequest(ClosedModel):
    query: Annotated[str, Field(min_length=10, max_length=50000)]
    params: dict[str, JsonValue]
    limit: Annotated[int, Field(ge=1, le=1000)]
    include_schema: Literal[True] = True


class McpColumn(ClosedModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    types: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...]


class McpSchema(ClosedModel):
    columns: tuple[McpColumn, ...]


class ExecuteQueryEnvelope(ClosedModel):
    success: bool
    data: tuple[dict[str, JsonValue], ...]
    schema_: McpSchema = Field(alias="schema", serialization_alias="schema")
    count: Annotated[int, Field(ge=0)]
    truncated: bool = False
    has_more: bool = False
    error: str | None = None


class GetMetadataRequest(ClosedModel):
    mode: Literal["summary", "list", "detail"]
    filter: str | None = None
    meta_type: str | None = None
    name_mask: str | None = None
    attribute_mask: str | None = None
    sections: tuple[str, ...] = ()
    limit: Annotated[int, Field(ge=1, le=1000)] = 100
    offset: Annotated[int, Field(ge=0)] = 0
    extension_name: str | None = None


class MetadataEnvelope(ClosedModel):
    success: bool
    data: JsonValue
    error: str | None = None


class HelpSearchRequest(ClosedModel):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    release: Literal["11.5.27.56"] = "11.5.27.56"
    source_kind: Literal["built_in_help"] = "built_in_help"
    metadata_kinds: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    top_k: Annotated[int, Field(ge=1, le=20)] = 8
    max_chunks_per_source: Annotated[int, Field(ge=1, le=10)] = 3


class HelpChunk(ClosedModel):
    chunk_id: str
    title: str
    heading: str
    text: str
    role: str
    source_uri: Annotated[str, Field(pattern=r"^ut-help://11\.5\.27\.56/")]
    relative_path: str
    metadata_kind: str
    metadata_object: str
    anchor: str
    chunk_sha256: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class PinnedCatalog:
    snapshot_id: UUID
    revision: int
    skills: Mapping[str, Skill]

    @classmethod
    def create(
        cls, snapshot_id: UUID, revision: int, skills: Mapping[str, Skill]
    ) -> "PinnedCatalog":
        return cls(snapshot_id, revision, MappingProxyType(dict(skills)))

    @property
    def digest(self) -> str:
        import hashlib

        entries = [
            {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "digest": skill.integrity.digest,
            }
            for skill in sorted(self.skills.values(), key=lambda item: item.skill_id)
        ]
        return hashlib.sha256(canonicalize(entries)).hexdigest()

    def cards(self, *, limit: int = 16) -> tuple[SkillCard, ...]:
        cards: list[SkillCard] = []
        for skill in sorted(self.skills.values(), key=lambda item: item.skill_id)[:limit]:
            cards.append(
                SkillCard(
                    skill_id=skill.skill_id,
                    version=skill.version,
                    digest=skill.integrity.digest,
                    purpose_ru=skill.display.purpose_ru,
                    limitations_ru=skill.display.limitations_ru,
                    intent_kinds=skill.selection.intent_kinds,
                    capability_ids=skill.provides.capability_ids,
                    required_context_fact_types=(
                        skill.selection.required_context_fact_types
                    ),
                    parameters=tuple(
                        _parameter_card(parameter) for parameter in skill.parameters
                    ),
                    output=_output_card(skill),
                    limits=_limits_card(skill),
                    compatibility=SkillCompatibilityCard(
                        configuration_id=skill.compatibility.configuration_id,
                        release_minimum=skill.compatibility.release_range.minimum,
                        release_maximum=skill.compatibility.release_range.maximum,
                        include_minimum=skill.compatibility.release_range.include_minimum,
                        include_maximum=skill.compatibility.release_range.include_maximum,
                        compatibility_modes=skill.compatibility.compatibility_modes,
                    ),
                    dependencies=tuple(
                        SkillDependencyCard(
                            skill_id=dependency.skill_id,
                            version_range=dependency.version_range,
                            required_fact_types=dependency.required_fact_types,
                        )
                        for dependency in skill.dependencies.skills
                    ),
                    anti_examples_ru=skill.selection.anti_examples_ru[:2],
                    applicability_examples_ru=tuple(
                        example.question_ru
                        for example in skill.examples
                        if example.applicability == "applicable"
                    )[:2],
                )
            )
        return tuple(cards)


def _parameter_card(parameter: object) -> SkillParameterCard:
    from chatbot1c.domain.skill import Parameter

    if not isinstance(parameter, Parameter):
        raise TypeError("expected Parameter")
    constraints = (
        None
        if parameter.constraints is None
        else SkillParameterConstraintsCard.model_validate(
            parameter.constraints.model_dump(mode="json")
        )
    )
    return SkillParameterCard(
        name=parameter.name,
        title_ru=parameter.title_ru,
        description_ru=parameter.description_ru,
        value_type=parameter.value_type,
        required=parameter.required,
        semantic_type=parameter.semantic_type,
        entity_types=parameter.entity_types,
        allowed_sources=parameter.allowed_sources,
        normalization=parameter.normalization,
        constraints=constraints,
        allowed_values=parameter.allowed_values,
        default=parameter.default,
    )


def _output_card(skill: Skill) -> SkillOutputCard:
    identities = set(skill.output_contract.row_identity_fact_ids or ())
    facts = tuple(
        ProducedFactCard(
            fact_id=fact.fact_id,
            semantic_type=fact.semantic_type,
            value_type=fact.value_type,
            role=fact.role,
            required=fact.required,
            nullable=fact.nullable,
            unit=_unit_card(fact.unit_contract),
            time_semantics=(
                "period"
                if fact.value_type is FactValueType.PERIOD
                else "moment"
                if fact.role == "time"
                else "none"
            ),
            row_identity=fact.fact_id in identities,
        )
        for fact in skill.output_contract.facts
    )
    return SkillOutputCard(
        cardinality=skill.output_contract.cardinality,
        facts=facts,
        truncation_policy=skill.output_contract.sufficiency.truncation_policy,
    )


def _unit_card(unit: object) -> SkillUnitCard:
    if isinstance(unit, UnitFixed):
        return SkillUnitCard(mode="fixed", code=unit.code)
    if isinstance(unit, UnitFromFact):
        return SkillUnitCard(mode="from_fact", fact_id=unit.fact_id)
    return SkillUnitCard(mode="not_applicable")


def _limits_card(skill: Skill) -> SkillLimitsCard:
    operation = skill.operation
    if isinstance(operation, DataQueryOperation):
        pagination = operation.pagination
        return SkillLimitsCard(
            operation_kind="data_query",
            default_rows=operation.query_template.mcp_limit.default,
            maximum_rows=operation.query_template.mcp_limit.maximum,
            pagination_strategy=pagination.strategy,
            maximum_total=(
                pagination.maximum_total
                if isinstance(pagination, PrefixPagination)
                else None
            ),
        )
    return SkillLimitsCard(
        operation_kind="documentation_retrieval",
        top_k=operation.retrieval.top_k,
        max_chunks_per_source=operation.retrieval.max_chunks_per_source,
    )


@dataclass(frozen=True, slots=True)
class ConfigurationProfile:
    configuration_id: str
    configuration_name: str
    release: str
    compatibility_mode: str
    metadata: Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    context_version: int


@dataclass(frozen=True, slots=True)
class TurnRecord:
    turn_id: UUID
    request_id: UUID
    trace_id: UUID
    session_id: UUID
    client_message_id: str
    user_text: str
    assistant_text: str | None
    status: str
    outcome: str | None
    created_at: datetime
    completed_at: datetime | None
    context_version: int
    catalog_snapshot_id: UUID | None
    catalog_revision: int | None
    plan_json: str | None
    evidence_json: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class TurnEvent:
    turn_id: UUID
    sequence: int
    event_name: str
    timestamp: datetime
    status: str
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ImportResult:
    revision: int
    snapshot_id: UUID
    skills: tuple[tuple[str, str, str], ...]
