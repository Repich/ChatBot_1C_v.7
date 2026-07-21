"""Deterministic Russian renderers for authoritative evidence."""

from __future__ import annotations

from collections import defaultdict

from chatbot1c.application.execution import ExecutionResult
from chatbot1c.domain.evidence import (
    CitationValue,
    DocumentFragment,
    Fact,
    UnitResolved,
)
from chatbot1c.domain.outcomes import Outcome
from chatbot1c.domain.plan import (
    CapabilityGapResult,
    ClarifyResult,
    LiteralBinding,
    PlannerOutput,
    RefuseResult,
    SkillCall,
    SlotBinding,
)
from chatbot1c.domain.types import EntityRef, Period


def render_execution(plan: PlannerOutput, execution: ExecutionResult) -> str:
    outcome = execution.outcome
    if outcome is Outcome.SUCCESS_EMPTY:
        filters = _empty_filter_summary(plan, execution)
        suffix = f" ({filters})" if filters else ""
        return (
            f"По заданным условиям данные не найдены{suffix}. "
            "Запрос выполнен успешно."
        )
    if outcome is Outcome.DOCUMENTATION_EMPTY:
        return "Во встроенной справке этого релиза подходящий фрагмент не найден."
    if outcome is Outcome.QUERY_ERROR:
        return (
            "Не удалось выполнить read-only запрос к 1С. Данные не подменялись. "
            f"Идентификатор операции: {execution.evidence.trace_id}."
        )
    if outcome is Outcome.MCP_UNAVAILABLE:
        return (
            "Сервис read-only MCP-доступа к 1С временно недоступен. "
            f"Идентификатор операции: {execution.evidence.trace_id}."
        )
    if outcome is Outcome.CLARIFICATION_REQUIRED:
        return "Найдено несколько подходящих объектов. Уточните, какой выбрать."
    if outcome is Outcome.CONTRACT_ERROR:
        return (
            "Результат зависимости не прошел проверку контракта; данные ответа "
            f"не показаны. Идентификатор операции: {execution.evidence.trace_id}."
        )

    fragments = [
        fact
        for fact in execution.evidence.facts
        if isinstance(fact.value, DocumentFragment)
    ]
    if fragments:
        citations = {
            citation.citation_id: citation
            for citation in execution.evidence.citations
        }
        citation_by_row: defaultdict[str, list[str]] = defaultdict(list)
        for fact in execution.evidence.facts:
            if isinstance(fact.value, CitationValue):
                citation = citations.get(fact.value.citation_id)
                if citation is not None:
                    citation_by_row[fact.row_id].append(
                        f"[{citation.title}]({citation.source_uri})"
                    )
        blocks = []
        for fact in fragments:
            source = " ".join(citation_by_row[fact.row_id])
            fragment = fact.value
            if isinstance(fragment, DocumentFragment):
                blocks.append(f"{fragment.text}\n\nИсточник: {source}".strip())
        return "\n\n".join(blocks)

    final_refs = {
        (reference.step_id, reference.fact_id)
        for reference in getattr(plan.result, "final_outputs", ())
    }
    facts = [
        fact
        for fact in execution.evidence.facts
        if (fact.step_id, fact.fact_id) in final_refs
    ]
    if not facts:
        facts = list(execution.evidence.facts)
    definitions = {
        (step.step_id, definition.fact_id): definition
        for step in execution.steps
        if step.skill is not None
        for definition in step.skill.output_contract.facts
    }
    titles = {
        fact.fact_id: definitions[(fact.step_id, fact.fact_id)].title_ru
        for fact in facts
        if (fact.step_id, fact.fact_id) in definitions
    }
    table = render_table(
        facts, titles=titles, zero=outcome is Outcome.ZERO_AGGREGATE
    )
    pagination = execution.evidence.pagination
    suffix = ""
    if pagination is not None and pagination.has_more:
        suffix = (
            f"\n\nПоказано {pagination.shown} строк. "
            "Для следующей страницы используйте продолжение списка."
        )
    if outcome is Outcome.PARTIAL:
        missing = [
            item.semantic_type
            for item in execution.evidence.coverage.requirements
            if item.status.value != "covered"
        ]
        reason = ", ".join(missing) if missing else "полная выборка"
        return (
            "Получена только подтвержденная часть данных; отсутствует: "
            f"{reason}.\n\n{table}{suffix}"
        )
    return table + suffix


