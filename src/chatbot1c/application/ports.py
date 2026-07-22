"""Ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from chatbot1c.application.models import (
    ClarificationResponse,
    ContextFact,
    ContextSlotSummary,
    ExecuteQueryEnvelope,
    ExecuteQueryRequest,
    GetMetadataRequest,
    HelpChunk,
    HelpSearchRequest,
    MaintenancePreview,
    MetadataEnvelope,
    PageContinuation,
    PageStrategy,
    PendingClarification,
    PendingClarificationDraft,
    PinnedCatalog,
    PlannerRequest,
    SessionRecord,
    TurnEvent,
    TurnRecord,
)
from chatbot1c.domain.evidence import DatabaseStateMarker
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill


class PlannerPort(Protocol):
    def outbound_http_request(self, request: PlannerRequest) -> bytes | None: ...

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


class DatabaseStateMarkerPort(Protocol):
    def capture(self, catalog: PinnedCatalog) -> DatabaseStateMarker: ...


class CatalogRepository(Protocol):
    def initialize(self) -> None: ...

    def load_catalog(self) -> PinnedCatalog: ...

    def load_catalog_revision(self, revision: int) -> PinnedCatalog: ...

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

    def context_slots(self, session_id: UUID) -> tuple[ContextSlotSummary, ...]: ...

    def context_handle_states(
        self, session_id: UUID, handles: Sequence[str] = ()
    ) -> Mapping[str, str]: ...

    def active_pending(self, session_id: UUID) -> PendingClarification | None: ...

    def pending_for_claim_turn(self, turn_id: UUID) -> PendingClarification | None: ...

    def claim_clarification(
        self,
        *,
        session_id: UUID,
        text: str,
        client_message_id: str,
        expected_context_version: int,
        response: ClarificationResponse,
        active_catalog: PinnedCatalog,
        database_marker: str,
    ) -> tuple[PendingClarification, TurnRecord]: ...

    def remove_context(
        self, session_id: UUID, handle: str, expected_context_version: int
    ) -> tuple[SessionRecord, TurnRecord]: ...

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
        pending_clarification: PendingClarificationDraft | None = None,
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


class ContinuationRepository(Protocol):
    def create_continuation(
        self,
        *,
        session_id: UUID,
        origin_turn_id: UUID,
        step_id: str,
        skill_id: str,
        skill_version: str,
        skill_digest: str,
        catalog_snapshot_id: UUID,
        catalog_revision: int,
        arguments: Mapping[str, JsonValue],
        plan_json: str,
        strategy: PageStrategy,
        page_size: int,
        shown: int,
        database_marker: str,
        sort_tuple: Sequence[JsonValue],
        cursor_values: Mapping[str, JsonValue],
    ) -> PageContinuation: ...

    def claim_continuation(
        self,
        handle: str,
        *,
        session_id: UUID,
        active_catalog: PinnedCatalog,
        database_marker: str,
    ) -> tuple[PageContinuation, TurnRecord]: ...

    def get_continuation(self, handle: str) -> PageContinuation | None: ...

    def continuation_for_turn(self, turn_id: UUID) -> PageContinuation | None: ...


class MaintenanceRepository(Protocol):
    def preview_clear(
        self,
        scopes: Sequence[str],
    ) -> MaintenancePreview: ...

    def confirm_clear(
        self, confirmation_token: str, scopes: Sequence[str]
    ) -> MaintenancePreview: ...
