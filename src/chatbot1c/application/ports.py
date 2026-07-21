"""Ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from chatbot1c.application.models import (
    ContextFact,
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    GetMetadataRequest,
    HelpChunk,
    HelpSearchRequest,
    MetadataEnvelope,
    PinnedCatalog,
    PlannerRequest,
    SessionRecord,
    TurnEvent,
    TurnRecord,
)
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill


class PlannerPort(Protocol):
    async def plan(self, request: PlannerRequest) -> PlannerOutput: ...


class SkillShortlistPort(Protocol):
    def select(
        self,
        *,
        question: str,
        context: Sequence[ContextFact],
        catalog: PinnedCatalog,
        limit: int,
    ) -> Sequence[Skill]: ...


class ReadOnly1CPort(Protocol):
    async def execute_query(
        self, request: ExecuteQueryRequest
    ) -> ExecuteQueryEnvelope: ...

    async def get_metadata(self, request: GetMetadataRequest) -> MetadataEnvelope: ...


class DocumentationPort(Protocol):
    async def search(self, request: HelpSearchRequest) -> tuple[HelpChunk, ...]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class CatalogRepository(Protocol):
    def initialize(self) -> None: ...

    def load_catalog(self) -> PinnedCatalog: ...

    def commit_catalog(
        self,
        *,
        expected_revision: int,
        skills: Mapping[str, Skill],
        package_json: str | None,
    ) -> PinnedCatalog: ...

    def known_digest(self, skill_id: str, version: str) -> str | None: ...


class SessionRepository(Protocol):
    def initialize(self) -> None: ...

    def create_session(self, title: str = "Новый диалог") -> SessionRecord: ...

    def list_sessions(self) -> tuple[SessionRecord, ...]: ...

    def get_session(self, session_id: UUID) -> SessionRecord | None: ...

    def delete_session(self, session_id: UUID) -> bool: ...

    def create_turn(
        self,
        *,
        session_id: UUID,
        text: str,
        client_message_id: str,
        expected_context_version: int,
    ) -> tuple[TurnRecord, bool]: ...

    def pin_turn(self, turn_id: UUID, catalog: PinnedCatalog) -> None: ...

    def get_turn(self, turn_id: UUID) -> TurnRecord | None: ...

    def list_turns(self, session_id: UUID) -> tuple[TurnRecord, ...]: ...

    def recent_user_messages(
        self, session_id: UUID, limit: int, *, exclude_turn_id: UUID | None = None
    ) -> tuple[str, ...]: ...

    def context_facts(self, session_id: UUID) -> tuple[ContextFact, ...]: ...

    def complete_turn(
        self,
        *,
        turn_id: UUID,
        assistant_text: str,
        status: str,
        outcome: str,
        plan_json: str | None,
        evidence_json: str | None,
        context_exports: Sequence[ContextFact],
        error_code: str | None = None,
    ) -> TurnRecord: ...

    def append_event(
        self,
        turn_id: UUID,
        event_name: str,
        status: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> TurnEvent: ...

    def events(self, turn_id: UUID, after: int = 0) -> tuple[TurnEvent, ...]: ...


class TraceRepository(Protocol):
    def put_artifact(self, trace_id: UUID, name: str, content: bytes) -> None: ...

    def artifacts(self, trace_id: UUID) -> Mapping[str, bytes]: ...