def render_table(
    facts: list[Fact], *, titles: dict[str, str] | None = None, zero: bool = False
) -> str:
    rows: defaultdict[str, dict[str, Fact]] = defaultdict(dict)
    columns: list[str] = []
    for fact in facts:
        rows[fact.row_id][fact.fact_id] = fact
        if fact.fact_id not in columns:
            columns.append(fact.fact_id)
    if not rows:
        return "Подтвержденных данных для отображения нет."
    labels = titles or {}
    header = " | ".join(_column_label(column, rows, labels) for column in columns)
    divider = " | ".join("---" for _ in columns)
    lines = [header, divider]
    for row in rows.values():
        lines.append(
            " | ".join(_display(row[column].value) if column in row else "" for column in columns)
        )
    prefix = "Подтвержденное значение равно нулю.\n\n" if zero else ""
    return prefix + "\n".join(lines)


def render_decision(plan: PlannerOutput) -> tuple[str, Outcome]:
    result = plan.result
    if isinstance(result, ClarifyResult):
        choices = "\n".join(f"- {choice.label_ru}" for choice in result.choices)
        return (
            f"{result.question_ru}\n{choices}".strip(),
            Outcome.CLARIFICATION_REQUIRED,
        )
    if isinstance(result, RefuseResult):
        return result.message_ru, Outcome.REFUSED
    if isinstance(result, CapabilityGapResult):
        return result.message_ru, Outcome.CAPABILITY_GAP
    raise ValueError("execute decision must be rendered from evidence")


def _display(value: object) -> str:
    if isinstance(value, EntityRef):
        return value.presentation
    if isinstance(value, Period):
        return f"{value.start:%d.%m.%Y} - {value.end_exclusive:%d.%m.%Y}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _column_label(
    fact_id: str,
    rows: defaultdict[str, dict[str, Fact]],
    titles: dict[str, str],
) -> str:
    title = titles.get(fact_id, fact_id)
    units = {
        unit.code
        for row in rows.values()
        if fact_id in row
        for unit in (row[fact_id].unit,)
        if isinstance(unit, UnitResolved)
    }
    return f"{title}, {next(iter(units))}" if len(units) == 1 else title


def _empty_filter_summary(
    plan: PlannerOutput, execution: ExecutionResult
) -> str:
    if not hasattr(plan.result, "steps"):
        return ""
    skills = {
        step.step_id: step.skill
        for step in execution.steps
        if step.skill is not None
    }
    slots = {slot.slot_id: slot for slot in plan.interpretation.slots}
    rendered: list[str] = []
    for step in plan.result.steps:
        if not isinstance(step, SkillCall):
            continue
        skill = skills.get(step.step_id)
        if skill is None:
            continue
        parameters = {parameter.name: parameter for parameter in skill.parameters}
        for argument in step.arguments:
            parameter = parameters.get(argument.parameter)
            if parameter is None:
                continue
            binding = argument.binding
            if isinstance(binding, SlotBinding):
                slot = slots.get(binding.slot_id)
                if slot is None or not isinstance(slot.binding, LiteralBinding):
                    continue
                literal = slot.binding
            elif isinstance(binding, LiteralBinding):
                literal = binding
            else:
                continue
            value = _safe_public_literal(literal.value)
            if value is not None:
                rendered.append(f"{parameter.title_ru}: {value}")
    return "; ".join(rendered[:8])


def _safe_public_literal(value: object) -> str | None:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized[:240] if normalized else None
    return None
