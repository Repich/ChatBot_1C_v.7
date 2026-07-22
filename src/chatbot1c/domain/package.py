"""Closed typed model for ``schemas/skill-package.schema.json``."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.skill import Skill, SourceReference
from chatbot1c.domain.types import FourPartVersion, Integrity, SemVer, Sha256, SkillId


class PackageDisplay(ClosedModel):
    name_ru: Annotated[str, Field(min_length=3, max_length=160)]
    description_ru: Annotated[str, Field(min_length=10, max_length=1000)]


class PackageTarget(ClosedModel):
    configuration_id: Annotated[str, Field(min_length=1, max_length=160)]
    configuration_name: Annotated[str, Field(min_length=3, max_length=300)]
    release: FourPartVersion
    compatibility_mode: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]


class DependencyLockEntry(ClosedModel):
    skill_id: SkillId
    version: SemVer
    digest: Sha256


class PackageProvenance(ClosedModel):
    author: Annotated[str, Field(min_length=2, max_length=160)]
    created_at: AwareDatetime
    release_note_ru: Annotated[str, Field(min_length=3, max_length=2000)]
    source_references: Annotated[
        tuple[SourceReference, ...], Field(min_length=1, max_length=100)
    ]


class SkillPackage(ClosedModel):
    schema_version: Literal["1.0.0", "1.1.0"]
    document_type: Literal["skill_package"]
    package_id: Annotated[
        str,
        Field(
            min_length=3,
            max_length=120,
            pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$",
        ),
    ]
    version: SemVer
    display: PackageDisplay
    target: PackageTarget
    skills: Annotated[tuple[Skill, ...], Field(min_length=1, max_length=500)]
    dependency_lock: Annotated[tuple[DependencyLockEntry, ...], Field(max_length=1000)]
    provenance: PackageProvenance
    integrity: Integrity

    @model_validator(mode="after")
    def embedded_versions_match_package(self) -> "SkillPackage":
        if self.schema_version == "1.0.0" and any(
            skill.schema_version != "1.0.0" for skill in self.skills
        ):
            raise ValueError("skill package 1.0.0 accepts only skill 1.0.0")
        return self
