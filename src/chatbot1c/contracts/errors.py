"""Stable validation diagnostics with RFC 6901 JSON pointers."""

from __future__ import annotations

from collections.abc import Iterable

from chatbot1c.domain.base import ClosedModel


class ContractIssue(ClosedModel):
    code: str
    json_pointer: str
    message_ru: str
    keyword: str | None = None

    @property
    def message(self) -> str:
        return self.message_ru


class ContractValidationError(ValueError):
    """Raised after a validation stage has collected all deterministic issues."""

    def __init__(self, issues: Iterable[ContractIssue]) -> None:
        ordered = tuple(
            sorted(
                issues,
                key=lambda issue: (issue.json_pointer, issue.code, issue.message_ru),
            )
        )
        if not ordered:
            raise ValueError("ContractValidationError requires at least one issue")
        self.issues = ordered
        super().__init__(self._render())

    def _render(self) -> str:
        return "\n".join(
            f"{issue.code} at {issue.json_pointer or '<root>'}: {issue.message_ru}"
            for issue in self.issues
        )


def raise_for_issues(issues: Iterable[ContractIssue]) -> None:
    materialized = tuple(issues)
    if materialized:
        raise ContractValidationError(materialized)
