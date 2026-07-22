"""Semantic validation that JSON Schema intentionally cannot express."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, TypeVar

from semantic_version import NpmSpec, SimpleSpec, Version

from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.errors import ContractIssue, raise_for_issues
from chatbot1c.contracts.query import (
    ParsedQuery,
    escaped_like_parameters,
    inspect_keyset_query_contract,
    inspect_query_contract,
)
from chatbot1c.domain.evidence import (
    CitationValue,
    ContextExport,
    DocumentFragment,
    EvidenceBundle,
    Fact,
    FilterRetentionProof,
    SelectionProof,
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
    ConfirmedFilterContextPolicy,
    DataQueryOperation,
    DocumentationFixture,
    DocumentationRetrievalOperation,
    FactDefinition,
    FactEqualsParameterConstraint,
    FactValueType,
    KeysetPagination,
    McpFixture,
    Parameter,
    ParameterValueType,
    SelectedOnlyContextPolicy,
    Skill,
    UnitFromFact,
    collection_scope_for_skill,
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
_RESOLVER_SAFE_LABEL_FACT_TYPES = frozenset(
    {
        FactValueType.STRING,
        FactValueType.INTEGER,
        FactValueType.DECIMAL,
        FactValueType.BOOLEAN,
        FactValueType.DATE,
        FactValueType.DATETIME,
        FactValueType.ENUM,
        FactValueType.MONEY,
        FactValueType.QUANTITY,
        FactValueType.PERCENTAGE,
    }
)

T = TypeVar("T")

_ERROR_CODE_ALIASES: dict[str, tuple[str, ...]] = {
    "DISAGREEMENT_CITATIONS_NOT_DISTINCT": (
        "DISAGREEMENT_DISTINCT_CITATIONS_REQUIRED",
    ),
    "DISAGREEMENT_FACT_UNKNOWN": ("DISAGREEMENT_FACT_REFERENCE_MISSING",),
    "DISAGREEMENT_SUBJECT_MISMATCH": ("DISAGREEMENT_SUBJECT_FACT_MISMATCH",),
    "PACKAGE_LOCK_MISSING": ("DEPENDENCY_LOCK_MISSING",),
    "PACKAGE_LOCK_ORPHAN": ("DEPENDENCY_LOCK_ORPHAN",),
    "QUERY_FINAL_PROJECTION_BINDING_MISMATCH": ("QUERY_BINDING_ALIAS_MISSING",),
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
        for index, parameter in enumerate(skill.parameters):
            is_entity = parameter.value_type in {
                ParameterValueType.ENTITY_REF,
                ParameterValueType.ENTITY_REF_LIST,
            }
            if is_entity and parameter.semantic_type not in (
                parameter.entity_types or ()
            ):
                issues.append(
                    _issue(
                        "PARAMETER_ENTITY_TYPE_NOT_ALLOWED",
                        f"{prefix}/parameters/{index}/entity_types",
                        (
                            "semantic_type entity parameter должен входить в "
                            "закрытый entity_types allowlist."
                        ),
                    )
                )
            if is_entity and set(parameter.allowed_sources) - {
                "session_context",
                "previous_step",
            }:
                issues.append(
                    _issue(
                        "ENTITY_REF_SOURCE_UNPROVEN",
                        f"{prefix}/parameters/{index}/allowed_sources",
                        (
                            "Entity parameter разрешает только provenance-bearing "
                            "session_context/previous_step."
                        ),
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
        output_types = {fact.semantic_type for fact in skill.output_contract.facts}
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
        issues.extend(self._result_constraint_issues(skill, parameters, facts, prefix))
        issues.extend(self._runtime_contract_issues(skill, prefix))
        issues.extend(self._fixture_issues(skill, parameters, facts, prefix))
        if skill.schema_version == "1.1.0":
            issues.extend(self._context_contract_issues(skill, facts, prefix))

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

    def _context_contract_issues(
        self,
        skill: Skill,
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        contract = skill.output_contract
        policies = contract.context_export_policy or ()
        policy_keys: set[tuple[str, str]] = set()
        for index, policy in enumerate(policies):
            pointer = f"{prefix}/output_contract/context_export_policy/{index}"
            key = (policy.fact_id, policy.slot_key)
            if key in policy_keys:
                issues.append(
                    _issue(
                        "CONTEXT_EXPORT_POLICY_INVALID",
                        pointer,
                        "Один fact/slot context policy объявлен повторно.",
                    )
                )
            policy_keys.add(key)
            fact = facts.get(policy.fact_id)
            if fact is None:
                issues.append(
                    _issue(
                        "CONTEXT_EXPORT_POLICY_INVALID",
                        pointer + "/fact_id",
                        "Context policy ссылается на неизвестный output fact.",
                    )
                )
                continue
            if isinstance(policy, SelectedOnlyContextPolicy):
                if (
                    fact.value_type is not FactValueType.ENTITY_REF
                    or fact.role != "entity"
                    or fact.nullable
                ):
                    issues.append(
                        _issue(
                            "CONTEXT_EXPORT_MODE_INVALID",
                            pointer,
                            "selected_only разрешен только для non-null entity_ref fact.",
                        )
                    )
            elif isinstance(policy, ConfirmedFilterContextPolicy):
                if fact.value_type is FactValueType.ENTITY_REF:
                    issues.append(
                        _issue(
                            "CONTEXT_EXPORT_MODE_INVALID",
                            pointer,
                            "confirmed_filter не может экспортировать entity_ref fact.",
                        )
                    )
                    continue
                if (
                    fact.semantic_type != policy.semantic_type
                    or fact.value_type.value != policy.value_type
                    or fact.nullable
                    or (
                        fact.value_type is FactValueType.ENUM
                        and not fact.allowed_values
                    )
                ):
                    issues.append(
                        _issue(
                            "CONTEXT_FILTER_CONTRACT_INVALID",
                            pointer,
                            "confirmed_filter не совпадает с exact non-entity fact contract.",
                        )
                    )
            lifetime = policy.lifetime
            if lifetime.mode == "until":
                expires = facts.get(lifetime.expires_at_fact_id)
                if (
                    expires is None
                    or expires.value_type is not FactValueType.DATETIME
                    or expires.nullable
                ):
                    issues.append(
                        _issue(
                            "CONTEXT_EXPORT_POLICY_INVALID",
                            pointer + "/lifetime/expires_at_fact_id",
                            "until lifetime требует non-null datetime output fact.",
                        )
                    )

        resolution = contract.resolution
        if resolution is None:
            if any(isinstance(item, SelectedOnlyContextPolicy) for item in policies):
                issues.append(
                    _issue(
                        "CONTEXT_EXPORT_POLICY_INVALID",
                        f"{prefix}/output_contract/context_export_policy",
                        "selected_only требует typed resolver declaration.",
                    )
                )
            return issues

        pointer = f"{prefix}/output_contract/resolution"
        if not isinstance(skill.operation, DataQueryOperation):
            issues.append(
                _issue(
                    "RESOLVER_CONTRACT_INVALID",
                    pointer,
                    "Typed resolver должен быть data-query skill.",
                )
            )
            return issues
        if contract.cardinality != "many":
            issues.append(
                _issue(
                    "RESOLVER_CONTRACT_INVALID",
                    pointer,
                    "Typed resolver producer обязан иметь cardinality=many.",
                )
            )
        identity = facts.get(resolution.identity_fact_id)
        if (
            identity is None
            or identity.value_type is not FactValueType.ENTITY_REF
            or identity.role != "entity"
            or not identity.required
            or identity.nullable
        ):
            issues.append(
                _issue(
                    "RESOLVER_IDENTITY_FACT_INVALID",
                    pointer + "/identity_fact_id",
                    "Resolver identity обязан быть required non-null entity_ref fact.",
                )
            )
        bindings = [
            item
            for item in skill.operation.column_bindings
            if item.fact_id == resolution.identity_fact_id
            and item.converter == "object_ref"
            and item.accepted_mcp_types
        ]
        if len(bindings) != 1:
            issues.append(
                _issue(
                    "RESOLVER_PHYSICAL_PROOF_MISSING",
                    pointer + "/identity_fact_id",
                    "Resolver identity требует один exact object_ref column binding.",
                )
            )
        if resolution.identity_fact_id not in (contract.row_identity_fact_ids or ()):
            issues.append(
                _issue(
                    "RESOLVER_ROW_IDENTITY_INVALID",
                    pointer + "/identity_fact_id",
                    "Resolver identity должна входить в row_identity_fact_ids.",
                )
            )
        for index, fact_id in enumerate(resolution.candidate_label_fact_ids):
            fact = facts.get(fact_id)
            if (
                fact is None
                or fact.nullable
                or not fact.required
                or fact.value_type not in _RESOLVER_SAFE_LABEL_FACT_TYPES
            ):
                issues.append(
                    _issue(
                        "RESOLVER_PROOF_FACT_INVALID",
                        f"{pointer}/candidate_label_fact_ids/{index}",
                        (
                            "Resolver label требует known required non-null "
                            "safe scalar fact."
                        ),
                    )
                )
        for index, fact_id in enumerate(resolution.role_proof_fact_ids):
            fact = facts.get(fact_id)
            exact_boolean = (
                fact is not None and fact.value_type is FactValueType.BOOLEAN
            )
            exact_enum = (
                fact is not None
                and fact.value_type is FactValueType.ENUM
                and len(fact.allowed_values or ()) == 1
            )
            if (
                fact is None
                or fact.nullable
                or not fact.required
                or not (exact_boolean or exact_enum)
            ):
                issues.append(
                    _issue(
                        "RESOLVER_ROLE_PROOF_INVALID",
                        f"{pointer}/role_proof_fact_ids/{index}",
                        (
                            "Role proof требует required non-null boolean fact "
                            "(истина означает соответствие) или enum fact с одним "
                            "exact allowed_values."
                        ),
                    )
                )
        selected = [
            item
            for item in policies
            if isinstance(item, SelectedOnlyContextPolicy)
            and item.fact_id == resolution.identity_fact_id
            and item.slot_key == resolution.default_slot_key
        ]
        if len(selected) != 1:
            issues.append(
                _issue(
                    "CONTEXT_EXPORT_POLICY_INVALID",
                    f"{prefix}/output_contract/context_export_policy",
                    "Resolver identity требует одну matching selected_only policy.",
                )
            )
        return issues

    def _result_constraint_issues(
        self,
        skill: Skill,
        parameters: dict[str, Parameter],
        facts: dict[str, FactDefinition],
        prefix: str,
    ) -> list[ContractIssue]:
        issues: list[ContractIssue] = []
        seen: set[tuple[str, str]] = set()
        bound_fact_ids = (
            {binding.fact_id for binding in skill.operation.column_bindings}
            if isinstance(skill.operation, DataQueryOperation)
            else set()
        )
        for index, constraint in enumerate(skill.result_constraints):
            pointer = f"{prefix}/result_constraints/{index}"
            key = (constraint.fact_id, constraint.parameter)
            if key in seen:
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_DUPLICATE",
                        pointer,
                        "Ограничение результата объявлено повторно.",
                    )
                )
            seen.add(key)
            fact = facts.get(constraint.fact_id)
            parameter = parameters.get(constraint.parameter)
            if fact is None:
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_FACT_MISSING",
                        pointer + "/fact_id",
                        "Ограничение ссылается на необъявленный output fact.",
                    )
                )
            if parameter is None:
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_PARAMETER_MISSING",
                        pointer + "/parameter",
                        "Ограничение ссылается на необъявленный parameter.",
                    )
                )
            if fact is not None and fact.value_type is not FactValueType.ENTITY_REF:
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_FACT_TYPE_MISMATCH",
                        pointer + "/fact_id",
                        "Result entity constraint требует entity_ref output fact.",
                    )
                )
            expected_parameter_type = (
                ParameterValueType.ENTITY_REF
                if isinstance(constraint, FactEqualsParameterConstraint)
                else ParameterValueType.ENTITY_REF_LIST
            )
            if (
                parameter is not None
                and parameter.value_type is not expected_parameter_type
            ):
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_PARAMETER_TYPE_MISMATCH",
                        pointer + "/parameter",
                        (
                            "fact_equals_parameter требует entity_ref parameter."
                            if isinstance(constraint, FactEqualsParameterConstraint)
                            else "fact_in_parameter требует entity_ref_list parameter."
                        ),
                    )
                )
            if (
                fact is not None
                and parameter is not None
                and fact.semantic_type != parameter.semantic_type
            ):
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_SEMANTIC_TYPE_MISMATCH",
                        pointer,
                        "Output fact и parameter должны иметь одинаковый semantic type.",
                    )
                )
            if fact is not None and fact.fact_id not in bound_fact_ids:
                issues.append(
                    _issue(
                        "RESULT_CONSTRAINT_OUTPUT_BINDING_MISSING",
                        pointer + "/fact_id",
                        "Ограниченный fact должен иметь exact output binding.",
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
            parameter = parameters.get(binding.parameter)
            if parameter is None:
                issues.append(
                    _issue(
                        "PARAMETER_BINDING_TARGET_MISSING",
                        pointer + "/parameter",
                        "Query binding ссылается на необъявленный parameter.",
                    )
                )
            else:
                if not _encoding_matches_parameter(binding.encoding, parameter):
                    issues.append(
                        _issue(
                            "PARAMETER_BINDING_TYPE_MISMATCH",
                            pointer + "/encoding",
                            "Encoding несовместим с parameter value_type.",
                        )
                    )
            prior_bindings = [
                item
                for item in operation.parameter_bindings[:index]
                if item.parameter == binding.parameter
            ]
            period_pair = (
                parameter is not None
                and parameter.value_type is ParameterValueType.PERIOD
                and len(prior_bindings) == 1
                and {
                    prior_bindings[0].encoding,
                    binding.encoding,
                }
                == {"period_start", "period_end_exclusive"}
            )
            if binding.parameter in binding_parameters and not period_pair:
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
                allowed_query_parameters.add(cursor_binding.query_parameter.casefold())
        if query_contract.parsed is not None:
            used_query_parameters = {
                name.casefold() for name in query_contract.parsed.parameters
            }
            escaped_like = escaped_like_parameters(query_contract.parsed)
            for index, binding in enumerate(operation.parameter_bindings):
                if (
                    binding.encoding == "like_contains"
                    and binding.query_parameter.casefold() not in escaped_like
                ):
                    issues.append(
                        _issue(
                            "LIKE_CONTAINS_ESCAPE_MISSING",
                            (f"{prefix}/operation/parameter_bindings/{index}/encoding"),
                            (
                                "like_contains требует predicate "
                                'ПОДОБНО &Параметр СПЕЦСИМВОЛ "~".'
                            ),
                        )
                    )
            for name in sorted(used_query_parameters - allowed_query_parameters):
                issues.append(
                    _issue(
                        "QUERY_PARAMETER_UNDECLARED",
                        text_pointer,
                        f"Query parameter '&{name}' не имеет exact binding.",
                    )
                )
            for name in sorted(set(query_names) - used_query_parameters):
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
            pagination = operation.pagination
            sort_fact_ids = [item.fact_id for item in pagination.sort]
            cursor_fact_ids = [item.fact_id for item in pagination.cursor_bindings]
            if len(set(sort_fact_ids)) != len(sort_fact_ids):
                issues.append(
                    _issue(
                        "PAGINATION_SORT_FACT_DUPLICATE",
                        f"{prefix}/operation/pagination/sort",
                        "Keyset sort содержит повторяющийся fact_id.",
                    )
                )
            if cursor_fact_ids != sort_fact_ids:
                issues.append(
                    _issue(
                        "PAGINATION_CURSOR_BIJECTION_MISMATCH",
                        f"{prefix}/operation/pagination/cursor_bindings",
                        "Cursor bindings должны exact совпадать с sort по fact_id и порядку.",
                    )
                )

            ordinary_query_names = set(query_names)
            cursor_query_names: set[str] = set()
            has_cursor_name = pagination.has_cursor_query_parameter.casefold()
            if has_cursor_name in ordinary_query_names:
                issues.append(
                    _issue(
                        "PAGINATION_PARAMETER_COLLISION",
                        (f"{prefix}/operation/pagination/has_cursor_query_parameter"),
                        "Has-cursor parameter конфликтует с обычным query binding.",
                    )
                )
            for index, sort in enumerate(pagination.sort):
                fact = facts.get(sort.fact_id)
                if fact is None:
                    issues.append(
                        _issue(
                            "PAGINATION_FACT_MISSING",
                            f"{prefix}/operation/pagination/sort/{index}/fact_id",
                            "Keyset sort ссылается на необъявленный fact_id.",
                        )
                    )
                    continue
                if not fact.required:
                    issues.append(
                        _issue(
                            "PAGINATION_SORT_COORDINATE_INVALID",
                            f"{prefix}/operation/pagination/sort/{index}/fact_id",
                            "Keyset sort может использовать только required fact.",
                        )
                    )
                if fact.nullable:
                    issues.append(
                        _issue(
                            "PAGINATION_SORT_COORDINATE_INVALID",
                            f"{prefix}/operation/pagination/sort/{index}/fact_id",
                            "Keyset sort не допускает nullable fact.",
                        )
                    )
            for index, cursor_binding in enumerate(pagination.cursor_bindings):
                fact = facts.get(cursor_binding.fact_id)
                if fact is None:
                    issues.append(
                        _issue(
                            "PAGINATION_FACT_MISSING",
                            f"{prefix}/operation/pagination/cursor_bindings/{index}/fact_id",
                            "Cursor binding ссылается на необъявленный fact_id.",
                        )
                    )
                elif not _cursor_encoding_matches_fact(cursor_binding.encoding, fact):
                    issues.append(
                        _issue(
                            "PAGINATION_CURSOR_ENCODING_MISMATCH",
                            f"{prefix}/operation/pagination/cursor_bindings/{index}/encoding",
                            "Cursor encoding несовместим с declared fact value_type.",
                        )
                    )
                normalized_name = cursor_binding.query_parameter.casefold()
                if normalized_name in cursor_query_names:
                    issues.append(
                        _issue(
                            "PAGINATION_CURSOR_PARAMETER_DUPLICATE",
                            (
                                f"{prefix}/operation/pagination/"
                                f"cursor_bindings/{index}/query_parameter"
                            ),
                            "Cursor query parameter должен быть уникальным.",
                        )
                    )
                if (
                    normalized_name == has_cursor_name
                    or normalized_name in ordinary_query_names
                ):
                    issues.append(
                        _issue(
                            "PAGINATION_PARAMETER_COLLISION",
                            (
                                f"{prefix}/operation/pagination/"
                                f"cursor_bindings/{index}/query_parameter"
                            ),
                            "Cursor query parameter конфликтует с другим binding.",
                        )
                    )
                cursor_query_names.add(normalized_name)

            identity_fact_ids = skill.output_contract.row_identity_fact_ids or ()
            identity_suffix = sort_fact_ids[-len(identity_fact_ids) :]
            if (
                not identity_fact_ids
                or len(identity_suffix) != len(identity_fact_ids)
                or set(identity_suffix) != set(identity_fact_ids)
            ):
                issues.append(
                    _issue(
                        "PAGINATION_IDENTITY_SUFFIX_MISMATCH",
                        f"{prefix}/operation/pagination/sort",
                        "Keyset sort должен оканчиваться полной immutable row identity.",
                    )
                )

            if query_contract.parsed is not None:
                used_query_parameters = {
                    name.casefold() for name in query_contract.parsed.parameters
                }
                pagination_names = {
                    has_cursor_name,
                    *cursor_query_names,
                }
                missing_pagination_names = sorted(
                    pagination_names - used_query_parameters
                )
                query_contract_unproven = bool(missing_pagination_names)
                if missing_pagination_names:
                    issues.append(
                        _issue(
                            "PAGINATION_QUERY_CONTRACT_UNPROVEN",
                            text_pointer,
                            "Не все pagination parameters присутствуют в final query.",
                        )
                    )
                column_binding_counts = Counter(
                    binding.fact_id for binding in operation.column_bindings
                )
                if any(
                    column_binding_counts[fact_id] != 1 for fact_id in sort_fact_ids
                ):
                    issues.append(
                        _issue(
                            "PAGINATION_FACT_MISSING",
                            f"{prefix}/operation/column_bindings",
                            "Каждый keyset sort fact требует ровно один column binding.",
                        )
                    )
                else:
                    aliases_by_fact = {
                        binding.fact_id: binding.column
                        for binding in operation.column_bindings
                    }
                    query_proof = inspect_keyset_query_contract(
                        query_contract.parsed,
                        projection_aliases=[
                            aliases_by_fact[fact_id] for fact_id in sort_fact_ids
                        ],
                        directions=[item.direction for item in pagination.sort],
                        guard_parameter=pagination.has_cursor_query_parameter,
                        cursor_parameters=[
                            item.query_parameter for item in pagination.cursor_bindings
                        ],
                    )
                    if query_proof.order == "mismatch":
                        issues.append(
                            _issue(
                                "PAGINATION_QUERY_ORDER_MISMATCH",
                                text_pointer,
                                "Final ORDER BY не совпадает с declared keyset sort.",
                            )
                        )
                    elif query_proof.order == "unproven":
                        query_contract_unproven = True
                    if query_proof.predicate == "mismatch":
                        issues.append(
                            _issue(
                                "PAGINATION_QUERY_PREDICATE_MISMATCH",
                                text_pointer,
                                "Cursor filter не равен guarded strict lexicographic predicate.",
                            )
                        )
                    elif query_proof.predicate == "unproven":
                        query_contract_unproven = True
                    if query_contract_unproven and not missing_pagination_names:
                        issues.append(
                            _issue(
                                "PAGINATION_QUERY_CONTRACT_UNPROVEN",
                                text_pointer,
                                "Parser не доказал полный keyset query contract.",
                            )
                        )
                if _has_static_top(query_contract.parsed):
                    issues.append(
                        _issue(
                            "PAGINATION_STATIC_TOP_FORBIDDEN",
                            text_pointer,
                            "Keyset query не может содержать static TOP/ПЕРВЫЕ.",
                        )
                    )
        if operation.pagination.strategy != "none":
            if operation.query_template.mcp_limit.maximum < 2:
                issues.append(
                    _issue(
                        "PAGINATION_PROBE_LIMIT_UNAVAILABLE",
                        f"{prefix}/operation/query_template/mcp_limit/maximum",
                        "Paged skill должен разрешать page_size+1 probe.",
                    )
                )
            if Version(skill.version) >= Version("1.1.0") and not isinstance(
                operation.pagination, KeysetPagination
            ):
                for index, invariant in enumerate(
                    operation.query_template.invariant_constants
                ):
                    if (
                        invariant.kind == "structural_integer"
                        and invariant.role == "top_limit"
                    ):
                        issues.append(
                            _issue(
                                "PAGINATION_STATIC_TOP_FORBIDDEN",
                                (
                                    f"{prefix}/operation/query_template/"
                                    f"invariant_constants/{index}"
                                ),
                                "Paged skill 1.1+ не может фиксировать TOP: probe управляется MCP limit.",
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
        embedded_ids: dict[str, int] = {}
        for index, skill in enumerate(package.skills):
            previous_id_index = embedded_ids.get(skill.skill_id)
            if previous_id_index is not None:
                issues.append(
                    _issue(
                        "PACKAGE_SKILL_ID_DUPLICATE",
                        f"/skills/{index}/skill_id",
                        (
                            "skill_id повторяется в package; один package может "
                            "содержать только одну version каждого skill_id."
                        ),
                    )
                )
            else:
                embedded_ids[skill.skill_id] = index
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
                    if _version_matches(candidate_pair[1], dependency.version_range)
                ]
                if not same_id:
                    issues.append(
                        _issue(
                            "SKILL_DEPENDENCY_MISSING",
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
                    selected_pair = max(locked_pairs, key=lambda item: Version(item[1]))

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
                    if step_index[dependency] >= index:
                        issues.append(
                            _issue(
                                "PLAN_STEP_ORDER_INVALID",
                                pointer,
                                (
                                    "Step dependency должна ссылаться только на "
                                    "более ранний step."
                                ),
                            )
                        )
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
        final_roots: set[str] = set()
        seen_final_refs: set[tuple[str, str]] = set()
        for index, output in enumerate(plan.result.final_outputs):
            final_ref = (output.step_id, output.fact_id)
            if final_ref in seen_final_refs:
                issues.append(
                    _issue(
                        "PLAN_FINAL_OUTPUT_AMBIGUOUS",
                        f"/result/final_outputs/{index}",
                        "Final output reference объявлен повторно.",
                    )
                )
            seen_final_refs.add(final_ref)
            if output.step_id not in step_index:
                issues.append(
                    _issue(
                        "PLAN_FINAL_OUTPUT_STEP_MISSING",
                        f"/result/final_outputs/{index}/step_id",
                        "Final output ссылается на отсутствующий step_id.",
                    )
                )
            else:
                final_roots.add(output.step_id)
        all_closure = _reverse_closure(final_roots, graph)
        for index, step in enumerate(steps):
            if step.step_id not in all_closure:
                issues.append(
                    _issue(
                        "PLAN_STEP_UNUSED",
                        f"/result/steps/{index}",
                        "Step не входит в transitive closure ни одного final output.",
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
        if (
            evidence.empty_reason is not None
            and evidence.outcome is not Outcome.SUCCESS_EMPTY
        ):
            issues.append(
                _issue(
                    "EMPTY_REASON_OUTCOME_MISMATCH",
                    "/empty_reason",
                    "Stable empty reason разрешен только для success_empty.",
                )
            )
        if (
            evidence.schema_version == "1.1.0"
            and evidence.outcome is Outcome.SUCCESS_EMPTY
            and evidence.empty_reason is None
        ):
            issues.append(
                _issue(
                    "EMPTY_REASON_REQUIRED",
                    "/empty_reason",
                    "Evidence 1.1 success_empty требует stable reason not_found/no_rows.",
                )
            )
        if evidence.coverage.sufficient and any(
            requirement.required and requirement.status is not CoverageStatus.COVERED
            for requirement in evidence.coverage.requirements
        ):
            issues.append(
                _issue(
                    "SUFFICIENT_COVERAGE_HAS_MISSING_REQUIREMENTS",
                    "/coverage/sufficient",
                    "coverage.sufficient=true несовместим с не покрытым requirement.",
                )
            )

        exported_fact_ids = {
            export.fact_instance_id for export in evidence.context_exports
        }
        context_producer_steps = {
            fact.step_id
            for fact in evidence.facts
            if fact.fact_instance_id in exported_fact_ids
        }
        if evidence.outcome is Outcome.SUCCESS_EMPTY and (
            any(fact.step_id not in context_producer_steps for fact in evidence.facts)
            or any(
                step.row_count > 0 and step.step_id not in context_producer_steps
                for step in evidence.steps
            )
        ):
            issues.append(
                _issue(
                    "ZERO_AGGREGATE_CLASSIFIED_AS_EMPTY",
                    "/outcome",
                    (
                        "success_empty не может содержать результатные строки или "
                        "факты; разрешены только доказательства подтвержденного "
                        "upstream context resolver/filter."
                    ),
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
        snapshot = {
            (skill.skill_id, skill.version): skill.digest
            for skill in evidence.catalog_snapshot.skills
        }
        for step_index, step in enumerate(evidence.steps):
            pair = _skill_operation_pair(step.operation_ref)
            if pair is None or pair not in catalog:
                continue
            if snapshot.get(pair) != catalog[pair].integrity.digest:
                issues.append(
                    _issue(
                        "EVIDENCE_SKILL_DIGEST_MISMATCH",
                        f"/steps/{step_index}/operation_ref",
                        (
                            "Evidence producer не совпадает с exact digest "
                            "pinned catalog snapshot."
                        ),
                    )
                )
        skill_by_step = {
            step.step_id: catalog[pair]
            for step in evidence.steps
            if (pair := _skill_operation_pair(step.operation_ref)) in catalog
            and snapshot.get(pair) == catalog[pair].integrity.digest
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
            producer = skill_by_step.get(fact.step_id)
            if fact.value_type is FactValueType.ENTITY_REF and isinstance(
                fact.value, EntityRef
            ):
                if producer is None:
                    issues.append(
                        _issue(
                            "ENTITY_BINDING_PROVENANCE_MISSING",
                            f"/facts/{index}/step_id",
                            (
                                "Entity fact требует доступный exact producer skill "
                                "из pinned catalog."
                            ),
                        )
                    )
                    continue
                definition = next(
                    (
                        item
                        for item in producer.output_contract.facts
                        if item.fact_id == fact.fact_id
                    ),
                    None,
                )
                exact_types = _accepted_entity_types(producer, fact)
                if (
                    definition is None
                    or definition.semantic_type != fact.semantic_type
                    or definition.value_type is not fact.value_type
                    or not exact_types
                    or fact.value.object_type not in exact_types
                ):
                    issues.append(
                        _issue(
                            "ENTITY_REF_SEMANTIC_TYPE_MISMATCH",
                            f"/facts/{index}/value/ТипОбъекта",
                            (
                                "Entity fact не совпадает с exact semantic/physical "
                                "signature producer column binding."
                            ),
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
                    if (
                        fact.value_type is not FactValueType.DOCUMENT_FRAGMENT
                        or not isinstance(fact.value, DocumentFragment)
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
                        and citation_fact.source_locator.kind == "documentation_chunk"
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
    collection_scope: Literal["visible_page", "complete_set"] = "complete_set"
    collection_obligation: Literal["fact", "visible_page", "complete_set"] = "fact"
    resolver_identity: bool = False


@dataclass(frozen=True, slots=True)
class FactCollectionScope:
    step_id: str
    fact_id: str
    collection_scope: Literal["visible_page", "complete_set"]


@dataclass(frozen=True, slots=True)
class StepCriticality:
    step_id: str
    criticality: Literal["required", "optional"]
    predecessors: tuple[str, ...]
    required_by_requirement_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequirementCoverageProof:
    requirement_id: str
    semantic_type: str
    required: bool
    final_step_id: str | None
    final_fact_id: str | None
    collection_obligation: Literal["fact", "visible_page", "complete_set"]
    collection_step_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanCoverageProof:
    steps: tuple[StepCriticality, ...]
    required_final_refs: frozenset[tuple[str, str]]
    fact_collection_scopes: tuple[FactCollectionScope, ...] = ()
    requirements: tuple[RequirementCoverageProof, ...] = ()

    @property
    def required_steps(self) -> frozenset[str]:
        return frozenset(
            step.step_id for step in self.steps if step.criticality == "required"
        )

    @property
    def optional_steps(self) -> frozenset[str]:
        return frozenset(
            step.step_id for step in self.steps if step.criticality == "optional"
        )


def build_plan_coverage_proof(
    plan: PlannerOutput, available_skills: Sequence[Skill]
) -> PlanCoverageProof:
    if not isinstance(plan.result, ExecuteResult):
        return PlanCoverageProof((), frozenset(), (), ())
    catalog = {(skill.skill_id, skill.version): skill for skill in available_skills}
    proofs_by_step = _coverage_proofs_by_step(plan, catalog)
    graph = {step.step_id: _step_dependencies(step) for step in plan.result.steps}
    required_roots: dict[str, set[str]] = defaultdict(set)
    required_final_refs: set[tuple[str, str]] = set()
    all_roots: set[str] = set()
    for output in plan.result.final_outputs:
        proof = proofs_by_step.get(output.step_id, {}).get(output.fact_id)
        if proof is None:
            continue
        matches = [
            requirement
            for requirement in plan.interpretation.required_facts
            if _proof_matches(requirement, proof)
        ]
        if len(matches) != 1:
            continue
        requirement = matches[0]
        all_roots.add(output.step_id)
        if requirement.required:
            required_roots[output.step_id].add(requirement.requirement_id)
            required_final_refs.add((output.step_id, output.fact_id))
    required_closure = _reverse_closure(required_roots, graph)
    all_closure = _reverse_closure(all_roots, graph)
    required_by: dict[str, set[str]] = defaultdict(set)
    for root, requirement_ids in required_roots.items():
        for step_id in _reverse_closure((root,), graph):
            required_by[step_id].update(requirement_ids)
    step_order = {step.step_id: index for index, step in enumerate(plan.result.steps)}
    requirement_proofs: list[RequirementCoverageProof] = []
    for requirement in plan.interpretation.required_facts:
        matching_finals = [
            output
            for output in plan.result.final_outputs
            if (proof := proofs_by_step.get(output.step_id, {}).get(output.fact_id))
            is not None
            and _proof_matches(requirement, proof)
        ]
        if len(matching_finals) != 1:
            requirement_proofs.append(
                RequirementCoverageProof(
                    requirement.requirement_id,
                    requirement.semantic_type,
                    requirement.required,
                    None,
                    None,
                    "fact",
                    (),
                )
            )
            continue
        final = matching_finals[0]
        final_proof = proofs_by_step[final.step_id][final.fact_id]
        closure = _reverse_closure((final.step_id,), graph)
        collection_steps = tuple(
            sorted(
                (
                    step_id
                    for step_id in closure
                    if any(
                        proof.collection_obligation != "fact"
                        for proof in proofs_by_step.get(step_id, {}).values()
                    )
                ),
                key=lambda step_id: step_order.get(step_id, len(step_order)),
            )
        )
        requirement_proofs.append(
            RequirementCoverageProof(
                requirement.requirement_id,
                requirement.semantic_type,
                requirement.required,
                final.step_id,
                final.fact_id,
                final_proof.collection_obligation,
                collection_steps,
            )
        )
    return PlanCoverageProof(
        steps=tuple(
            StepCriticality(
                step_id=step.step_id,
                criticality=(
                    "required" if step.step_id in required_closure else "optional"
                ),
                predecessors=tuple(sorted(graph.get(step.step_id, set()))),
                required_by_requirement_ids=tuple(sorted(required_by[step.step_id])),
            )
            for step in plan.result.steps
            if step.step_id in all_closure
        ),
        required_final_refs=frozenset(required_final_refs),
        fact_collection_scopes=tuple(
            FactCollectionScope(step_id, fact_id, proof.collection_scope)
            for step_id, step_proofs in proofs_by_step.items()
            for fact_id, proof in step_proofs.items()
        ),
        requirements=tuple(requirement_proofs),
    )


def collection_obligation_satisfied(
    requirement: RequirementCoverageProof, evidence: EvidenceBundle
) -> bool:
    if requirement.final_step_id is None or requirement.final_fact_id is None:
        return False
    if requirement.collection_obligation == "fact":
        return True
    steps = {step.step_id: step for step in evidence.steps}
    final_step = steps.get(requirement.final_step_id)
    if final_step is None:
        return False
    failed = {
        Outcome.QUERY_ERROR,
        Outcome.MCP_UNAVAILABLE,
        Outcome.LLM_UNAVAILABLE,
        Outcome.CONTRACT_ERROR,
    }
    if final_step.status in failed:
        return False
    if requirement.collection_obligation == "visible_page":
        return final_step.collection_scope in {"visible_page", "complete_set"}
    for step_id in requirement.collection_step_ids:
        step = steps.get(step_id)
        if (
            step is None
            or step.collection_scope != "complete_set"
            or step.truncated
            or step.has_more
            or step.status in failed | {Outcome.PARTIAL}
        ):
            return False
    return True


def cross_artifact_evidence_issues(
    plan: PlannerOutput,
    coverage_proof: PlanCoverageProof,
    evidence: EvidenceBundle,
) -> tuple[ContractIssue, ...]:
    expected = {
        requirement.requirement_id: requirement
        for requirement in plan.interpretation.required_facts
    }
    actual_counts = Counter(
        requirement.requirement_id for requirement in evidence.coverage.requirements
    )
    if set(actual_counts) != set(expected) or any(
        count != 1 for count in actual_counts.values()
    ):
        return (
            _issue(
                "EVIDENCE_COVERAGE_ID_MISMATCH",
                "/coverage/requirements",
                "Evidence requirements не совпадают one-to-one с pinned plan.",
            ),
        )
    actual = {
        requirement.requirement_id: requirement
        for requirement in evidence.coverage.requirements
    }
    proof_by_requirement = {
        requirement.requirement_id: requirement
        for requirement in coverage_proof.requirements
    }
    issues: list[ContractIssue] = []
    for requirement_id, planned in expected.items():
        covered = actual[requirement_id]
        proof = proof_by_requirement.get(requirement_id)
        if (
            covered.semantic_type != planned.semantic_type
            or proof is None
            or proof.semantic_type != planned.semantic_type
        ):
            issues.append(
                _issue(
                    "EVIDENCE_COVERAGE_ID_MISMATCH",
                    "/coverage/requirements",
                    "Evidence semantic requirement не совпадает с pinned plan/proof.",
                )
            )
            continue
        if covered.required != planned.required or proof.required != planned.required:
            issues.append(
                _issue(
                    "EVIDENCE_COVERAGE_CRITICALITY_MISMATCH",
                    "/coverage/requirements",
                    "Evidence required flag не совпадает с immutable CoverageProof.",
                )
            )
        if proof.final_step_id is not None and proof.final_fact_id is not None:
            mapped_fact_ids = {
                fact.fact_instance_id
                for fact in evidence.facts
                if fact.step_id == proof.final_step_id
                and fact.fact_id == proof.final_fact_id
            }
            if not set(covered.fact_instance_ids) <= mapped_fact_ids:
                issues.append(
                    _issue(
                        "EVIDENCE_COVERAGE_ID_MISMATCH",
                        "/coverage/requirements",
                        "Coverage fact refs не принадлежат exact final provider.",
                    )
                )

    expected_sufficient = all(
        not requirement.required
        or (
            actual[requirement.requirement_id].status is CoverageStatus.COVERED
            and collection_obligation_satisfied(requirement, evidence)
        )
        for requirement in coverage_proof.requirements
    )
    if evidence.coverage.sufficient != expected_sufficient:
        if any(
            requirement.required
            and actual[requirement.requirement_id].status is CoverageStatus.COVERED
            and not collection_obligation_satisfied(requirement, evidence)
            for requirement in coverage_proof.requirements
        ):
            issues.append(
                _issue(
                    "EVIDENCE_COLLECTION_COMPLETENESS_MISMATCH",
                    "/coverage/sufficient",
                    "Required collection obligation не выполнен evidence steps.",
                )
            )
        issues.append(
            _issue(
                "EVIDENCE_SUFFICIENT_MISMATCH",
                "/coverage/sufficient",
                "coverage.sufficient не совпадает с pinned cross-artifact proof.",
            )
        )
    return _deduplicate(issues)


def validate_evidence_against_plan(
    plan: PlannerOutput,
    coverage_proof: PlanCoverageProof,
    evidence: EvidenceBundle,
) -> None:
    raise_for_issues(cross_artifact_evidence_issues(plan, coverage_proof, evidence))


def context_proof_evidence_issues(
    coverage_proof: PlanCoverageProof,
    evidence: EvidenceBundle,
    *,
    selection_proofs: Sequence[SelectionProof],
    filter_retention_proofs: Sequence[FilterRetentionProof],
    available_skills: Sequence[Skill],
) -> tuple[ContractIssue, ...]:
    """Validate private selection/filter proofs against public Evidence 1.1."""

    catalog = {(skill.skill_id, skill.version): skill for skill in available_skills}
    snapshot = {
        (skill.skill_id, skill.version): skill.digest
        for skill in evidence.catalog_snapshot.skills
    }
    skill_by_step = {
        step.step_id: catalog[pair]
        for step in evidence.steps
        if (pair := _skill_operation_pair(step.operation_ref)) in catalog
        and snapshot.get(pair) == catalog[pair].integrity.digest
    }
    return _deduplicate(
        _context_proof_issues(
            evidence,
            selection_proofs=selection_proofs,
            filter_retention_proofs=filter_retention_proofs,
            required_step_ids=frozenset(coverage_proof.required_steps),
            fact_index={fact.fact_instance_id: fact for fact in evidence.facts},
            skill_by_step=skill_by_step,
        )
    )


def validate_context_proofs_against_evidence(
    coverage_proof: PlanCoverageProof,
    evidence: EvidenceBundle,
    *,
    selection_proofs: Sequence[SelectionProof],
    filter_retention_proofs: Sequence[FilterRetentionProof],
    available_skills: Sequence[Skill],
) -> None:
    raise_for_issues(
        context_proof_evidence_issues(
            coverage_proof,
            evidence,
            selection_proofs=selection_proofs,
            filter_retention_proofs=filter_retention_proofs,
            available_skills=available_skills,
        )
    )


def _coverage_proofs_by_step(
    plan: PlannerOutput, catalog: dict[tuple[str, str], Skill]
) -> dict[str, dict[str, _FactProof]]:
    if not isinstance(plan.result, ExecuteResult):
        return {}
    proofs_by_step: dict[str, dict[str, _FactProof]] = {}
    for step in plan.result.steps:
        if isinstance(step, SkillCall):
            skill = catalog.get((step.skill_id, step.skill_version))
            if skill is not None:
                proofs_by_step[step.step_id] = _skill_call_proofs(step, skill)
        elif isinstance(step, (FilterOperator, RankOperator)):
            proofs_by_step[step.step_id] = {
                fact_id: replace(proof, collection_obligation="complete_set")
                for fact_id, proof in proofs_by_step.get(step.input_step_id, {}).items()
            }
        elif isinstance(step, JoinOperator):
            joined = {
                fact_id: replace(proof, collection_obligation="complete_set")
                for fact_id, proof in proofs_by_step.get(step.left_step_id, {}).items()
            }
            joined.update(
                {
                    fact_id: replace(proof, collection_obligation="complete_set")
                    for fact_id, proof in proofs_by_step.get(
                        step.right_step_id, {}
                    ).items()
                }
            )
            proofs_by_step[step.step_id] = joined
        elif isinstance(step, CountOperator):
            source = proofs_by_step.get(step.input_step_id, {})
            if all(fact_id in source for fact_id in step.distinct_by_fact_ids):
                times = {
                    proof.time_semantics
                    for proof in source.values()
                    if proof.time_semantics != "none"
                }
                proofs_by_step[step.step_id] = {
                    step.result_fact_id: _FactProof(
                        step.result_fact_id,
                        "measure.count",
                        FactValueType.INTEGER.value,
                        "aggregate",
                        "none",
                        times.pop() if len(times) == 1 else "none",
                        True,
                        True,
                        False,
                        _combined_collection_scope(source.values()),
                        "complete_set",
                    )
                }
        elif isinstance(step, AggregateOperator):
            source = proofs_by_step.get(step.input_step_id, {})
            measure = source.get(step.measure_fact_id)
            if measure is not None:
                proofs_by_step[step.step_id] = {
                    step.result_fact_id: _FactProof(
                        step.result_fact_id,
                        measure.semantic_type,
                        measure.value_type,
                        "many" if step.group_by_fact_ids else "aggregate",
                        measure.unit_dimension,
                        measure.time_semantics,
                        all(item in source for item in step.group_by_fact_ids),
                        True,
                        False,
                        _combined_collection_scope(source.values()),
                        "complete_set",
                    )
                }
        elif isinstance(step, CalculateOperator):
            source = proofs_by_step.get(step.input_step_id, {})
            times = {
                proof.time_semantics
                for proof in source.values()
                if proof.time_semantics != "none"
            }
            proofs_by_step[step.step_id] = {
                step.result_fact_id: _FactProof(
                    step.result_fact_id,
                    step.result_semantic_type,
                    FactValueType.DECIMAL.value,
                    "aggregate",
                    "none",
                    times.pop() if len(times) == 1 else "none",
                    True,
                    True,
                    False,
                    _combined_collection_scope(source.values()),
                    "complete_set",
                )
            }
        elif isinstance(step, NormalizePeriodOperator):
            proofs_by_step[step.step_id] = {
                step.result_fact_id: _FactProof(
                    step.result_fact_id,
                    "time.period",
                    FactValueType.PERIOD.value,
                    "exactly_one",
                    "none",
                    "period",
                    True,
                    True,
                    False,
                )
            }
    return proofs_by_step


def _combined_collection_scope(
    proofs: Iterable[_FactProof],
) -> Literal["visible_page", "complete_set"]:
    return (
        "visible_page"
        if any(proof.collection_scope == "visible_page" for proof in proofs)
        else "complete_set"
    )


def _plan_coverage_issues(
    plan: PlannerOutput,
    catalog: dict[tuple[str, str], Skill],
) -> list[ContractIssue]:
    if not isinstance(plan.result, ExecuteResult):
        return []
    issues: list[ContractIssue] = []
    proofs_by_step = _coverage_proofs_by_step(plan, catalog)

    all_proofs = [
        proof
        for step_proofs in proofs_by_step.values()
        for proof in step_proofs.values()
    ]
    requirements = plan.interpretation.required_facts
    if not any(requirement.required for requirement in requirements):
        issues.append(
            _issue(
                "PLAN_FACT_REQUIREMENT_UNMET",
                "/interpretation/required_facts",
                "Execute plan должен содержать хотя бы один required FactRequirement.",
            )
        )

    requirement_signatures: dict[tuple[str, ...], int] = {}
    for index, requirement in enumerate(requirements):
        signature = _requirement_signature(requirement)
        previous = requirement_signatures.get(signature)
        if previous is not None:
            issues.append(
                _issue(
                    "PLAN_FINAL_OUTPUT_AMBIGUOUS",
                    f"/interpretation/required_facts/{index}",
                    "Duplicate FactRequirement signature делает final mapping неоднозначным.",
                )
            )
        requirement_signatures.setdefault(signature, index)

    matched_finals: dict[int, list[int]] = defaultdict(list)
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
            continue
        matches = [
            requirement_index
            for requirement_index, requirement in enumerate(requirements)
            if _proof_matches(requirement, proof)
        ]
        if not matches:
            issues.append(
                _issue(
                    "PLAN_FINAL_OUTPUT_UNCLAIMED",
                    f"/result/final_outputs/{index}",
                    "Final output не покрывает ни один declared FactRequirement.",
                )
            )
        elif len(matches) > 1:
            issues.append(
                _issue(
                    "PLAN_FINAL_OUTPUT_AMBIGUOUS",
                    f"/result/final_outputs/{index}",
                    "Final output совместим более чем с одним FactRequirement.",
                )
            )
        else:
            matched_finals[matches[0]].append(index)

    for index, requirement in enumerate(requirements):
        pointer = f"/interpretation/required_facts/{index}"
        compatible = [
            proof for proof in all_proofs if _proof_matches(requirement, proof)
        ]
        if not compatible:
            issues.append(_requirement_mismatch_issue(requirement, all_proofs, pointer))
            continue
        final_count = len(matched_finals[index])
        if final_count > 1:
            issues.append(
                _issue(
                    "PLAN_FINAL_OUTPUT_AMBIGUOUS",
                    pointer,
                    "FactRequirement покрыт несколькими final outputs.",
                )
            )
        elif requirement.required and final_count == 0:
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
            and definitions[fact_id].role in {"entity", "dimension", "provenance"}
            for fact_id in identity_ids
        )
    )
    time_semantics = _contract_time_semantics(skill, requested)
    collection_scope = collection_scope_for_skill(skill)
    collection_obligation = _skill_collection_obligation(skill)
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
            collection_scope=collection_scope,
            collection_obligation=collection_obligation,
            resolver_identity=(
                contract.resolution is not None
                and contract.resolution.identity_fact_id == fact.fact_id
            ),
        )
        for fact in contract.facts
        if fact.fact_id in requested
    }


def _skill_collection_obligation(
    skill: Skill,
) -> Literal["fact", "visible_page", "complete_set"]:
    contract = skill.output_contract
    if contract.cardinality == "aggregate":
        return "complete_set"
    if contract.cardinality != "many":
        return "fact"
    if contract.sufficiency.truncation_policy == "page_is_complete":
        return "visible_page"
    return "complete_set"


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
    if not _proof_signature_matches(requirement, proof):
        return False
    return not (
        proof.semantic_type == "measure.count"
        and proof.collection_scope != "complete_set"
    )


def _proof_signature_matches(requirement: FactRequirement, proof: _FactProof) -> bool:
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
    resolver_select_one = (
        requirement.cardinality == "one"
        and proof.cardinality == "many"
        and proof.resolver_identity
    )
    if (
        proof.cardinality not in cardinalities[requirement.cardinality]
        and not resolver_select_one
    ):
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


def _requirement_signature(requirement: FactRequirement) -> tuple[str, ...]:
    return (
        requirement.semantic_type,
        requirement.value_type.value,
        requirement.cardinality,
        requirement.unit_dimension or "none",
        requirement.time_semantics or "none",
    )


def _requirement_mismatch_issue(
    requirement: FactRequirement,
    proofs: Sequence[_FactProof],
    pointer: str,
) -> ContractIssue:
    if not proofs:
        code = "PLAN_FACT_REQUIREMENT_UNMET"
        message = "Ни один step не предоставляет fact для FactRequirement."
        return _issue(code, pointer, message)

    if requirement.semantic_type == "measure.count" and any(
        proof.collection_scope == "visible_page"
        and _proof_signature_matches(requirement, proof)
        for proof in proofs
    ):
        return _issue(
            "PLAN_COUNT_SCOPE_MISMATCH",
            pointer,
            "Count видимой страницы не может покрывать requirement общего количества.",
        )

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
        proof
        for proof in matching
        if proof.cardinality in allowed_cardinalities
        or (
            requirement.cardinality == "one"
            and proof.cardinality == "many"
            and proof.resolver_identity
        )
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
        "string": {FactValueType.STRING, FactValueType.ENUM},
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


def _cursor_encoding_matches_fact(encoding: str, fact: FactDefinition) -> bool:
    allowed: dict[str, set[FactValueType]] = {
        "string": {FactValueType.STRING},
        "integer": {FactValueType.INTEGER},
        "decimal": {
            FactValueType.DECIMAL,
            FactValueType.MONEY,
            FactValueType.QUANTITY,
            FactValueType.PERCENTAGE,
        },
        "date": {FactValueType.DATE},
        "datetime": {FactValueType.DATETIME},
        "object_ref": {FactValueType.ENTITY_REF},
    }
    return fact.value_type in allowed[encoding]


def _has_static_top(parsed: ParsedQuery) -> bool:
    for statement in parsed.statements:
        for index, token in enumerate(statement.tokens[:-1]):
            if (
                token.kind == "word"
                and token.upper in {"TOP", "ПЕРВЫЕ"}
                and statement.tokens[index + 1].kind == "number"
            ):
                return True
    return False


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


def _reverse_closure(roots: Iterable[str], graph: dict[str, set[str]]) -> set[str]:
    closure = set(roots)
    pending = list(closure)
    while pending:
        step_id = pending.pop()
        for predecessor in graph.get(step_id, set()):
            if predecessor not in closure:
                closure.add(predecessor)
                pending.append(predecessor)
    return closure


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
        FactValueType.ENUM: isinstance(value, str),
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


def _context_proof_issues(
    evidence: EvidenceBundle,
    *,
    selection_proofs: Sequence[SelectionProof],
    filter_retention_proofs: Sequence[FilterRetentionProof],
    required_step_ids: frozenset[str],
    fact_index: dict[object, Fact],
    skill_by_step: dict[str, Skill],
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    steps = {step.step_id: step for step in evidence.steps}
    exports_by_fact: dict[object, list[ContextExport]] = defaultdict(list)
    exports_by_handle: dict[str, set[object]] = defaultdict(set)
    for export in evidence.context_exports:
        exports_by_fact[export.fact_instance_id].append(export)
        exports_by_handle[export.context_handle].add(export.fact_instance_id)

    required_technical_failure = any(
        step.step_id in required_step_ids
        and step.status
        in {Outcome.QUERY_ERROR, Outcome.MCP_UNAVAILABLE, Outcome.CONTRACT_ERROR}
        for step in evidence.steps
    )
    if required_technical_failure and (
        selection_proofs
        or filter_retention_proofs
        or evidence.context_exports
    ):
        issues.append(
            _issue(
                "CONTEXT_COMMIT_ON_TECHNICAL_FAILURE",
                "/context_exports",
                (
                    "Context proofs/exports запрещены при техническом падении "
                    "обязательного downstream step."
                ),
            )
        )

    expected_export_ids: list[object] = []
    proof_fact_ids: set[object] = set()
    for index, proof in enumerate(selection_proofs):
        pointer = f"/selection_proofs/{index}"
        step = steps.get(proof.resolver.step_id)
        skill = skill_by_step.get(proof.resolver.step_id)
        resolution = None if skill is None else skill.output_contract.resolution
        policy = (
            None
            if skill is None
            else next(
                (
                    item
                    for item in (skill.output_contract.context_export_policy or ())
                    if isinstance(item, SelectedOnlyContextPolicy)
                    and item.fact_id == proof.resolver.identity_fact_id
                    and item.slot_key == proof.resolver.slot_key
                ),
                None,
            )
        )
        contract_matches = (
            step is not None
            and step.step_id in required_step_ids
            and step.status is Outcome.SUCCESS_WITH_ROWS
            and step.collection_scope == "complete_set"
            and not step.has_more
            and not step.truncated
            and skill is not None
            and resolution is not None
            and proof.resolver.skill_id == skill.skill_id
            and proof.resolver.identity_fact_id == resolution.identity_fact_id
            and proof.resolver.slot_key == resolution.default_slot_key
            and proof.resolver.mode in {"select_one", "select_set"}
            and policy is not None
        )
        if not contract_matches:
            issues.append(
                _issue(
                    "CONTEXT_SELECTION_PROOF_INVALID",
                    pointer,
                    (
                        "Selection proof не совпадает с required complete resolver "
                        "step и exact selected_only policy."
                    ),
                )
            )
            continue

        assert step is not None and skill is not None and resolution is not None
        identity_facts: dict[tuple[str, str, object], Fact] = {}
        for fact in evidence.facts:
            if (
                fact.step_id != step.step_id
                or fact.fact_id != resolution.identity_fact_id
                or not isinstance(fact.value, EntityRef)
                or not _semantic_role_proofs_satisfied(skill, evidence.facts, fact.row_id)
            ):
                continue
            key = (fact.semantic_type, fact.value.object_type, fact.value.unique_id)
            identity_facts.setdefault(key, fact)
        expected_facts = tuple(identity_facts.values())
        expected_ids = tuple(fact.fact_instance_id for fact in expected_facts)
        expected_identities = tuple(
            (fact.semantic_type, fact.value.object_type, fact.value.unique_id)
            for fact in expected_facts
            if isinstance(fact.value, EntityRef)
        )
        actual_identities = tuple(
            (item.semantic_type, item.physical_type, item.unique_id)
            for item in proof.identities
        )
        cardinality_valid = (
            proof.state == "selected_one"
            and proof.resolver.mode == "select_one"
            and len(expected_facts) == 1
        ) or (
            proof.state == "selected_set"
            and proof.resolver.mode == "select_set"
            and bool(expected_facts)
            and policy is not None
            and len(expected_facts) <= policy.max_members
        )
        payload = {
            "resolver": proof.resolver.model_dump(mode="json"),
            "state": proof.state,
            "fact_instance_ids": [str(item) for item in proof.fact_instance_ids],
            "identities": [item.model_dump(mode="json") for item in proof.identities],
            "complete": True,
        }
        digest_valid = (
            hashlib.sha256(canonicalize(payload)).hexdigest() == proof.proof_digest
        )
        exact_members = (
            len(set(proof.fact_instance_ids)) == len(proof.fact_instance_ids)
            and set(proof.fact_instance_ids) == set(expected_ids)
            and len(proof.identities) == len(expected_identities)
            and set(actual_identities) == set(expected_identities)
        )
        if not cardinality_valid or not digest_valid or not exact_members:
            issues.append(
                _issue(
                    "CONTEXT_SELECTION_PROOF_INVALID",
                    pointer,
                    (
                        "Selection proof не подтверждает exact member set, "
                        "identity/cardinality или canonical digest."
                    ),
                )
            )
            continue
        if proof_fact_ids & set(proof.fact_instance_ids):
            issues.append(
                _issue(
                    "CONTEXT_SELECTION_PROOF_INVALID",
                    pointer + "/fact_instance_ids",
                    "Один fact instance не может входить в несколько context proofs.",
                )
            )
        proof_fact_ids.update(proof.fact_instance_ids)
        expected_export_ids.extend(proof.fact_instance_ids)
        handles = {
            export.context_handle
            for fact_id in proof.fact_instance_ids
            for export in exports_by_fact.get(fact_id, ())
        }
        if (
            len(handles) != 1
            or any(len(exports_by_fact.get(fact_id, ())) != 1 for fact_id in proof.fact_instance_ids)
            or exports_by_handle[next(iter(handles), "")] != set(proof.fact_instance_ids)
        ):
            issues.append(
                _issue(
                    "CONTEXT_EXPORT_PROOF_MISMATCH",
                    "/context_exports",
                    "Selected member set должен экспортироваться одним exact handle.",
                )
            )

    for index, filter_proof in enumerate(filter_retention_proofs):
        pointer = f"/filter_retention_proofs/{index}"
        step = steps.get(filter_proof.step_id)
        skill = skill_by_step.get(filter_proof.step_id)
        retained_fact = fact_index.get(filter_proof.fact_instance_id)
        filter_policy = (
            None
            if skill is None
            else next(
                (
                    item
                    for item in (skill.output_contract.context_export_policy or ())
                    if isinstance(item, ConfirmedFilterContextPolicy)
                    and item.fact_id == filter_proof.fact_id
                    and item.slot_key == filter_proof.slot_key
                ),
                None,
            )
        )
        definition = (
            None
            if skill is None
            else next(
                (
                    item
                    for item in skill.output_contract.facts
                    if item.fact_id == filter_proof.fact_id
                ),
                None,
            )
        )
        value_digest = (
            ""
            if retained_fact is None
            else hashlib.sha256(
                canonicalize(retained_fact.model_dump(mode="json")["value"])
            ).hexdigest()
        )
        filter_payload = {
            "step_id": filter_proof.step_id,
            "fact_instance_id": str(filter_proof.fact_instance_id),
            "fact_id": filter_proof.fact_id,
            "slot_key": filter_proof.slot_key,
            "semantic_type": filter_proof.semantic_type,
            "value_type": filter_proof.value_type,
            "canonical_value_digest": filter_proof.canonical_value_digest,
        }
        valid = (
            step is not None
            and step.step_id in required_step_ids
            and step.status
            in {Outcome.SUCCESS_WITH_ROWS, Outcome.ZERO_AGGREGATE, Outcome.SUCCESS_EMPTY}
            and skill is not None
            and filter_policy is not None
            and definition is not None
            and retained_fact is not None
            and retained_fact.step_id == filter_proof.step_id
            and retained_fact.fact_id == filter_proof.fact_id
            and retained_fact.semantic_type
            == filter_proof.semantic_type
            == filter_policy.semantic_type
            and retained_fact.value_type.value
            == filter_proof.value_type
            == filter_policy.value_type
            and value_digest == filter_proof.canonical_value_digest
            and hashlib.sha256(canonicalize(filter_payload)).hexdigest()
            == filter_proof.proof_digest
        )
        if not valid:
            issues.append(
                _issue(
                    "CONTEXT_FILTER_PROOF_INVALID",
                    pointer,
                    (
                        "Filter proof не совпадает с required producer step, "
                        "exact fact/policy/value digest."
                    ),
                )
            )
            continue
        if filter_proof.fact_instance_id in proof_fact_ids:
            issues.append(
                _issue(
                    "CONTEXT_FILTER_PROOF_INVALID",
                    pointer + "/fact_instance_id",
                    "Один fact instance не может входить в несколько context proofs.",
                )
            )
        proof_fact_ids.add(filter_proof.fact_instance_id)
        expected_export_ids.append(filter_proof.fact_instance_id)
        matching_exports = exports_by_fact.get(filter_proof.fact_instance_id, ())
        if (
            len(matching_exports) != 1
            or len(exports_by_handle[matching_exports[0].context_handle]) != 1
        ):
            issues.append(
                _issue(
                    "CONTEXT_EXPORT_PROOF_MISMATCH",
                    "/context_exports",
                    "Confirmed filter должен экспортироваться одним отдельным handle.",
                )
            )

    actual_export_ids = [item.fact_instance_id for item in evidence.context_exports]
    if Counter(actual_export_ids) != Counter(expected_export_ids):
        issues.append(
            _issue(
                "CONTEXT_EXPORT_PROOF_MISSING",
                "/context_exports",
                "Context exports должны точно совпадать с validated proof member set.",
            )
        )
    return issues


def _semantic_role_proofs_satisfied(
    skill: Skill, facts: Sequence[Fact], row_id: str
) -> bool:
    resolution = skill.output_contract.resolution
    if resolution is None:
        return False
    definitions = {item.fact_id: item for item in skill.output_contract.facts}
    for fact_id in resolution.role_proof_fact_ids:
        definition = definitions.get(fact_id)
        matches = [
            fact for fact in facts if fact.row_id == row_id and fact.fact_id == fact_id
        ]
        if definition is None or len(matches) != 1:
            return False
        if definition.value_type is FactValueType.BOOLEAN:
            if matches[0].value is not True:
                return False
        elif definition.value_type is FactValueType.ENUM:
            allowed = definition.allowed_values or ()
            if len(allowed) != 1 or matches[0].value != allowed[0]:
                return False
        else:
            return False
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
        and binding.converter == "object_ref"
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
