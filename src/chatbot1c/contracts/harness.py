"""Production validation pipeline for every portable contract document."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from pydantic import ValidationError

from chatbot1c.contracts.digest import canonicalize, verify_digest
from chatbot1c.contracts.errors import (
    ContractIssue,
    ContractValidationError,
    raise_for_issues,
)
from chatbot1c.contracts.json_limits import (
    MAX_EMBEDDED_SKILL_BYTES,
    loads_bounded_json,
    validate_json_structure,
)
from chatbot1c.contracts.schema import SchemaRepository, json_pointer
from chatbot1c.contracts.semantic import SemanticValidator
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill

ContractDocument: TypeAlias = Skill | SkillPackage | PlannerOutput | EvidenceBundle

SCHEMA_BY_DOCUMENT_TYPE: dict[str, str] = {
    "skill": "skill.schema.json",
    "skill_package": "skill-package.schema.json",
    "planner_output": "planner-output.schema.json",
    "evidence_bundle": "evidence.schema.json",
}
MODEL_BY_DOCUMENT_TYPE: dict[
    str,
    type[Skill] | type[SkillPackage] | type[PlannerOutput] | type[EvidenceBundle],
] = {
    "skill": Skill,
    "skill_package": SkillPackage,
    "planner_output": PlannerOutput,
    "evidence_bundle": EvidenceBundle,
}


@dataclass(frozen=True, slots=True)
class ContractHarness:
    schemas: SchemaRepository
    semantics: SemanticValidator

    @classmethod
    def discover(cls, project_root: Path | str | None = None) -> "ContractHarness":
        return cls(
            schemas=SchemaRepository.discover(project_root),
            semantics=SemanticValidator(),
        )

    def validate_document(
        self,
        document: dict[str, Any],
        *,
        available_skills: Sequence[Skill] = (),
        verify_integrity: bool = True,
    ) -> ContractDocument:
        validate_json_structure(document)
        document_type = document.get("document_type")
        if (
            not isinstance(document_type, str)
            or document_type not in SCHEMA_BY_DOCUMENT_TYPE
        ):
            raise ContractValidationError(
                (
                    ContractIssue(
                        code="DOCUMENT_TYPE_UNKNOWN",
                        json_pointer="/document_type",
                        message_ru="Неизвестный document_type контрактного документа.",
                    ),
                )
            )

        if document_type == "skill_package":
            self._validate_embedded_skill_sizes(document)
        self.schemas.validate(document, SCHEMA_BY_DOCUMENT_TYPE[document_type])
        model_type = MODEL_BY_DOCUMENT_TYPE[document_type]
        try:
            model = model_type.model_validate(document)
        except ValidationError as error:
            raise ContractValidationError(_pydantic_issues(error)) from error

        if verify_integrity:
            self._verify_integrity(document, document_type)
        self.semantics.validate(model, available_skills=available_skills)
        return model

    def validate_path(
        self,
        path: Path | str,
        *,
        available_skills: Sequence[Skill] = (),
        verify_integrity: bool = True,
    ) -> ContractDocument:
        document = self.schemas.load_json(path)
        return self.validate_document(
            document,
            available_skills=available_skills,
            verify_integrity=verify_integrity,
        )

    def validate_json_bytes(
        self,
        payload: bytes,
        *,
        available_skills: Sequence[Skill] = (),
        verify_integrity: bool = True,
    ) -> ContractDocument:
        document = loads_bounded_json(payload)
        return self.validate_document(
            document,
            available_skills=available_skills,
            verify_integrity=verify_integrity,
        )

    @staticmethod
    def _validate_embedded_skill_sizes(document: dict[str, Any]) -> None:
        skills = document.get("skills")
        if not isinstance(skills, list):
            return
        for index, skill in enumerate(skills):
            if not isinstance(skill, dict):
                continue
            canonical_size = len(canonicalize(skill))
            if canonical_size > MAX_EMBEDDED_SKILL_BYTES:
                raise ContractValidationError(
                    (
                        ContractIssue(
                            code="JSON_BYTES_LIMIT",
                            json_pointer=f"/skills/{index}",
                            message_ru=(
                                "Canonical embedded skill превышает предел "
                                f"{MAX_EMBEDDED_SKILL_BYTES} bytes."
                            ),
                        ),
                    )
                )

    @staticmethod
    def _verify_integrity(document: dict[str, Any], document_type: str) -> None:
        issues: list[ContractIssue] = []
        if document_type == "skill_package":
            skills = document.get("skills", [])
            if isinstance(skills, list):
                for index, skill in enumerate(skills):
                    if not isinstance(skill, dict):
                        continue
                    try:
                        verify_digest(skill, pointer_prefix=f"/skills/{index}")
                    except ContractValidationError as error:
                        issues.extend(error.issues)
        if document_type in {"skill", "skill_package"}:
            try:
                verify_digest(document)
            except ContractValidationError as error:
                issues.extend(error.issues)
        raise_for_issues(issues)


def _pydantic_issues(error: ValidationError) -> tuple[ContractIssue, ...]:
    issues: list[ContractIssue] = []
    for item in error.errors(include_url=False):
        location = [part for part in item["loc"] if isinstance(part, (str, int))]
        issues.append(
            ContractIssue(
                code="DOMAIN_MODEL_VALIDATION_ERROR",
                json_pointer=json_pointer(location),
                message_ru=f"Typed domain model отклонила значение: {item['msg']}.",
                keyword=str(item["type"]),
            )
        )
    return tuple(issues)
