"""Semantic validation that JSON Schema intentionally cannot express."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from semantic_version import NpmSpec, SimpleSpec, Version

from chatbot1c.contracts.errors import ContractIssue, raise_for_issues
from chatbot1c.contracts.query import inspect_query_contract
from chatbot1c.domain.evidence import (
    CitationValue,
    DocumentFragment,
    EvidenceBundle,
    Fact,
)
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import (
    AggregateOperator,
    Binding,
    CalculateOperator,
    CountOperator,
    ExecuteResult,
    FactRequirement,
    FilterOperator,
    JoinOperator,
    NormalizePeriodOperator,
    PlannerOutput,
    RankOperator,
    SkillCall,
    SlotBinding,
    StepBinding,
)
from chatbot1c.domain.skill import (
    DataQueryOperation,
    DocumentationFixture,
    DocumentationRetrievalOperation,
    FactDefinition,
    FactValueType,
    KeysetPagination,
    McpFixture,
    Parameter,
    ParameterValueType,
    Skill,
    UnitFromFact,
)
from chatbot1c.domain.types import EntityRef, Period

_PLACEHOLDER_RE = re.compile(r"<[^>\r\n]+>|\{\{[^}\r\n]+}}|\$\{[^}\r\n]+}")
_QUERY_PARAMETER_RE = re.compile(r"&([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*")
_NON_EMPTY_LITERAL_RE = re.compile(r'"(?:[^"\r\n]|"")+"|\'(?:[^\'\r\n]|\'\')+\'')
_NUMERIC_PREDICATE_RE = re.compile(r"(?:=|<>|<=|>=|<|>)\s*[+-]?[0-9]")
_FORBIDDEN_QUERY_WORDS = frozenset(
    {
        "ВСТАВИТЬ",
        "ВЫПОЛНИТЬ",
        "ЗАПИСАТЬ",
        "ИЗМЕНИТЬ",
        "ОБНОВИТЬ",
        "ПОМЕСТИТЬ",
        "СОЗДАТЬ",
        "УДАЛИТЬ",
        "УНИЧТОЖИТЬ",
        "DELETE",
        "DROP",
        "EXEC",
        "EXECUTE",
        "INSERT",
        "MERGE",
        "UPDATE",
    }
)

T = TypeVar("T")

_ERROR_CODE_ALIASES: dict[str, tuple[str, ...]] = {
    "DISAGREEMENT_CITATIONS_NOT_DISTINCT": (
        "DISAGREEMENT_DISTINCT_CITATIONS_REQUIRED",
    ),
    "DISAGREEMENT_FACT_UNKNOWN": ("DISAGREEMENT_FACT_REFERENCE_MISSING",),
    "DISAGREEMENT_SUBJECT_MISMATCH": (
        "DISAGREEMENT_SUBJECT_FACT_MISMATCH",
    ),
    "PACKAGE_LOCK_MISSING": ("DEPENDENCY_LOCK_MISSING",),
    "PACKAGE_LOCK_ORPHAN": ("DEPENDENCY_LOCK_ORPHAN",),
    "QUERY_FINAL_PROJECTION_BINDING_MISMATCH": (
        "QUERY_BINDING_ALIAS_MISSING",
    ),
    "QUERY_PARAMETER_UNDECLARED": ("QUERY_PARAMETER_UNBOUND",),
    "SKILL_DIGEST_CONFLICT": ("PACKAGE_CATALOG_DIGEST_CONFLICT",),
}


class SemanticValidator:
    """Validate domain boundaries and return all deterministic issues."""

    def issues(
        self,
        document: Skill | SkillPackage | PlannerOutput | EvidenceBundle,
        *,
        available_skills: Sequence[Skill] = (),
    ) -> tuple[ContractIssue, ...]:
        if isinstance(document, Skill):
            issues = self.skill_issues(document)
        elif isinstance(document, SkillPackage):
            issues = self.package_issues(document, available_skills=available_skills)
        elif isinstance(document, PlannerOutput):
            issues = self.plan_issues(document, available_skills=available_skills)
        elif isinstance(document, EvidenceBundle):
            issues = self.evidence_issues(document, available_skills=available_skills)
        else:  # pragma: no cover - protected by the public type and harness
            raise TypeError(f"Unsupported domain document: {type(document)!r}")
        return _deduplicate(issues)

    def validate(
        self,
        document: Skill | SkillPackage | PlannerOutput | EvidenceBundle,
        *,
        available_skills: Sequence[Skill] = (),
    ) -> None:
        raise_for_issues(self.issues(document, available_skills=available_skills))

    def skill_issues(self, skill: Skill, prefix: str = "") -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        parameters = _indexed(skill.parameters, lambda item: item.name)
        facts = _indexed(skill.output_contract.facts, lambda item: item.fact_id)

        issues.extend(
            _duplicate_key_issues(
                skill.parameters,
                lambda item: item.name,
                prefix + "/parameters",
                "PARAMETER_DUPLICATE",
            )
        )
        issues.extend(
            _duplicate_key_issues(
                skill.output_contract.facts,
                lambda item: item.fact_id,
                prefix + "/output_contract/facts",
                "FACT_DEFINITION_DUPLICATE",
            )
        )
        issues.extend(
            _duplicate_key_issues(
                skill.tests,
                lambda item: item.test_id,
                prefix + "/tests",
                "TEST_ID_DUPLICATE",
            )
        )

        provided = set(skill.provides.fact_types)
        output_types = {
            fact.semantic_type for fact in skill.output_contract.facts
        }
        if provided != output_types:
            issues.append(
                _issue(
                    "SKILL_PROVIDES_OUTPUT_MISMATCH",
                    f"{prefix}/provides/fact_types",
                    "provides.fact_types должен точно совпадать с множеством output semantic types.",
                )
            )
        for index, fact in enumerate(skill.output_contract.facts):
            if fact.semantic_type not in provided:
                issues.append(
                    _issue(
                        "PROVIDED_FACT_TYPE_MISSING",
                        f"{prefix}/output_contract/facts/{index}/semantic_type",
                        "Semantic type факта отсутствует в provides.fact_types.",
                    )
                )
            if (
                isinstance(fact.unit_contract, UnitFromFact)
                and fact.unit_contract.fact_id not in facts
            ):
                issues.append(
                    _issue(
                        "UNIT_FACT_REFERENCE_MISSING",
                        f"{prefix}/output_contract/facts/{index}/unit_contract/fact_id",
                        "Unit contract ссылается на необъявленный fact_id.",
                    )
                )
        for index, semantic_type in enumerate(skill.provides.fact_types):
            if semantic_type not in output_types:
                issues.append(
                    _issue(
                        "PROVIDES_FACT_TYPE_ORPHAN",
                        f"{prefix}/provides/fact_types/{index}",
                        "Advertised fact type отсутствует в output_contract.facts.",
                    )
                )

        issues.extend(self._output_reference_issues(skill, facts, prefix))
        issues.extend(self._runtime_contract_issues(skill, prefix))
        issues.extend(self._fixture_issues(skill, parameters, facts, prefix))

        operation = skill.operation
        if isinstance(operation, DataQueryOperation):
            issues.extend(
                self._data_operation_issues(skill, operation, parameters, facts, prefix)
            )
        else:
            issues.extend(
                self._documentation_operation_issues(
                    skill, operation, parameters, facts, prefix
                )
            )
        return issues

    def _output_reference_issues(
        self,
        skill: Skill,
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        contract = skill.output_contract
        references: list[tuple[str, str]] = []
        references.extend(
            (f"{prefix}/output_contract/row_identity_fact_ids/{index}", fact_id)
            for index, fact_id in enumerate(contract.row_identity_fact_ids or ())
        )
        for set_index, fact_set in enumerate(contract.sufficiency.required_fact_sets):
            references.extend(
                (
                    f"{prefix}/output_contract/sufficiency/required_fact_sets/{set_index}/{fact_index}",
                    fact_id,
                )
                for fact_index, fact_id in enumerate(fact_set)
            )
        references.extend(
            (f"{prefix}/output_contract/sufficiency/zero_fact_ids/{index}", fact_id)
            for index, fact_id in enumerate(contract.sufficiency.zero_fact_ids)
        )
        references.extend(
            (f"{prefix}/output_contract/renderer/primary_fact_ids/{index}", fact_id)
            for index, fact_id in enumerate(contract.renderer.primary_fact_ids)
        )
        references.extend(
            (f"{prefix}/output_contract/renderer/column_fact_ids/{index}", fact_id)
            for index, fact_id in enumerate(contract.renderer.column_fact_ids)
        )
        for pointer, fact_id in references:
            if fact_id not in facts:
                issues.append(
                    _issue(
                        "OUTPUT_FACT_REFERENCE_MISSING",
                        pointer,
                        f"Fact reference '{fact_id}' не объявлен в output_contract.facts.",
                    )
                )

        numeric = {
            FactValueType.INTEGER,
            FactValueType.DECIMAL,
            FactValueType.MONEY,
            FactValueType.QUANTITY,
            FactValueType.PERCENTAGE,
        }
        for index, fact_id in enumerate(contract.sufficiency.zero_fact_ids):
            fact = facts.get(fact_id)
            if fact is not None and fact.value_type not in numeric:
                issues.append(
                    _issue(
                        "ZERO_FACT_NOT_NUMERIC",
                        f"{prefix}/output_contract/sufficiency/zero_fact_ids/{index}",
                        "zero_fact_ids может содержать только числовые факты.",
                    )
                )
        return issues

    def _runtime_contract_issues(
        self, skill: Skill, prefix: str
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        contracts = [
            str(item.contract) for item in skill.dependencies.runtime_contracts
        ]
        counts: Counter[str] = Counter(contracts)
        if counts["skill-runtime"] != 1:
            issues.append(
                _issue(
                    "RUNTIME_CONTRACT_MISSING",
                    f"{prefix}/dependencies/runtime_contracts",
                    "Требуется ровно один runtime contract 'skill-runtime'.",
                )
            )
        source_contracts = counts["mcp.execute_query"] + counts["help-index"]
        expected = (
            "mcp.execute_query"
            if isinstance(skill.operation, DataQueryOperation)
            else "help-index"
        )
        if source_contracts != 1 or counts[expected] != 1:
            issues.append(
                _issue(
                    "RUNTIME_CONTRACT_OPERATION_MISMATCH",
                    f"{prefix}/dependencies/runtime_contracts",
                    f"Operation требует ровно один source runtime contract '{expected}'.",
                )
            )
        if any(count > 1 for count in counts.values()):
            issues.append(
                _issue(
                    "RUNTIME_CONTRACT_DUPLICATE",
                    f"{prefix}/dependencies/runtime_contracts",
                    "Runtime contract указан более одного раза.",
                )
            )
        return issues

    def _fixture_issues(
        self,
        skill: Skill,
        parameters: dict[str, Parameter],
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        classes = {test.case_kind for test in skill.tests}
        if classes != {"positive", "negative"}:
            issues.append(
                _issue(
                    "POSITIVE_NEGATIVE_FIXTURES_REQUIRED",
                    f"{prefix}/tests",
                    "Skill обязан содержать хотя бы по одному positive и negative fixture.",
                )
            )
        for test_index, test in enumerate(skill.tests):
            expected_fixture = (
                McpFixture
                if isinstance(skill.operation, DataQueryOperation)
                else DocumentationFixture
            )
            if not isinstance(test.fixture, expected_fixture):
                issues.append(
                    _issue(
                        "FIXTURE_OPERATION_MISMATCH",
                        f"{prefix}/tests/{test_index}/fixture/kind",
                        "Тип fixture не соответствует operation kind.",
                    )
                )
            seen_bindings: set[str] = set()
            for binding_index, binding in enumerate(test.bindings):
                pointer = (
                    f"{prefix}/tests/{test_index}/bindings/{binding_index}/parameter"
                )
                if binding.parameter not in parameters:
                    issues.append(
                        _issue(
                            "FIXTURE_PARAMETER_MISSING",
                            pointer,
                            "Fixture binding ссылается на необъявленный parameter.",
                        )
                    )
                if binding.parameter in seen_bindings:
                    issues.append(
                        _issue(
                            "FIXTURE_PARAMETER_DUPLICATE",
                            pointer,
                            "Parameter fixture задан повторно.",
                        )
                    )
                seen_bindings.add(binding.parameter)
            for fact_index, fact_id in enumerate(test.expected.required_fact_ids):
                if fact_id not in facts:
                    issues.append(
                        _issue(
                            "FIXTURE_EXPECTED_FACT_MISSING",
                            f"{prefix}/tests/{test_index}/expected/required_fact_ids/{fact_index}",
                            "Fixture ожидает необъявленный fact_id.",
                        )
                    )
        return issues

    def _data_operation_issues(
        self,
        skill: Skill,
        operation: DataQueryOperation,
        parameters: dict[str, Parameter],
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        text = operation.query_template.text
        text_pointer = f"{prefix}/operation/query_template/text"
        code = _query_code(text)
        query_contract = inspect_query_contract(operation.query_template)
        issues.extend(
            _issue(
                problem.code,
                f"{prefix}/operation/query_template{problem.path}",
                problem.message,
            )
            for problem in query_contract.problems
        )

        if _PLACEHOLDER_RE.search(code):
            issues.append(
                _issue(
                    "UNRESOLVED_QUERY_PLACEHOLDER",
                    text_pointer,
                    "Query template содержит незамещенный placeholder.",
                )
            )
        binding_parameters: dict[str, int] = {}
        query_names: dict[str, int] = {}
        for index, binding in enumerate(operation.parameter_bindings):
            pointer = f"{prefix}/operation/parameter_bindings/{index}"
            if binding.parameter not in parameters:
                issues.append(
                    _issue(
                        "PARAMETER_BINDING_TARGET_MISSING",
                        pointer + "/parameter",
                        "Query binding ссылается на необъявленный parameter.",
                    )
                )
            else:
                if not _encoding_matches_parameter(
                    binding.encoding, parameters[binding.parameter]
                ):
                    issues.append(
                        _issue(
                            "PARAMETER_BINDING_TYPE_MISMATCH",
                            pointer + "/encoding",
                            "Encoding несовместим с parameter value_type.",
                        )
                    )
            if binding.parameter in binding_parameters:
                issues.append(
                    _issue(
                        "PARAMETER_BINDING_DUPLICATE",
                        pointer + "/parameter",
                        "Parameter имеет более одного query binding.",
                    )
                )
            binding_parameters[binding.parameter] = index
            normalized_query_name = binding.query_parameter.casefold()
            if normalized_query_name in query_names:
                issues.append(
                    _issue(
                        "QUERY_PARAMETER_BINDING_DUPLICATE",
                        pointer + "/query_parameter",
                        "Query parameter связан более одного раза.",
                    )
                )
            query_names[normalized_query_name] = index

        for name, parameter in parameters.items():
            if (
                parameter.value_type is not ParameterValueType.PAGINATION
                and name not in binding_parameters
            ):
                issues.append(
                    _issue(
                        "PARAMETER_BINDING_MISSING",
                        f"{prefix}/parameters",
                        f"Parameter '{name}' не имеет query binding.",
                    )
                )

        allowed_query_parameters = set(query_names)
        if isinstance(operation.pagination, KeysetPagination):
            allowed_query_parameters.add(
                operation.pagination.has_cursor_query_parameter.casefold()
            )
            for cursor_binding in operation.pagination.cursor_bindings:
                allowed_query_parameters.add(
                    cursor_binding.query_parameter.casefold()
                )
        if query_contract.parsed is not None:
            used_query_parameters = {
                name.casefold() for name in query_contract.parsed.parameters
            }
            for name in sorted(used_query_parameters - allowed_query_parameters):
                issues.append(
                    _issue(
                        "QUERY_PARAMETER_UNDECLARED",
                        text_pointer,
                        f"Query parameter '&{name}' не имеет exact binding.",
                    )
                )
            for name in sorted(allowed_query_parameters - used_query_parameters):
                issues.append(
                    _issue(
                        "QUERY_PARAMETER_UNUSED",
                        f"{prefix}/operation/parameter_bindings",
                        f"Declared query parameter '{name}' отсутствует в template.",
                    )
                )

        aliases = (
            list(query_contract.parsed.final_projection_aliases)
            if query_contract.parsed is not None
            else []
        )
        alias_counts = Counter(alias.casefold() for alias in aliases)
        for alias, count in alias_counts.items():
            if count > 1:
                issues.append(
                    _issue(
                        "QUERY_ALIAS_DUPLICATE",
                        text_pointer,
                        f"Projection alias '{alias}' указан более одного раза.",
                    )
                )
        columns = [binding.column for binding in operation.column_bindings]
        normalized_columns = [column.casefold() for column in columns]
        column_counts = Counter(normalized_columns)
        for index, column_binding in enumerate(operation.column_bindings):
            pointer = f"{prefix}/operation/column_bindings/{index}"
            normalized_column = column_binding.column.casefold()
            if column_counts[normalized_column] > 1:
                issues.append(
                    _issue(
                        "COLUMN_BINDING_ALIAS_DUPLICATE",
                        pointer + "/column",
                        "Column alias имеет более одного binding.",
                    )
                )
            fact = facts.get(column_binding.fact_id)
            if fact is None:
                issues.append(
                    _issue(
                        "COLUMN_BINDING_FACT_MISSING",
                        pointer + "/fact_id",
                        "Column binding ссылается на необъявленный fact_id.",
                    )
                )
            elif not _converter_matches_fact(column_binding.converter, fact):
                issues.append(
                    _issue(
                        "COLUMN_BINDING_TYPE_MISMATCH",
                        pointer + "/converter",
                        "Converter несовместим с fact value_type.",
                    )
                )
            if normalized_column not in alias_counts:
                issues.append(
                    _issue(
                        "QUERY_FINAL_PROJECTION_BINDING_MISMATCH",
                        pointer + "/column",
                        "Column binding не имеет exact projection alias в query template.",
                    )
                )
        bound_columns = set(normalized_columns)
        required_binding_missing = any(
            fact.required
            and Counter(binding.fact_id for binding in operation.column_bindings)[
                fact.fact_id
            ]
            != 1
            for fact in skill.output_contract.facts
        )
        if not required_binding_missing:
            for alias in sorted(set(alias_counts) - bound_columns):
                issues.append(
                    _issue(
                        "QUERY_ALIAS_BINDING_MISSING",
                        text_pointer,
                        f"Projection alias '{alias}' не имеет column binding.",
                    )
                )

        issues.extend(
            _required_binding_issues(
                skill,
                [binding.fact_id for binding in operation.column_bindings],
                f"{prefix}/operation/column_bindings",
            )
        )
        if isinstance(operation.pagination, KeysetPagination):
            for index, sort in enumerate(operation.pagination.sort):
                if sort.fact_id not in facts:
                    issues.append(
                        _issue(
                            "PAGINATION_FACT_MISSING",
                            f"{prefix}/operation/pagination/sort/{index}/fact_id",
                            "Keyset sort ссылается на необъявленный fact_id.",
                        )
                    )
            for index, cursor_binding in enumerate(
                operation.pagination.cursor_bindings
            ):
                if cursor_binding.fact_id not in facts:
                    issues.append(
                        _issue(
                            "PAGINATION_FACT_MISSING",
                            f"{prefix}/operation/pagination/cursor_bindings/{index}/fact_id",
                            "Cursor binding ссылается на необъявленный fact_id.",
                        )
                    )
        return issues

    def _documentation_operation_issues(
        self,
        skill: Skill,
        operation: DocumentationRetrievalOperation,
        parameters: dict[str, Parameter],
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        parameter = parameters.get(operation.query_parameter)
        if parameter is None:
            issues.append(
                _issue(
                    "DOCUMENTATION_QUERY_PARAMETER_MISSING",
                    f"{prefix}/operation/query_parameter",
                    "Documentation query_parameter не объявлен в parameters.",
                )
            )
        elif parameter.value_type not in {
            ParameterValueType.STRING,
            ParameterValueType.NORMALIZED_TEXT,
        }:
            issues.append(
                _issue(
                    "DOCUMENTATION_QUERY_PARAMETER_TYPE_MISMATCH",
                    f"{prefix}/operation/query_parameter",
                    "Documentation query_parameter должен быть string/normalized_text.",
                )
            )
        seen_fields: set[str] = set()
        for index, binding in enumerate(operation.output_bindings):
            pointer = f"{prefix}/operation/output_bindings/{index}"
            if binding.chunk_field in seen_fields:
                issues.append(
                    _issue(
                        "DOCUMENTATION_OUTPUT_FIELD_DUPLICATE",
                        pointer + "/chunk_field",
                        "Chunk field имеет более одного output binding.",
                    )
                )
            seen_fields.add(binding.chunk_field)
            if binding.fact_id not in facts:
                issues.append(
                    _issue(
                        "DOCUMENTATION_OUTPUT_FACT_MISSING",
                        pointer + "/fact_id",
                        "Output binding ссылается на необъявленный fact_id.",
                    )
                )
        issues.extend(
            _required_binding_issues(
                skill,
                [binding.fact_id for binding in operation.output_bindings],
                f"{prefix}/operation/output_bindings",
            )
        )
        return issues

    def package_issues(
        self,
        package: SkillPackage,
        *,
        available_skills: Sequence[Skill] = (),
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        for index, skill in enumerate(package.skills):
            issues.extend(self.skill_issues(skill, prefix=f"/skills/{index}"))

        embedded: dict[tuple[str, str], tuple[int, Skill]] = {}
        for index, skill in enumerate(package.skills):
            pair = (skill.skill_id, skill.version)
            if pair in embedded:
                issues.append(
                    _issue(
                        "PACKAGE_SKILL_DUPLICATE",
                        f"/skills/{index}",
                        "Пара skill_id/version повторяется в package.",
                    )
                )
            else:
                embedded[pair] = (index, skill)
            issues.extend(_target_compatibility_issues(package, skill, index))

        lock: dict[tuple[str, str], tuple[int, str]] = {}
        for index, entry in enumerate(package.dependency_lock):
            pair = (entry.skill_id, entry.version)
            if pair in lock:
                issues.append(
                    _issue(
                        "DEPENDENCY_LOCK_DUPLICATE",
                        f"/dependency_lock/{index}",
                        "Пара skill_id/version повторяется в dependency lock.",
                    )
                )
            else:
                lock[pair] = (index, entry.digest)

        documents_by_pair: dict[tuple[str, str], list[Skill]] = defaultdict(list)
        candidates_by_id: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for pair, (_, skill) in embedded.items():
            documents_by_pair[pair].append(skill)
            candidates_by_id[skill.skill_id].add(pair)
        for skill in available_skills:
            pair = (skill.skill_id, skill.version)
            documents_by_pair[pair].append(skill)
            candidates_by_id[skill.skill_id].add(pair)

        for pair, (index, embedded_skill) in embedded.items():
            if any(
                candidate.integrity.digest != embedded_skill.integrity.digest
                for candidate in documents_by_pair[pair]
            ):
                issues.append(
                    _issue(
                        "SKILL_DIGEST_CONFLICT",
                        f"/skills/{index}/integrity/digest",
                        "Pinned catalog содержит тот же skill_id/version с другим digest.",
                    )
                )

        graph: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        selected: dict[tuple[str, str], Skill] = {
            pair: skill for pair, (_, skill) in embedded.items()
        }
        pointers: dict[tuple[str, str], str] = {
            pair: f"/skills/{index}" for pair, (index, _) in embedded.items()
        }
        pending = deque(embedded)
        processed: set[tuple[str, str]] = set()
        while pending:
            pair = pending.popleft()
            if pair in processed:
                continue
            processed.add(pair)
            skill = selected[pair]
            base_pointer = pointers[pair]
            for dep_index, dependency in enumerate(skill.dependencies.skills):
                dep_pointer = f"{base_pointer}/dependencies/skills/{dep_index}"
                same_id = candidates_by_id.get(dependency.skill_id, set())
                matching_pairs = [
                    candidate_pair
                    for candidate_pair in same_id
                    if _version_matches(
                        candidate_pair[1], dependency.version_range
                    )
                ]
                if not same_id:
                    issues.append(
                        _issue(
                            "DEPENDENCY_MISSING",
                            dep_pointer + "/skill_id",
                            f"Dependency skill '{dependency.skill_id}' отсутствует.",
                        )
                    )
                    continue
                if not matching_pairs:
                    issues.append(
                        _issue(
                            "DEPENDENCY_VERSION_INCOMPATIBLE",
                            dep_pointer + "/version_range",
                            "Нет dependency version, совместимой с version_range.",
                        )
                    )
                    continue

                locked_pairs = [
                    locked_pair
                    for locked_pair in lock
                    if locked_pair[0] == dependency.skill_id
                    and _version_matches(locked_pair[1], dependency.version_range)
                ]
                if not locked_pairs:
                    selected_pair = max(
                        matching_pairs, key=lambda item: Version(item[1])
                    )
                    issues.append(
                        _issue(
                            "PACKAGE_LOCK_MISSING",
                            "/dependency_lock",
                            f"Dependency {dependency.skill_id} не закреплена exact lock entry.",
                        )
                    )
                else:
                    if len(locked_pairs) > 1:
                        issues.append(
                            _issue(
                                "DEPENDENCY_LOCK_AMBIGUOUS",
                                "/dependency_lock",
                                f"Dependency {dependency.skill_id} имеет несколько совместимых lock entries.",
                            )
                        )
                    selected_pair = max(
                        locked_pairs, key=lambda item: Version(item[1])
                    )

                selected_documents = documents_by_pair.get(selected_pair, [])
                if not selected_documents:
                    issues.append(
                        _issue(
                            "DEPENDENCY_LOCK_TARGET_MISSING",
                            f"/dependency_lock/{lock[selected_pair][0]}",
                            "Lock entry не разрешается в embedded skills или pinned catalog.",
                        )
                    )
                    continue
                selected_skill = selected_documents[0]
                selected_digests = {
                    candidate.integrity.digest for candidate in selected_documents
                }
                if len(selected_digests) > 1:
                    lock_index = lock.get(selected_pair, (0, ""))[0]
                    issues.append(
                        _issue(
                            "SKILL_DIGEST_CONFLICT",
                            f"/dependency_lock/{lock_index}/digest",
                            "Одинаковая dependency version имеет разные digests в catalog/package.",
                        )
                    )
                missing_facts = set(dependency.required_fact_types) - set(
                    selected_skill.provides.fact_types
                )
                if missing_facts:
                    issues.append(
                        _issue(
                            "DEPENDENCY_FACT_TYPE_INCOMPATIBLE",
                            dep_pointer + "/required_fact_types",
                            "Dependency не предоставляет все required_fact_types.",
                        )
                    )
                graph[pair].add(selected_pair)
                if selected_pair not in selected:
                    selected[selected_pair] = selected_skill
                    pointers[selected_pair] = "/available_skills"
                    pending.append(selected_pair)

        expected_pairs = set(selected)
        for pair, skill in selected.items():
            locked = lock.get(pair)
            if locked is None:
                issues.append(
                    _issue(
                        "PACKAGE_LOCK_MISSING",
                        "/dependency_lock",
                        f"Expected {skill.skill_id}@{skill.version} отсутствует в lock.",
                    )
                )
            elif locked[1] != skill.integrity.digest:
                issues.append(
                    _issue(
                        "DEPENDENCY_LOCK_DIGEST_MISMATCH",
                        f"/dependency_lock/{locked[0]}/digest",
                        "Lock digest не совпадает с exact resolved skill digest.",
                    )
                )
        for pair, (index, _) in lock.items():
            if pair not in expected_pairs:
                issues.append(
                    _issue(
                        "PACKAGE_LOCK_ORPHAN",
                        f"/dependency_lock/{index}",
                        "Lock entry не входит в транзитивное замыкание imported roots.",
                    )
                )

        cycle = _find_cycle(graph)
        if cycle:
            pointer = pointers.get(cycle[0], "/skills") + "/dependencies/skills"
            rendered = " -> ".join(
                f"{skill_id}@{version}" for skill_id, version in cycle
            )
            issues.append(
                _issue(
                    "DEPENDENCY_CYCLE",
                    pointer,
                    f"Dependency graph содержит cycle: {rendered}.",
                )
            )
        return issues

    def plan_issues(
        self,
        plan: PlannerOutput,
        *,
        available_skills: Sequence[Skill] = (),
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        requirement_ids = {
            item.requirement_id for item in plan.interpretation.required_facts
        }
        slot_ids = {item.slot_id for item in plan.interpretation.slots}
        issues.extend(
            _duplicate_key_issues(
                plan.interpretation.required_facts,
                lambda item: item.requirement_id,
                "/interpretation/required_facts",
                "PLAN_REQUIREMENT_DUPLICATE",
            )
        )
        issues.extend(
            _duplicate_key_issues(
                plan.interpretation.slots,
                lambda item: item.slot_id,
                "/interpretation/slots",
                "PLAN_SLOT_DUPLICATE",
            )
        )

        if plan.result.kind == "clarify":
            for index, requirement_id in enumerate(plan.result.missing_requirement_ids):
                if requirement_id not in requirement_ids:
                    issues.append(
                        _issue(
                            "PLAN_REQUIREMENT_REFERENCE_MISSING",
                            f"/result/missing_requirement_ids/{index}",
                            "Clarification ссылается на неизвестный requirement_id.",
                        )
                    )
            for index, choice in enumerate(plan.result.choices):
                if choice.slot_id not in slot_ids:
                    issues.append(
                        _issue(
                            "PLAN_SLOT_REFERENCE_MISSING",
                            f"/result/choices/{index}/slot_id",
                            "Clarification choice ссылается на неизвестный slot_id.",
                        )
                    )
            return issues
        if not isinstance(plan.result, ExecuteResult):
            return issues

        steps = plan.result.steps
        step_index: dict[str, int] = {}
        for index, step in enumerate(steps):
            if step.step_id in step_index:
                issues.append(
                    _issue(
                        "PLAN_STEP_DUPLICATE",
                        f"/result/steps/{index}/step_id",
                        "step_id повторяется в плане.",
                    )
                )
            step_index[step.step_id] = index

        graph: dict[str, set[str]] = defaultdict(set)
        catalog: dict[tuple[str, str], Skill] = {}
        for skill in available_skills:
            pair = (skill.skill_id, skill.version)
            existing = catalog.get(pair)
            if (
                existing is not None
                and existing.integrity.digest != skill.integrity.digest
            ):
                issues.append(
                    _issue(
                        "PLAN_CATALOG_DIGEST_CONFLICT",
                        "/catalog_snapshot_id",
                        "Pinned catalog содержит одинаковую skill version с разными digests.",
                    )
                )
            catalog.setdefault(pair, skill)
        for index, step in enumerate(steps):
            pointer = f"/result/steps/{index}"
            dependencies = _step_dependencies(step)
            for dependency in dependencies:
                if dependency not in step_index:
                    issues.append(
                        _issue(
                            "PLAN_STEP_REFERENCE_MISSING",
                            pointer,
                            f"Step ссылается на отсутствующий step_id '{dependency}'.",
                        )
                    )
                else:
                    graph[step.step_id].add(dependency)
            for binding in _step_bindings(step):
                if isinstance(binding, SlotBinding) and binding.slot_id not in slot_ids:
                    issues.append(
                        _issue(
                            "PLAN_SLOT_REFERENCE_MISSING",
                            pointer,
                            f"Binding ссылается на неизвестный slot_id '{binding.slot_id}'.",
                        )
                    )
            if isinstance(step, SkillCall):
                resolved_skill = catalog.get((step.skill_id, step.skill_version))
                if resolved_skill is None:
                    issues.append(
                        _issue(
                            "PLAN_SKILL_MISSING",
                            pointer + "/skill_id",
                            "Plan использует skill/version вне pinned catalog.",
                        )
                    )
                else:
                    declared_facts = {
                        fact.fact_id for fact in resolved_skill.output_contract.facts
                    }
                    for fact_index, fact_id in enumerate(step.required_output_fact_ids):
                        if fact_id not in declared_facts:
                            issues.append(
                                _issue(
                                    "PLAN_SKILL_OUTPUT_MISSING",
                                    pointer + f"/required_output_fact_ids/{fact_index}",
                                    "Plan требует fact_id, отсутствующий в skill output contract.",
                                )
                            )

        cycle = _find_cycle(graph)
        if cycle:
            issues.append(
                _issue(
                    "PLAN_DEPENDENCY_CYCLE",
                    "/result/steps",
                    f"Plan DAG содержит cycle: {' -> '.join(cycle)}.",
                )
            )
        for index, output in enumerate(plan.result.final_outputs):
            if output.step_id not in step_index:
                issues.append(
                    _issue(
                        "PLAN_FINAL_OUTPUT_STEP_MISSING",
                        f"/result/final_outputs/{index}/step_id",
                        "Final output ссылается на отсутствующий step_id.",
                    )
                )
        issues.extend(_plan_coverage_issues(plan, catalog))
        return issues

    def evidence_issues(
        self,
        evidence: EvidenceBundle,
        *,
        available_skills: Sequence[Skill] = (),
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        if evidence.coverage.sufficient and any(
            requirement.status is not CoverageStatus.COVERED
            for requirement in evidence.coverage.requirements
        ):
            issues.append(
                _issue(
                    "SUFFICIENT_COVERAGE_HAS_MISSING_REQUIREMENTS",
                    "/coverage/sufficient",
                    "coverage.sufficient=true несовместим с не покрытым requirement.",
                )
            )

        if evidence.outcome is Outcome.SUCCESS_EMPTY and (
            evidence.facts or any(step.row_count > 0 for step in evidence.steps)
        ):
            issues.append(
                _issue(
                    "ZERO_AGGREGATE_CLASSIFIED_AS_EMPTY",
                    "/outcome",
                    "success_empty не может содержать строки или подтвержденные факты; нулевой aggregate имеет отдельный outcome.",
                )
            )
        if evidence.outcome is Outcome.ZERO_AGGREGATE:
            numeric_zero = any(
                type(fact.value) in {int, float} and fact.value == 0
                for fact in evidence.facts
            )
            if not numeric_zero or not any(
                step.row_count > 0 for step in evidence.steps
            ):
                issues.append(
                    _issue(
                        "ZERO_AGGREGATE_WITHOUT_ZERO_FACT",
                        "/outcome",
                        "zero_aggregate требует строку и подтвержденный числовой факт со значением 0.",
                    )
                )

        fact_index: dict[object, tuple[int, Fact]] = {}
        catalog = {(skill.skill_id, skill.version): skill for skill in available_skills}
        skill_by_step = {
            step.step_id: catalog[pair]
            for step in evidence.steps
            if (pair := _skill_operation_pair(step.operation_ref)) in catalog
        }
        for index, fact in enumerate(evidence.facts):
            if fact.fact_instance_id in fact_index:
                issues.append(
                    _issue(
                        "FACT_INSTANCE_DUPLICATE",
                        f"/facts/{index}/fact_instance_id",
                        "fact_instance_id повторяется.",
                    )
                )
            fact_index[fact.fact_instance_id] = (index, fact)
            issues.extend(_fact_value_issues(fact, index))
            if fact.value_type is FactValueType.ENTITY_REF and isinstance(
                fact.value, EntityRef
            ):
                exact_types = _accepted_entity_types(
                    skill_by_step.get(fact.step_id), fact
                )
                if not _entity_semantics_match(
                    fact.semantic_type, fact.value.object_type
                ) or (exact_types and fact.value.object_type not in exact_types):
                    issues.append(
                        _issue(
                            "ENTITY_REF_SEMANTIC_TYPE_MISMATCH",
                            f"/facts/{index}/value/ТипОбъекта",
                            "Физический ТипОбъекта несовместим с business semantic_type факта.",
                        )
                    )

        step_ids = {step.step_id for step in evidence.steps}
        for index, fact in enumerate(evidence.facts):
            if fact.step_id not in step_ids:
                issues.append(
                    _issue(
                        "FACT_STEP_REFERENCE_MISSING",
                        f"/facts/{index}/step_id",
                        "Fact ссылается на отсутствующий evidence step.",
                    )
                )
        for step_index, step in enumerate(evidence.steps):
            for fact_id_index, fact_instance_id in enumerate(
                step.produced_fact_instance_ids
            ):
                if fact_instance_id not in fact_index:
                    issues.append(
                        _issue(
                            "STEP_FACT_REFERENCE_MISSING",
                            f"/steps/{step_index}/produced_fact_instance_ids/{fact_id_index}",
                            "Step ссылается на отсутствующий fact_instance_id.",
                        )
                    )

        for requirement_index, requirement in enumerate(evidence.coverage.requirements):
            if (
                requirement.status is not CoverageStatus.COVERED
                and requirement.fact_instance_ids
            ):
                issues.append(
                    _issue(
                        "UNCOVERED_REQUIREMENT_HAS_FACTS",
                        f"/coverage/requirements/{requirement_index}/fact_instance_ids",
                        "Непокрытый requirement не должен ссылаться на факты.",
                    )
                )
            for reference_index, fact_instance_id in enumerate(
                requirement.fact_instance_ids
            ):
                resolved = fact_index.get(fact_instance_id)
                if resolved is None:
                    issues.append(
                        _issue(
                            "COVERAGE_FACT_REFERENCE_MISSING",
                            f"/coverage/requirements/{requirement_index}/fact_instance_ids/{reference_index}",
                            "Coverage ссылается на отсутствующий fact_instance_id.",
                        )
                    )
                elif resolved[1].semantic_type != requirement.semantic_type:
                    issues.append(
                        _issue(
                            "COVERAGE_FACT_SEMANTIC_TYPE_MISMATCH",
                            f"/coverage/requirements/{requirement_index}/fact_instance_ids/{reference_index}",
                            "Coverage fact semantic_type не совпадает с requirement.",
                        )
                    )

        for export_index, export in enumerate(evidence.context_exports):
            resolved = fact_index.get(export.fact_instance_id)
            if resolved is None:
                issues.append(
                    _issue(
                        "CONTEXT_FACT_REFERENCE_MISSING",
                        f"/context_exports/{export_index}/fact_instance_id",
                        "Context export ссылается на отсутствующий fact_instance_id.",
                    )
                )
            elif resolved[1].semantic_type != export.semantic_type:
                issues.append(
                    _issue(
                        "CONTEXT_SEMANTIC_TYPE_MISMATCH",
                        f"/context_exports/{export_index}/semantic_type",
                        "Context semantic_type не совпадает с экспортируемым фактом.",
                    )
                )

        citation_ids = {citation.citation_id for citation in evidence.citations}
        citation_facts: dict[object, list[Fact]] = defaultdict(list)
        for fact in evidence.facts:
            if fact.value_type is FactValueType.SOURCE_CITATION and isinstance(
                fact.value, CitationValue
            ):
                citation_facts[fact.value.citation_id].append(fact)
        for disagreement_index, disagreement in enumerate(
            evidence.documentation_disagreements
        ):
            disagreement_citations: set[object] = set()
            disagreement_facts: set[object] = set()
            for position_index, position in enumerate(disagreement.positions):
                position_fragments: list[Fact] = []
                for ref_index, fact_instance_id in enumerate(
                    position.fact_instance_ids
                ):
                    pointer = f"/documentation_disagreements/{disagreement_index}/positions/{position_index}/fact_instance_ids/{ref_index}"
                    resolved = fact_index.get(fact_instance_id)
                    if resolved is None:
                        issues.append(
                            _issue(
                                "DISAGREEMENT_FACT_UNKNOWN",
                                pointer,
                                "Disagreement ссылается на отсутствующий факт.",
                            )
                        )
                        continue
                    fact = resolved[1]
                    if fact_instance_id in disagreement_facts:
                        issues.append(
                            _issue(
                                "DISAGREEMENT_FACT_REUSED",
                                pointer,
                                "Один fragment fact не может обосновывать разные positions.",
                            )
                        )
                    disagreement_facts.add(fact_instance_id)
                    if fact.fact_id != disagreement.subject_fact_id:
                        issues.append(
                            _issue(
                                "DISAGREEMENT_SUBJECT_MISMATCH",
                                pointer,
                                "Fact fact_id не совпадает с disagreement.subject_fact_id.",
                            )
                        )
                    if fact.value_type is not FactValueType.DOCUMENT_FRAGMENT or not isinstance(
                        fact.value, DocumentFragment
                    ):
                        issues.append(
                            _issue(
                                "DISAGREEMENT_FACT_TYPE_MISMATCH",
                                pointer,
                                "Disagreement position может ссылаться только на document_fragment facts.",
                            )
                        )
                        continue
                    position_fragments.append(fact)
                    if fact.source_locator.kind != "documentation_chunk":
                        issues.append(
                            _issue(
                                "DISAGREEMENT_FRAGMENT_PROVENANCE_MISMATCH",
                                pointer,
                                "Fragment disagreement должен иметь documentation_chunk provenance.",
                            )
                        )
                for ref_index, citation_id in enumerate(position.citation_ids):
                    pointer = f"/documentation_disagreements/{disagreement_index}/positions/{position_index}/citation_ids/{ref_index}"
                    if citation_id not in citation_ids:
                        issues.append(
                            _issue(
                                "DISAGREEMENT_CITATION_UNKNOWN",
                                pointer,
                                "Disagreement ссылается на отсутствующую citation.",
                            )
                        )
                    if citation_id in disagreement_citations:
                        issues.append(
                            _issue(
                                "DISAGREEMENT_CITATION_REUSED",
                                pointer,
                                "Одна citation не может обосновывать разные disagreement positions.",
                            )
                        )
                    disagreement_citations.add(citation_id)
                    provenance = citation_facts.get(citation_id, [])
                    if position_fragments and not any(
                        citation_fact.row_id == fragment.row_id
                        and citation_fact.step_id == fragment.step_id
                        and citation_fact.source_locator.kind
                        == "documentation_chunk"
                        and citation_fact.source_locator.reference
                        == fragment.source_locator.reference
                        for citation_fact in provenance
                        for fragment in position_fragments
                    ):
                        issues.append(
                            _issue(
                                "DISAGREEMENT_POSITION_PROVENANCE_MISSING",
                                pointer,
                                "Citation не связана с fragment fact этой position по row/step/chunk.",
                            )
                        )
            if len(disagreement_citations) < 2:
                issues.append(
                    _issue(
                        "DISAGREEMENT_CITATIONS_NOT_DISTINCT",
                        f"/documentation_disagreements/{disagreement_index}/positions",
                        "Disagreement требует минимум две разные grounded citations.",
                    )
                )
        return issues


def _issue(code: str, pointer: str, message: str) -> ContractIssue:
    return ContractIssue(code=code, json_pointer=pointer, message_ru=message)


def _deduplicate(issues: Iterable[ContractIssue]) -> tuple[ContractIssue, ...]:
    expanded: list[ContractIssue] = []
    for issue in issues:
        expanded.append(issue)
        expanded.extend(
            ContractIssue(
                code=alias,
                json_pointer=issue.json_pointer,
                message_ru=issue.message_ru,
                keyword=issue.keyword,
            )
            for alias in _ERROR_CODE_ALIASES.get(issue.code, ())
        )
    unique = {
        (issue.code, issue.json_pointer, issue.message_ru): issue for issue in expanded
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda issue: (issue.json_pointer, issue.code, issue.message_ru),
        )
    )


def _indexed(items: Iterable[T], key: Callable[[T], object]) -> dict[str, T]:
    return {str(key(item)): item for item in items}


def _duplicate_key_issues(
    items: Sequence[T],
    key: Callable[[T], object],
    pointer: str,
    code: str,
) -> list[ContractIssue]:
    seen: set[object] = set()
    issues: list[ContractIssue] = []
    for index, item in enumerate(items):
        value = key(item)
        if value in seen:
            issues.append(
                _issue(
                    code, f"{pointer}/{index}", f"Значение '{value}' указано повторно."
                )
            )
        seen.add(value)
    return issues


def _required_binding_issues(
    skill: Skill,
    bound_fact_ids: Sequence[str],
    pointer: str,
) -> list[ContractIssue]:
    counts = Counter(bound_fact_ids)
    issues: list[ContractIssue] = []
    for fact in skill.output_contract.facts:
        if fact.required and counts[fact.fact_id] != 1:
            issues.append(
                _issue(
                    "REQUIRED_OUTPUT_BINDING_MISSING",
                    pointer,
                    f"Required fact '{fact.fact_id}' должен иметь ровно один exact binding.",
                )
            )
    return issues


@dataclass(frozen=True, slots=True)
class _FactProof:
    fact_id: str
    semantic_type: str
    value_type: str
    cardinality: str
    unit_dimension: str
    time_semantics: str
    identity_complete: bool
    required: bool
    nullable: bool


def _plan_coverage_issues(
    plan: PlannerOutput,
    catalog: dict[tuple[str, str], Skill],
) -> list[ContractIssue]:
    if not isinstance(plan.result, ExecuteResult):
        return []
    issues: list[ContractIssue] = []
    proofs_by_step: dict[str, dict[str, _FactProof]] = {}
    for step in plan.result.steps:
        if isinstance(step, SkillCall):
            skill = catalog.get((step.skill_id, step.skill_version))
            if skill is not None:
                proofs_by_step[step.step_id] = _skill_call_proofs(step, skill)
            continue
        if isinstance(step, (FilterOperator, RankOperator)):
            proofs_by_step[step.step_id] = dict(
                proofs_by_step.get(step.input_step_id, {})
            )
            continue
        if isinstance(step, JoinOperator):
            joined = dict(proofs_by_step.get(step.left_step_id, {}))
            joined.update(proofs_by_step.get(step.right_step_id, {}))
            proofs_by_step[step.step_id] = joined
            continue
        if isinstance(step, CountOperator):
            input_proofs = proofs_by_step.get(step.input_step_id, {})
            if all(
                fact_id in input_proofs for fact_id in step.distinct_by_fact_ids
            ):
                input_time = {
                    proof.time_semantics
                    for proof in input_proofs.values()
                    if proof.time_semantics != "none"
                }
                proofs_by_step[step.step_id] = {
                    step.result_fact_id: _FactProof(
                        fact_id=step.result_fact_id,
                        semantic_type="measure.count",
                        value_type=FactValueType.INTEGER.value,
                        cardinality="aggregate",
                        unit_dimension="none",
                        time_semantics=(
                            input_time.pop() if len(input_time) == 1 else "none"
                        ),
                        identity_complete=True,
                        required=True,
                        nullable=False,
                    )
                }
            continue
        if isinstance(step, AggregateOperator):
            input_proofs = proofs_by_step.get(step.input_step_id, {})
            measure = input_proofs.get(step.measure_fact_id)
            if measure is not None:
                identity_complete = all(
                    fact_id in input_proofs for fact_id in step.group_by_fact_ids
                )
                proofs_by_step[step.step_id] = {
                    step.result_fact_id: _FactProof(
                        fact_id=step.result_fact_id,
                        semantic_type=measure.semantic_type,
                        value_type=measure.value_type,
                        cardinality=(
                            "many" if step.group_by_fact_ids else "aggregate"
                        ),
                        unit_dimension=measure.unit_dimension,
                        time_semantics=measure.time_semantics,
                        identity_complete=identity_complete,
                        required=True,
                        nullable=False,
                    )
                }
            continue
        if isinstance(step, CalculateOperator):
            input_proofs = proofs_by_step.get(step.input_step_id, {})
            input_time = {
                proof.time_semantics
                for proof in input_proofs.values()
                if proof.time_semantics != "none"
            }
            proofs_by_step[step.step_id] = {
                step.result_fact_id: _FactProof(
                    fact_id=step.result_fact_id,
                    semantic_type=step.result_semantic_type,
                    value_type=FactValueType.DECIMAL.value,
                    cardinality="aggregate",
                    unit_dimension="none",
                    time_semantics=(input_time.pop() if len(input_time) == 1 else "none"),
                    identity_complete=True,
                    required=True,
                    nullable=False,
                )
            }
            continue
        if isinstance(step, NormalizePeriodOperator):
            proofs_by_step[step.step_id] = {
                step.result_fact_id: _FactProof(
                    fact_id=step.result_fact_id,
                    semantic_type="time.period",
                    value_type=FactValueType.PERIOD.value,
                    cardinality="exactly_one",
                    unit_dimension="none",
                    time_semantics="period",
                    identity_complete=True,
                    required=True,
                    nullable=False,
                )
            }

    all_proofs = [
        proof for step_proofs in proofs_by_step.values() for proof in step_proofs.values()
    ]
    final_proofs: list[_FactProof] = []
    for index, output in enumerate(plan.result.final_outputs):
        proof = proofs_by_step.get(output.step_id, {}).get(output.fact_id)
        if proof is None:
            issues.append(
                _issue(
                    "PLAN_FINAL_FACT_UNKNOWN",
                    f"/result/final_outputs/{index}/fact_id",
                    "Final output fact не доказан outputs указанного step.",
                )
            )
        else:
            final_proofs.append(proof)

    for index, requirement in enumerate(plan.interpretation.required_facts):
        pointer = f"/interpretation/required_facts/{index}"
        compatible = [
            proof for proof in all_proofs if _proof_matches(requirement, proof)
        ]
        if not compatible:
            issues.append(_requirement_mismatch_issue(requirement, all_proofs, pointer))
            continue
        if requirement.required and not any(
            _proof_matches(requirement, proof) for proof in final_proofs
        ):
            issues.append(
                _issue(
                    "PLAN_FACT_REQUIREMENT_UNMET",
                    pointer,
                    "Mandatory FactRequirement не покрыт ни одним final output.",
                )
            )
    return issues


def _skill_call_proofs(step: SkillCall, skill: Skill) -> dict[str, _FactProof]:
    requested = set(step.required_output_fact_ids)
    contract = skill.output_contract
    definitions = {fact.fact_id: fact for fact in contract.facts}
    identity_ids = set(contract.row_identity_fact_ids or ())
    identity_complete = contract.cardinality != "many" or (
        bool(identity_ids)
        and identity_ids <= requested
        and all(
            definitions.get(fact_id) is not None
            and definitions[fact_id].role in {"entity", "dimension"}
            for fact_id in identity_ids
        )
    )
    time_semantics = _contract_time_semantics(skill, requested)
    return {
        fact.fact_id: _FactProof(
            fact_id=fact.fact_id,
            semantic_type=fact.semantic_type,
            value_type=fact.value_type.value,
            cardinality=contract.cardinality,
            unit_dimension=_fact_unit_dimension(fact),
            time_semantics=time_semantics,
            identity_complete=identity_complete,
            required=fact.required,
            nullable=fact.nullable,
        )
        for fact in contract.facts
        if fact.fact_id in requested
    }


def _contract_time_semantics(skill: Skill, requested: set[str]) -> str:
    semantics: set[str] = set()
    for fact in skill.output_contract.facts:
        if fact.fact_id not in requested or fact.role != "time":
            continue
        if fact.value_type is FactValueType.PERIOD or fact.semantic_type.endswith(
            ".period"
        ):
            semantics.add("period")
        elif fact.value_type in {FactValueType.DATE, FactValueType.DATETIME}:
            semantics.add("moment")
    return semantics.pop() if len(semantics) == 1 else "none"


def _fact_unit_dimension(fact: FactDefinition) -> str:
    if fact.value_type is FactValueType.PERCENTAGE:
        return "percentage"
    if fact.unit_contract.mode == "not_applicable":
        return "none"
    if fact.value_type is FactValueType.MONEY:
        return "currency"
    if fact.value_type is FactValueType.QUANTITY:
        return "quantity_unit"
    return "none"


def _proof_matches(requirement: FactRequirement, proof: _FactProof) -> bool:
    if proof.semantic_type != requirement.semantic_type:
        return False
    if proof.value_type != requirement.value_type.value:
        return False
    cardinalities = {
        "one": {"exactly_one"},
        "zero_or_one": {"exactly_one", "zero_or_one"},
        "many": {"many"},
        "aggregate": {"aggregate"},
    }
    if proof.cardinality not in cardinalities[requirement.cardinality]:
        return False
    if requirement.unit_dimension is not None and (
        proof.unit_dimension != requirement.unit_dimension
    ):
        return False
    if requirement.time_semantics is not None and (
        proof.time_semantics != requirement.time_semantics
    ):
        return False
    if requirement.cardinality == "many" and not proof.identity_complete:
        return False
    return not (requirement.required and (not proof.required or proof.nullable))


def _requirement_mismatch_issue(
    requirement: FactRequirement,
    proofs: Sequence[_FactProof],
    pointer: str,
) -> ContractIssue:
    if not proofs:
        code = "PLAN_FACT_REQUIREMENT_UNMET"
        message = "Ни один step не предоставляет fact для FactRequirement."
        return _issue(code, pointer, message)

    matching = [
        proof for proof in proofs if proof.semantic_type == requirement.semantic_type
    ]
    if not matching:
        code = "PLAN_FACT_SEMANTIC_TYPE_MISMATCH"
        message = "Нет output с exact semantic_type FactRequirement."
        return _issue(code, pointer, message)

    matching = [
        proof for proof in matching if proof.value_type == requirement.value_type.value
    ]
    if not matching:
        code = "PLAN_FACT_VALUE_TYPE_MISMATCH"
        message = "Output value_type несовместим с FactRequirement."
        return _issue(code, pointer, message)

    allowed_cardinalities = {
            "one": {"exactly_one"},
            "zero_or_one": {"exactly_one", "zero_or_one"},
            "many": {"many"},
            "aggregate": {"aggregate"},
    }[requirement.cardinality]
    matching = [
        proof for proof in matching if proof.cardinality in allowed_cardinalities
    ]
    if not matching:
        code = "PLAN_FACT_CARDINALITY_MISMATCH"
        message = "Output cardinality несовместима с FactRequirement."
        return _issue(code, pointer, message)

    if requirement.unit_dimension is not None:
        matching = [
            proof
            for proof in matching
            if proof.unit_dimension == requirement.unit_dimension
        ]
    if not matching:
        code = "PLAN_FACT_UNIT_MISMATCH"
        message = "Output unit dimension несовместима с FactRequirement."
        return _issue(code, pointer, message)

    if requirement.time_semantics is not None:
        matching = [
            proof
            for proof in matching
            if proof.time_semantics == requirement.time_semantics
        ]
    if not matching:
        code = "PLAN_FACT_TIME_MISMATCH"
        message = "Output time semantics несовместима с FactRequirement."
        return _issue(code, pointer, message)

    if requirement.cardinality == "many":
        matching = [proof for proof in matching if proof.identity_complete]
    if not matching:
        code = "PLAN_FACT_IDENTITY_MISMATCH"
        message = "Many-output не доказывает полную row identity/dimensions."
        return _issue(code, pointer, message)

    matching = [
        proof
        for proof in matching
        if not requirement.required or (proof.required and not proof.nullable)
    ]
    if not matching:
        code = "PLAN_FACT_NULLABILITY_MISMATCH"
        message = "Required FactRequirement покрыт optional/nullable output."
        return _issue(code, pointer, message)
    return _issue(
        "PLAN_FACT_REQUIREMENT_UNMET",
        pointer,
        "FactRequirement не имеет полного typed coverage proof.",
    )


def _encoding_matches_parameter(encoding: str, parameter: Parameter) -> bool:
    allowed: dict[ParameterValueType, set[str]] = {
        ParameterValueType.STRING: {"string", "like_contains"},
        ParameterValueType.NORMALIZED_TEXT: {"string", "like_contains"},
        ParameterValueType.BOOLEAN: {"boolean"},
        ParameterValueType.INTEGER: {"integer"},
        ParameterValueType.DECIMAL: {"decimal"},
        ParameterValueType.DATE: {"date"},
        ParameterValueType.DATETIME: {"datetime"},
        ParameterValueType.PERIOD: {"period_start", "period_end_exclusive"},
        ParameterValueType.ENUM: {"string"},
        ParameterValueType.ENTITY_REF: {"object_ref"},
        ParameterValueType.ENTITY_REF_LIST: {"object_ref_list"},
        ParameterValueType.PAGINATION: set(),
    }
    return encoding in allowed[parameter.value_type]


def _converter_matches_fact(converter: str, fact: FactDefinition) -> bool:
    allowed: dict[str, set[FactValueType]] = {
        "identity": {FactValueType.STRING},
        "string": {FactValueType.STRING},
        "integer": {FactValueType.INTEGER},
        "decimal": {
            FactValueType.DECIMAL,
            FactValueType.MONEY,
            FactValueType.QUANTITY,
            FactValueType.PERCENTAGE,
        },
        "boolean": {FactValueType.BOOLEAN},
        "date": {FactValueType.DATE},
        "datetime": {FactValueType.DATETIME},
        "object_ref": {FactValueType.ENTITY_REF},
    }
    return fact.value_type in allowed[converter]


def _target_compatibility_issues(
    package: SkillPackage, skill: Skill, index: int
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    pointer = f"/skills/{index}/compatibility"
    compatibility = skill.compatibility
    if (
        compatibility.configuration_id != package.target.configuration_id
        or compatibility.configuration_name != package.target.configuration_name
    ):
        issues.append(
            _issue(
                "TARGET_COMPATIBILITY_MISMATCH",
                pointer,
                "Skill configuration target не совпадает с package target.",
            )
        )
    target = tuple(int(part) for part in package.target.release.split("."))
    minimum = tuple(
        int(part) for part in compatibility.release_range.minimum.split(".")
    )
    maximum = tuple(
        int(part) for part in compatibility.release_range.maximum.split(".")
    )
    in_range = minimum <= target <= maximum
    if target == minimum and not compatibility.release_range.include_minimum:
        in_range = False
    if target == maximum and not compatibility.release_range.include_maximum:
        in_range = False
    if (
        not in_range
        or package.target.compatibility_mode not in compatibility.compatibility_modes
    ):
        issues.append(
            _issue(
                "TARGET_COMPATIBILITY_MISMATCH",
                pointer + "/release_range",
                "Package release/compatibility mode не входит в skill compatibility.",
            )
        )
    return issues


def _version_matches(version: str, specification: str) -> bool:
    parsed = Version(version)
    try:
        return NpmSpec(specification).match(parsed)
    except ValueError:
        normalized = ",".join(specification.split())
        try:
            return SimpleSpec(normalized).match(parsed)
        except ValueError:
            return False


Node = TypeVar("Node", bound=object)


def _find_cycle(graph: dict[Node, set[Node]]) -> list[Node]:
    visiting: set[Node] = set()
    visited: set[Node] = set()
    stack: list[Node] = []

    def visit(node: Node) -> list[Node]:
        if node in visiting:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for dependency in graph.get(node, set()):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def _step_dependencies(step: object) -> set[str]:
    dependencies = {
        binding.step_id
        for binding in _step_bindings(step)
        if isinstance(binding, StepBinding)
    }
    if isinstance(
        step,
        (
            CountOperator,
            AggregateOperator,
            RankOperator,
            FilterOperator,
            CalculateOperator,
        ),
    ):
        dependencies.add(step.input_step_id)
    elif isinstance(step, JoinOperator):
        dependencies.update({step.left_step_id, step.right_step_id})
    return dependencies


def _step_bindings(step: object) -> tuple[Binding, ...]:
    if isinstance(step, SkillCall):
        return tuple(argument.binding for argument in step.arguments)
    if isinstance(step, NormalizePeriodOperator):
        return (step.expression,)
    if isinstance(step, RankOperator):
        return (step.limit,)
    if isinstance(step, FilterOperator) and step.operand is not None:
        return (step.operand,)
    return ()


def _fact_value_issues(fact: Fact, index: int) -> list[ContractIssue]:
    value = fact.value
    valid = {
        FactValueType.STRING: isinstance(value, str),
        FactValueType.INTEGER: type(value) is int,
        FactValueType.DECIMAL: type(value) is float,
        FactValueType.BOOLEAN: type(value) is bool,
        FactValueType.DATE: isinstance(value, str),
        FactValueType.DATETIME: isinstance(value, str),
        FactValueType.PERIOD: isinstance(value, Period),
        FactValueType.ENTITY_REF: isinstance(value, EntityRef),
        FactValueType.MONEY: type(value) in {int, float},
        FactValueType.QUANTITY: type(value) in {int, float},
        FactValueType.PERCENTAGE: type(value) in {int, float},
        FactValueType.DOCUMENT_FRAGMENT: isinstance(value, DocumentFragment),
        FactValueType.SOURCE_CITATION: isinstance(value, CitationValue),
    }
    if valid[fact.value_type]:
        return []
    return [
        _issue(
            "FACT_VALUE_TYPE_MISMATCH",
            f"/facts/{index}/value",
            "Fact value не соответствует declared value_type.",
        )
    ]


def _entity_semantics_match(semantic_type: str, object_type: str) -> bool:
    if object_type.startswith("ДокументСсылка."):
        return semantic_type.startswith("document.")
    if semantic_type.startswith("document."):
        return object_type.startswith("ДокументСсылка.")
    reference_roots = (
        "catalog.",
        "party.",
        "warehouse.",
        "inventory.",
        "price.",
        "finance.",
    )
    if semantic_type.startswith(reference_roots):
        return object_type.startswith(("СправочникСсылка.", "ПеречислениеСсылка."))
    return True


def _skill_operation_pair(operation_ref: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"skill://([^/]+)/([^/]+)", operation_ref)
    return (match.group(1), match.group(2)) if match else None


def _accepted_entity_types(skill: Skill | None, fact: Fact) -> set[str]:
    if skill is None or not isinstance(skill.operation, DataQueryOperation):
        return set()
    return {
        accepted_type
        for binding in skill.operation.column_bindings
        if binding.fact_id == fact.fact_id
        and binding.column == fact.source_locator.reference
        for accepted_type in binding.accepted_mcp_types
    }


def _strip_query_comments(text: str) -> str:
    result = list(text)
    index = 0
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if text.startswith("//", index) or text.startswith("--", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
            result[index:end] = " " * (end - index)
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
            result[index:end] = " " * (end - index)
            index = end
            continue
        index += 1
    return "".join(result)


def _query_code(text: str) -> str:
    uncommented = _strip_query_comments(text)
    result = list(uncommented)
    index = 0
    quote: str | None = None
    while index < len(uncommented):
        char = uncommented[index]
        if quote is None and char in {'"', "'"}:
            quote = char
            result[index] = " "
        elif quote is not None:
            result[index] = " "
            if char == quote:
                if index + 1 < len(uncommented) and uncommented[index + 1] == quote:
                    result[index + 1] = " "
                    index += 1
                else:
                    quote = None
        index += 1
    return "".join(result)


def _projection_aliases(code: str) -> list[str]:
    select_match = re.search(r"\b(?:ВЫБРАТЬ|SELECT)\b", code, re.IGNORECASE)
    if select_match is None:
        return []
    start = select_match.end()
    end = _top_level_keyword(code, ("ИЗ", "FROM"), start)
    projection = code[start : end if end is not None else len(code)]
    aliases: list[str] = []
    for expression in _split_top_level(projection, ","):
        tokens = _top_level_words(expression)
        for index in range(len(tokens) - 1, -1, -1):
            if tokens[index].upper() in {"КАК", "AS"} and index + 1 < len(tokens):
                aliases.append(tokens[index + 1])
                break
    return aliases


def _top_level_keyword(code: str, words: tuple[str, ...], start: int) -> int | None:
    depth = 0
    for match in _WORD_RE.finditer(code, start):
        depth = _depth_before(code, start, match.start(), depth)
        start = match.end()
        if depth == 0 and match.group(0).upper() in words:
            return match.start()
    return None


def _split_top_level(text: str, delimiter: str) -> list[str]:
    depth = 0
    start = 0
    parts: list[str] = []
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == delimiter and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _top_level_words(text: str) -> list[str]:
    words: list[str] = []
    depth = 0
    cursor = 0
    for match in _WORD_RE.finditer(text):
        depth = _depth_before(text, cursor, match.start(), depth)
        cursor = match.end()
        if depth == 0:
            words.append(match.group(0))
    return words


def _depth_before(text: str, start: int, end: int, depth: int) -> int:
    for char in text[start:end]:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
    return depth
