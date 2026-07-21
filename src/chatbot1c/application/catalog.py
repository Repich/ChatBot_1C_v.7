"""Atomic skill catalog import, replacement, deletion and export."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable, Mapping
from typing import Literal

from semantic_version import NpmSpec, SimpleSpec, Version

from chatbot1c.application.errors import ApplicationError, CatalogConflictError
from chatbot1c.application.models import (
    ConfigurationProfile,
    ImportResult,
    PinnedCatalog,
)
from chatbot1c.application.ports import CatalogRepository
from chatbot1c.contracts.digest import canonicalize, generate_integrity
from chatbot1c.contracts.errors import (
    ContractIssue,
    ContractValidationError,
    raise_for_issues,
)
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.skill import Skill

ImportDocument = Skill | SkillPackage


class CatalogManager:
    """Provides strong immutable snapshot references to each turn."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository
        self._lock = threading.RLock()
        self._snapshot = repository.load_catalog()

    def pin(self) -> PinnedCatalog:
        persisted = self._repository.load_catalog()
        with self._lock:
            if persisted.revision > self._snapshot.revision:
                self._snapshot = persisted
            return self._snapshot

    def activate(self, snapshot: PinnedCatalog) -> None:
        with self._lock:
            if snapshot.revision < self._snapshot.revision:
                raise CatalogConflictError(
                    "Нельзя активировать устаревший snapshot каталога."
                )
            self._snapshot = snapshot


