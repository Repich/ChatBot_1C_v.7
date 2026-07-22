"""Closed typed model for ``schemas/evidence.schema.json``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.skill import FactValueType
from chatbot1c.domain.types import (
    ContextHandle,
    EntityRef,
    FactId,
    Period,
    RequirementId,
    SemanticType,
    Sha256,
)


class CatalogSkill(ClosedModel):
    skill_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")]
    version: Annotated[
        str,
        Field(
            pattern=(
                r"^[0-9]+\.[0-9]+\.[0-9]+"
                r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
            )
        ),
    ]
    digest: Sha256


class CatalogSnapshot(ClosedModel):
    snapshot_id: UUID
    revision: Annotated[int, Field(ge=1)]
    skills: Annotated[tuple[CatalogSkill, ...], Field(max_length=500)]


class DatabaseStateMarker(ClosedModel):
    marker_id: UUID
    algorithm: Literal["sha256"]
    scope: Literal["acceptance_observable_state"]
    digest: Sha256
    captured_at: AwareDatetime
    profile_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    acceptance_suite_version: Annotated[str, Field(pattern=r"^q001-q116-v[1-9][0-9]*$")]
    configuration_revision: Literal["11.5.27.56"]
    configuration_profile_digest: Sha256
    catalog_revision: Annotated[int, Field(ge=1)]
    catalog_snapshot_digest: Sha256
    documentation_revision: Annotated[str, Field(min_length=1, max_length=160)]
    documentation_manifest_digest: Sha256
    projection_manifest_digest: Sha256


class StepEvidence(ClosedModel):
    step_id: Annotated[str, Field(pattern=r"^s[1-9][0-9]{0,2}$")]
    source_kind: Literal[
        "mcp_data",
        "documentation_index",
        "deterministic_operator",
        "system",
        "deepseek",
    ]
    operation_ref: Annotated[str, Field(min_length=1, max_length=240)]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    attempts: Annotated[int, Field(ge=0, le=3)]
    status: Outcome
    row_count: Annotated[int, Field(ge=0)]
    truncated: bool
    has_more: bool
    produced_fact_instance_ids: Annotated[tuple[UUID, ...], Field(max_length=100000)]
    error_ids: Annotated[tuple[UUID, ...], Field(max_length=100)]
    # The enclosing bundle accepts this implicit value only for legacy 1.0 reads.
    collection_scope: Literal["visible_page", "complete_set"] = "complete_set"


class SourceLocator(ClosedModel):
    kind: Literal[
        "query_column_binding", "documentation_chunk", "operator_result", "system_value"
    ]
    reference: Annotated[str, Field(min_length=1, max_length=500)]


class UnitNotApplicable(ClosedModel):
    mode: Literal["not_applicable"]


class UnitResolved(ClosedModel):
    mode: Literal["resolved"]
    code: Annotated[str, Field(min_length=1, max_length=40)]
    label_ru: Annotated[str, Field(min_length=1, max_length=80)]


class UnitUnresolved(ClosedModel):
    mode: Literal["unresolved"]
    reason_ru: Annotated[str, Field(min_length=3, max_length=300)]


FactUnit = Annotated[
    UnitNotApplicable | UnitResolved | UnitUnresolved,
    Field(discriminator="mode"),
]


class DocumentFragment(ClosedModel):
    chunk_id: Annotated[str, Field(min_length=3, max_length=160)]
    role: Literal[
        "definition",
        "procedure",
        "prerequisite",
        "restriction",
        "error_cause",
        "verification_action",
        "status_meaning",
        "navigation",
    ]
    text: Annotated[str, Field(min_length=1, max_length=100000)]


class CitationValue(ClosedModel):
    citation_id: UUID


FactValue = (
    str | int | float | bool | Period | EntityRef | DocumentFragment | CitationValue
)


class Fact(ClosedModel):
    fact_instance_id: UUID
    row_id: Annotated[str, Field(pattern=r"^row_[A-Za-z0-9_-]{8,100}$")]
    fact_id: FactId
    semantic_type: SemanticType
    value_type: FactValueType
    value: FactValue
    confirmation: Literal["confirmed"]
    step_id: Annotated[str, Field(pattern=r"^s[1-9][0-9]{0,2}$")]
    source_locator: SourceLocator
    unit: FactUnit
    moment: AwareDatetime | None = None
    period: Period | None = None


class Citation(ClosedModel):
    citation_id: UUID
    source_kind: Literal["built_in_help"]
    corpus_id: Literal["ut_11_5_27_56_built_in_help"]
    release: Literal["11.5.27.56"]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source_uri: Annotated[
        str, Field(max_length=1000, pattern=r"^ut-help://11\.5\.27\.56/")
    ]
    relative_path: Annotated[
        str,
        Field(
            max_length=800,
            pattern=r"^(?![A-Za-z]:)(?!/)(?!.*\.\.).+$",
        ),
    ]
    anchor: Annotated[str, Field(max_length=300)]
    chunk_sha256: Sha256


class DisagreementPosition(ClosedModel):
    position_id: Annotated[str, Field(pattern=r"^p[1-9][0-9]?$")]
    fact_instance_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=1000)]
    citation_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]


class DocumentationDisagreement(ClosedModel):
    disagreement_id: UUID
    subject_fact_id: FactId
    kind: Literal[
        "contradiction", "scope_difference", "terminology_variation", "unresolved"
    ]
    status: Literal["unresolved"]
    presentation_policy: Literal["surface_all_grounded_positions"]
    positions: Annotated[
        tuple[DisagreementPosition, ...], Field(min_length=2, max_length=10)
    ]


class CoverageRequirement(ClosedModel):
    requirement_id: RequirementId
    semantic_type: SemanticType
    status: CoverageStatus
    fact_instance_ids: Annotated[tuple[UUID, ...], Field(max_length=100000)]
    # The enclosing bundle accepts this implicit value only for legacy 1.0 reads.
    required: bool = True


class Coverage(ClosedModel):
    sufficient: bool
    requirements: Annotated[tuple[CoverageRequirement, ...], Field(max_length=40)]


class Pagination(ClosedModel):
    shown: Annotated[int, Field(ge=0)]
    page_size: Annotated[int, Field(ge=1, le=1000)]
    has_more: bool
    continuation_handle: (
        Annotated[str, Field(pattern=r"^page_[A-Za-z0-9_-]{32,100}$")] | None
    ) = None


class ContextExport(ClosedModel):
    context_handle: ContextHandle
    fact_instance_id: UUID
    semantic_type: SemanticType


class EntityIdentity(ClosedModel):
    semantic_type: SemanticType
    physical_type: Annotated[str, Field(min_length=3, max_length=240)]
    unique_id: UUID


class ResolverUseProof(ClosedModel):
    step_id: Annotated[str, Field(pattern=r"^s[1-9][0-9]{0,2}$")]
    skill_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")]
    mode: Literal["select_one", "select_set", "display_only"]
    identity_fact_id: FactId
    slot_key: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    ]
    consumer_parameters: Annotated[tuple[str, ...], Field(max_length=50)]


class SelectionProof(ClosedModel):
    resolver: ResolverUseProof
    state: Literal["selected_one", "selected_set"]
    fact_instance_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]
    identities: Annotated[
        tuple[EntityIdentity, ...], Field(min_length=1, max_length=100)
    ]
    complete: Literal[True]
    proof_digest: Sha256


class FilterRetentionProof(ClosedModel):
    step_id: Annotated[str, Field(pattern=r"^s[1-9][0-9]{0,2}$")]
    fact_instance_id: UUID
    fact_id: FactId
    slot_key: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    ]
    semantic_type: SemanticType
    value_type: Literal["datetime", "period", "enum"]
    canonical_value_digest: Sha256
    proof_digest: Sha256


class EvidenceError(ClosedModel):
    error_id: UUID
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]+$")]
    stage: Literal[
        "request",
        "planning",
        "coverage",
        "execution",
        "evidence_validation",
        "answering",
    ]
    dependency: Literal[
        "none", "deepseek", "mcp", "documentation_index", "skill_catalog", "database"
    ]
    retryable: bool
    public_message_ru: Annotated[str, Field(min_length=3, max_length=1000)]
    diagnostic_ref: Annotated[str, Field(pattern=r"^diag_[A-Za-z0-9_-]{8,100}$")]


class EvidenceBundle(ClosedModel):
    schema_version: Literal["1.0.0", "1.1.0"]
    document_type: Literal["evidence_bundle"]
    trace_id: UUID
    request_id: UUID
    session_id: UUID
    created_at: AwareDatetime
    source_boundary: Literal["data", "documentation", "mixed", "none"]
    outcome: Outcome
    empty_reason: Literal["not_found", "no_rows"] | None = None
    catalog_snapshot: CatalogSnapshot
    database_state_marker: DatabaseStateMarker
    steps: Annotated[tuple[StepEvidence, ...], Field(max_length=50)]
    facts: Annotated[tuple[Fact, ...], Field(max_length=100000)]
    citations: Annotated[tuple[Citation, ...], Field(max_length=500)]
    documentation_disagreements: Annotated[
        tuple[DocumentationDisagreement, ...], Field(max_length=100)
    ]
    coverage: Coverage
    pagination: Pagination | None = None
    context_exports: Annotated[tuple[ContextExport, ...], Field(max_length=100)]
    errors: Annotated[tuple[EvidenceError, ...], Field(max_length=100)]

    @model_validator(mode="before")
    @classmethod
    def require_explicit_v11_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if value.get("schema_version") == "1.0.0":
            facts = value.get("facts")
            if isinstance(facts, (list, tuple)) and any(
                isinstance(fact, Mapping) and fact.get("value_type") == "enum"
                for fact in facts
            ):
                raise ValueError("evidence 1.0.0 cannot contain enum facts")
            return value
        if value.get("schema_version") != "1.1.0":
            return value
        steps = value.get("steps")
        if isinstance(steps, (list, tuple)) and any(
            not _field_was_supplied(step, "collection_scope") for step in steps
        ):
            raise ValueError("evidence 1.1.0 requires steps[*].collection_scope")
        coverage = value.get("coverage")
        requirements = (
            coverage.get("requirements") if isinstance(coverage, Mapping) else None
        )
        if isinstance(requirements, (list, tuple)) and any(
            not _field_was_supplied(requirement, "required")
            for requirement in requirements
        ):
            raise ValueError(
                "evidence 1.1.0 requires coverage.requirements[*].required"
            )
        return value


def _field_was_supplied(value: object, field: str) -> bool:
    if isinstance(value, Mapping):
        return field in value
    if isinstance(value, ClosedModel):
        return field in value.model_fields_set
    return False
