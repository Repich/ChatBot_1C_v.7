"""Identifiers and reusable value objects from the accepted schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatbot1c.domain.base import ClosedModel

SkillId = Annotated[
    str,
    Field(
        min_length=3,
        max_length=120,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$",
    ),
]
SemVer = Annotated[
    str,
    Field(
        pattern=(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
        )
    ),
]
FourPartVersion = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FactId = Annotated[
    str,
    Field(max_length=120, pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"),
]
SemanticType = Annotated[
    str,
    Field(max_length=160, pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"),
]
StepId = Annotated[str, Field(pattern=r"^s[1-9][0-9]{0,2}$")]
RequirementId = Annotated[str, Field(pattern=r"^r[1-9][0-9]{0,2}$")]
ContextHandle = Annotated[str, Field(pattern=r"^ctx_[A-Za-z0-9_-]{16,80}$")]


class Integrity(ClosedModel):
    algorithm: Literal["sha256"]
    canonicalization: Literal["RFC8785"]
    scope: Literal["document_without_integrity"]
    digest: Sha256


class Period(ClosedModel):
    start: AwareDatetime
    end_exclusive: AwareDatetime
    timezone: Literal["Europe/Moscow"]
    precision: Literal["day", "month", "quarter", "year", "instant"]

    @model_validator(mode="after")
    def end_follows_start(self) -> "Period":
        if self.end_exclusive <= self.start:
            raise ValueError("end_exclusive must be later than start")
        return self


class PaginationValue(ClosedModel):
    mode: Literal["first_page", "continue", "full"]
    limit: Annotated[int, Field(ge=1, le=1000)]
    cursor_handle: (
        Annotated[str, Field(pattern=r"^page_[A-Za-z0-9_-]{16,100}$")] | None
    ) = None


class EntityRef(ClosedModel):
    object_ref: Literal[True] = Field(alias="_objectRef")
    unique_id: UUID = Field(alias="УникальныйИдентификатор")
    object_type: Annotated[str, Field(min_length=3, max_length=240)] = Field(
        alias="ТипОбъекта"
    )
    presentation: Annotated[str, Field(max_length=1000)] = Field(alias="Представление")


def ensure_aware(value: datetime) -> datetime:
    """Retain an explicit helper for application-created timestamps."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset")
    return value
