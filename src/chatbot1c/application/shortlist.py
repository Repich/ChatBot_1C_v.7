"""Generic bounded shortlist selection over declarative skill manifests."""

from __future__ import annotations

import re
from collections.abc import Sequence

from chatbot1c.application.models import ContextFact, PinnedCatalog
from chatbot1c.application.ports import SkillShortlistPort
from chatbot1c.domain.skill import Skill

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_DOC_SIGNALS = frozenset({"что", "означает", "назначение", "справка", "описание"})
_DATA_SIGNALS = frozenset(
    {"найти", "найди", "покажи", "сколько", "остаток", "строки", "состав", "статус"}
)


class LexicalSkillShortlist(SkillShortlistPort):
    """Scores only declared manifest text/types; query templates are never read."""

    def select(
        self,
        *,
        question: str,
        context: Sequence[ContextFact],
        catalog: PinnedCatalog,
        limit: int,
    ) -> Sequence[Skill]:
        tokens = _tokens(question)
        context_types = {fact.semantic_type for fact in context}
        intent = _intent(tokens)
        ranked: list[tuple[float, str, Skill]] = []
        for skill in catalog.skills.values():
            manifest_text = " ".join(
                (
                    skill.display.name_ru,
                    skill.display.purpose_ru,
                    *skill.selection.aliases_ru,
                    *(example.question_ru for example in skill.examples),
                )
            )
            manifest_tokens = _tokens(manifest_text)
            overlap = len(tokens & manifest_tokens)
            score = float(overlap * 5)
            if intent in skill.selection.intent_kinds:
                score += 4.0
            required = set(skill.selection.required_context_fact_types)
            if required and required <= context_types:
                score += 6.0
            for parameter in skill.parameters:
                if parameter.semantic_type in context_types:
                    score += 2.0
            ranked.append((score, skill.skill_id, skill))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: dict[str, Skill] = {}
        for _, _, candidate in ranked:
            group = _dependency_group(candidate, catalog)
            missing = [skill for skill in group if skill.skill_id not in selected]
            if len(selected) + len(missing) > limit:
                continue
            for skill in missing:
                selected[skill.skill_id] = skill
            if len(selected) == limit:
                break
        return tuple(selected.values())


def _tokens(value: str) -> set[str]:
    return {
        token.casefold().replace("ё", "е")
        for token in _WORD_RE.findall(value)
        if len(token) > 1
    }


def _intent(tokens: set[str]) -> str:
    doc = len(tokens & _DOC_SIGNALS)
    data = len(tokens & _DATA_SIGNALS)
    if doc and data:
        return "mixed"
    if doc > data:
        return "documentation"
    return "data"


def _dependency_group(skill: Skill, catalog: PinnedCatalog) -> tuple[Skill, ...]:
    ordered: list[Skill] = [skill]
    seen = {skill.skill_id}
    index = 0
    while index < len(ordered):
        current = ordered[index]
        index += 1
        for dependency in current.dependencies.skills:
            if dependency.skill_id in seen:
                continue
            resolved = catalog.skills.get(dependency.skill_id)
            if resolved is not None:
                ordered.append(resolved)
                seen.add(resolved.skill_id)
    return tuple(ordered)