class CatalogService:
    def __init__(
        self,
        harness: ContractHarness,
        repository: CatalogRepository,
        manager: CatalogManager,
        profile: ConfigurationProfile,
    ) -> None:
        self._harness = harness
        self._repository = repository
        self._manager = manager
        self._profile = profile

    def validate_package(self, payload: bytes) -> SkillPackage:
        document = self.validate_import(payload)
        if not isinstance(document, SkillPackage):
            raise ApplicationError(
                "SKILL_PACKAGE_REQUIRED",
                "Операция принимает JSON skill package.",
                422,
            )
        return document

    def validate_import(self, payload: bytes) -> ImportDocument:
        """Validate either portable document accepted by web and CLI import."""

        current = self._manager.pin()
        document = self._harness.validate_json_bytes(
            payload, available_skills=tuple(current.skills.values())
        )
        if not isinstance(document, (Skill, SkillPackage)):
            raise ApplicationError(
                "SKILL_IMPORT_DOCUMENT_REQUIRED",
                "Операция принимает portable skill или skill package.",
                422,
            )
        if isinstance(document, SkillPackage):
            self._validate_package_target(document)
            skills = document.skills
            prefix = "/skills"
        else:
            skills = (document,)
            prefix = ""
        self._validate_skill_profiles(skills, prefix=prefix)
        for index, skill in enumerate(skills):
            known = self._repository.known_digest(skill.skill_id, skill.version)
            if known is not None and known != skill.integrity.digest:
                pointer = (
                    f"/skills/{index}/integrity/digest"
                    if isinstance(document, SkillPackage)
                    else "/integrity/digest"
                )
                raise ContractValidationError(
                    (
                        ContractIssue(
                            code="SKILL_DIGEST_CONFLICT",
                            json_pointer=pointer,
                            message_ru=(
                                "Та же пара skill_id/version уже известна с другим "
                                "canonical digest."
                            ),
                        ),
                    )
                )
        return document

    def import_package(
        self,
        payload: bytes,
        *,
        mode: Literal["create", "replace"] = "create",
        if_match: str | None = None,
    ) -> ImportResult:
        document = self.validate_import(payload)
        incoming = document.skills if isinstance(document, SkillPackage) else (document,)
        pinned = self._manager.pin()
        incoming_ids = {skill.skill_id for skill in incoming}
        active_ids = set(pinned.skills)
        if mode == "create":
            conflicts = incoming_ids & active_ids
            if conflicts:
                raise CatalogConflictError(
                    "Create не заменяет active skills: " + ", ".join(sorted(conflicts))
                )
        else:
            missing = incoming_ids - active_ids
            if missing:
                raise CatalogConflictError(
                    "Replace требует active skill: " + ", ".join(sorted(missing))
                )
            self._check_replace_match(incoming, pinned, if_match)

        resulting = dict(pinned.skills)
        resulting.update({skill.skill_id: skill for skill in incoming})
        self._validate_dependency_closure(resulting)
        package_json = canonicalize(
            document.model_dump(mode="json", by_alias=True, exclude_none=True)
        ).decode("utf-8")
        committed = self._repository.commit_catalog(
            expected_revision=pinned.revision,
            skills=resulting,
            package_json=package_json,
        )
        self._manager.activate(committed)
        return ImportResult(
            revision=committed.revision,
            snapshot_id=committed.snapshot_id,
            skills=tuple(
                (skill.skill_id, skill.version, skill.integrity.digest)
                for skill in incoming
            ),
        )

    def delete_skill(self, skill_id: str, *, if_match: str | None) -> PinnedCatalog:
        pinned = self._manager.pin()
        skill = pinned.skills.get(skill_id)
        if skill is None:
            raise ApplicationError("SKILL_NOT_FOUND", "Навык не найден.", 404)
        if if_match is None or if_match != skill.integrity.digest:
            raise CatalogConflictError(
                "Delete требует точный If-Match digest активного навыка."
            )
        dependents = sorted(
            candidate.skill_id
            for candidate in pinned.skills.values()
            if any(dep.skill_id == skill_id for dep in candidate.dependencies.skills)
        )
        if dependents:
            raise CatalogConflictError(
                "Навык используется зависимостями: " + ", ".join(dependents)
            )
        resulting = dict(pinned.skills)
        del resulting[skill_id]
        committed = self._repository.commit_catalog(
            expected_revision=pinned.revision,
            skills=resulting,
            package_json=None,
        )
        self._manager.activate(committed)
        return committed

    def export_skill(
        self,
        skill_id: str,
        *,
        closure: Literal["bare", "embedded"] = "bare",
    ) -> bytes:
        skill = self._manager.pin().skills.get(skill_id)
        if skill is None:
            raise ApplicationError("SKILL_NOT_FOUND", "Навык не найден.", 404)
        if closure == "embedded":
            return self.export_package((skill_id,))
        return canonicalize(_wire_skill(skill))

    def export_package(self, skill_ids: Iterable[str] | None = None) -> bytes:
        pinned = self._manager.pin()
        selected = self._dependency_closure(pinned.skills, skill_ids)
        if not selected:
            raise ApplicationError(
                "CATALOG_EMPTY", "В каталоге нет навыков для экспорта.", 409
            )
        skills = sorted(selected.values(), key=lambda item: item.skill_id)
        created_at = max(skill.provenance.created_at for skill in skills)
        closure_identity = [
            {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "digest": skill.integrity.digest,
            }
            for skill in skills
        ]
        closure_digest = hashlib.sha256(canonicalize(closure_identity)).hexdigest()
        document: dict[str, object] = {
            "schema_version": "1.0.0",
            "document_type": "skill_package",
            "package_id": f"ut.export.closure-{closure_digest[:24]}",
            "version": "1.0.0",
            "display": {
                "name_ru": "Экспорт активного каталога УТ",
                "description_ru": (
                    "Переносимый замкнутый snapshot выбранных активных навыков."
                ),
            },
            "target": {
                "configuration_id": self._profile.configuration_id,
                "configuration_name": self._profile.configuration_name,
                "release": self._profile.release,
                "compatibility_mode": self._profile.compatibility_mode,
            },
            "skills": [
                _wire_skill(skill) for skill in skills
            ],
            "dependency_lock": [
                {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "digest": skill.integrity.digest,
                }
                for skill in skills
            ],
            "provenance": {
                "author": "ChatBot 1C catalog exporter",
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
                "release_note_ru": "Детерминированный экспорт замкнутого каталога.",
                "source_references": [
                    {
                        "kind": "configuration_metadata",
                        "uri": (
                            f"ut-config://{self._profile.release}/catalog-snapshot"
                        ),
                    }
                ],
            },
        }
        signed = generate_integrity(document)
        validated = self._harness.validate_document(signed)
        if not isinstance(validated, SkillPackage):
            raise AssertionError("generated catalog export is not a package")
        return canonicalize(
            validated.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    def _validate_package_target(self, package: SkillPackage) -> None:
        issues: list[ContractIssue] = []
        target = package.target
        expected = self._profile
        comparisons = (
            ("configuration_id", target.configuration_id, expected.configuration_id),
            ("configuration_name", target.configuration_name, expected.configuration_name),
            ("release", target.release, expected.release),
            (
                "compatibility_mode",
                target.compatibility_mode,
                expected.compatibility_mode,
            ),
        )
        for field, actual, required in comparisons:
            if actual != required:
                issues.append(
                    ContractIssue(
                        code="PACKAGE_TARGET_INCOMPATIBLE",
                        json_pointer=f"/target/{field}",
                        message_ru=f"Target должен точно совпадать с профилем: {required}.",
                    )
                )
        raise_for_issues(issues)

    def _validate_skill_profiles(
        self, skills: tuple[Skill, ...], *, prefix: str
    ) -> None:
        issues: list[ContractIssue] = []
        expected = self._profile
        release = tuple(int(part) for part in expected.release.split("."))
        for skill_index, skill in enumerate(skills):
            base = f"{prefix}/{skill_index}" if prefix else ""
            compatibility = skill.compatibility
            if (
                compatibility.configuration_id != expected.configuration_id
                or compatibility.configuration_name != expected.configuration_name
            ):
                issues.append(
                    ContractIssue(
                        code="SKILL_CONFIGURATION_INCOMPATIBLE",
                        json_pointer=f"{base}/compatibility/configuration_id",
                        message_ru="Skill предназначен для другого типа конфигурации.",
                    )
                )
            minimum = tuple(
                int(part) for part in compatibility.release_range.minimum.split(".")
            )
            maximum = tuple(
                int(part) for part in compatibility.release_range.maximum.split(".")
            )
            release_matches = minimum <= release <= maximum
            if release == minimum and not compatibility.release_range.include_minimum:
                release_matches = False
            if release == maximum and not compatibility.release_range.include_maximum:
                release_matches = False
            if (
                not release_matches
                or expected.compatibility_mode
                not in compatibility.compatibility_modes
            ):
                issues.append(
                    ContractIssue(
                        code="SKILL_RELEASE_INCOMPATIBLE",
                        json_pointer=f"{base}/compatibility/release_range",
                        message_ru=(
                            "Release или compatibility mode базы не входит в "
                            "декларативный диапазон skill."
                        ),
                    )
                )
            for metadata_index, requirement in enumerate(
                skill.compatibility.required_metadata
            ):
                available = expected.metadata.get(requirement.object_name)
                pointer = (
                    f"{base}/compatibility/required_metadata/"
                    f"{metadata_index}"
                )
                if available is None:
                    issues.append(
                        ContractIssue(
                            code="REQUIRED_METADATA_MISSING",
                            json_pointer=pointer + "/object_name",
                            message_ru=(
                                f"В database profile нет {requirement.object_name}."
                            ),
                        )
                    )
                    continue
                missing = set(requirement.attributes) - available
                if missing:
                    issues.append(
                        ContractIssue(
                            code="REQUIRED_METADATA_ATTRIBUTE_MISSING",
                            json_pointer=pointer + "/attributes",
                            message_ru=(
                                "В database profile отсутствуют поля: "
                                + ", ".join(sorted(missing))
                            ),
                        )
                    )
        raise_for_issues(issues)

    @staticmethod
    def _check_replace_match(
        skills: tuple[Skill, ...], pinned: PinnedCatalog, if_match: str | None
    ) -> None:
        if if_match is None:
            raise CatalogConflictError("Replace требует заголовок If-Match.")
        if len(skills) == 1:
            current = pinned.skills[skills[0].skill_id].integrity.digest
            if if_match != current:
                raise CatalogConflictError("If-Match не совпадает с active digest.")
            return
        if if_match != pinned.digest:
            raise CatalogConflictError(
                "Replace нескольких навыков требует If-Match digest всего snapshot."
            )

    @staticmethod
    def _validate_dependency_closure(skills: Mapping[str, Skill]) -> None:
        issues: list[ContractIssue] = []
        graph: dict[str, set[str]] = {skill_id: set() for skill_id in skills}
        for skill in skills.values():
            for index, dependency in enumerate(skill.dependencies.skills):
                candidate = skills.get(dependency.skill_id)
                pointer = f"/active/{skill.skill_id}/dependencies/skills/{index}"
                if candidate is None:
                    issues.append(
                        ContractIssue(
                            code="SKILL_DEPENDENCY_MISSING",
                            json_pointer=pointer + "/skill_id",
                            message_ru=f"Отсутствует dependency {dependency.skill_id}.",
                        )
                    )
                    continue
                if not _version_matches(candidate.version, dependency.version_range):
                    issues.append(
                        ContractIssue(
                            code="DEPENDENCY_VERSION_INCOMPATIBLE",
                            json_pointer=pointer + "/version_range",
                            message_ru="Active dependency version несовместима.",
                        )
                    )
                missing = set(dependency.required_fact_types) - set(
                    candidate.provides.fact_types
                )
                if missing:
                    issues.append(
                        ContractIssue(
                            code="DEPENDENCY_FACT_TYPE_INCOMPATIBLE",
                            json_pointer=pointer + "/required_fact_types",
                            message_ru="Dependency не предоставляет required fact types.",
                        )
                    )
                graph[skill.skill_id].add(candidate.skill_id)
        issues.extend(_cycle_issues(graph))
        raise_for_issues(issues)

    @staticmethod
    def _dependency_closure(
        active: Mapping[str, Skill], skill_ids: Iterable[str] | None
    ) -> dict[str, Skill]:
        requested = set(active) if skill_ids is None else set(skill_ids)
        missing = requested - set(active)
        if missing:
            raise ApplicationError(
                "SKILL_NOT_FOUND",
                "Навыки не найдены: " + ", ".join(sorted(missing)),
                404,
            )
        selected: dict[str, Skill] = {}
        pending = list(requested)
        while pending:
            skill_id = pending.pop()
            if skill_id in selected:
                continue
            skill = active[skill_id]
            selected[skill_id] = skill
            pending.extend(dep.skill_id for dep in skill.dependencies.skills)
        return selected


def _version_matches(version: str, specification: str) -> bool:
    parsed = Version(version)
    try:
        return NpmSpec(specification).match(parsed)
    except ValueError:
        normalized = specification.replace(" ", ",")
        return SimpleSpec(normalized).match(parsed)


def _wire_skill(skill: Skill) -> dict[str, object]:
    """Serialize a skill exactly as the closed JSON Schema wire contract permits."""

    return skill.model_dump(mode="json", by_alias=True, exclude_none=True)


def _cycle_issues(graph: Mapping[str, set[str]]) -> tuple[ContractIssue, ...]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    for skill_id in graph:
        if visit(skill_id):
            return (
                ContractIssue(
                    code="DEPENDENCY_CYCLE",
                    json_pointer="/active",
                    message_ru="Active catalog содержит цикл зависимостей.",
                ),
            )
    return ()
