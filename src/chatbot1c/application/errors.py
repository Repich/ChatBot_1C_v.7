"""Stable application errors shared by web and CLI adapters."""

from __future__ import annotations

from dataclasses import dataclass

from chatbot1c.contracts.errors import ContractIssue


@dataclass(frozen=True, slots=True)
class ApplicationError(Exception):
    code: str
    message_ru: str
    status_code: int = 400
    issues: tuple[ContractIssue, ...] = ()

    def __str__(self) -> str:
        return f"{self.code}: {self.message_ru}"


class CatalogConflictError(ApplicationError):
    def __init__(self, message_ru: str) -> None:
        super().__init__("CATALOG_CONFLICT", message_ru, 409)


class NotFoundError(ApplicationError):
    def __init__(self, resource_ru: str) -> None:
        super().__init__("NOT_FOUND", f"{resource_ru} не найден.", 404)
