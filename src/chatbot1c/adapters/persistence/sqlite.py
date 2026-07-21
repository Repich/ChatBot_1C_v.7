"""SQLite repositories with WAL and Alembic-managed schema upgrades."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Engine, create_engine, event
from sqlalchemy import text as sql_text
from sqlalchemy.engine import Connection, RowMapping, make_url
from sqlalchemy.pool import StaticPool

from chatbot1c.application.errors import ApplicationError, CatalogConflictError
from chatbot1c.application.models import (
    ContextFact,
    EntityFactOrigin,
    MaintenancePreview,
    MaintenanceScope,
    PageContinuation,
    PageStrategy,
    PinnedCatalog,
    SessionRecord,
    TurnEvent,
    TurnRecord,
    canonical_maintenance_scopes,
)
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.plan import ExecuteResult, PlannerOutput, SkillCall
from chatbot1c.domain.skill import (
    DataQueryOperation,
    FactValueType,
    KeysetPagination,
    PrefixPagination,
    Skill,
)
from chatbot1c.domain.types import EntityRef

_CLEAR_SCOPES: frozenset[MaintenanceScope] = frozenset(
    {"sessions", "traces", "raw_payloads"}
)
_MAINTENANCE_SCOPES_ADAPTER = TypeAdapter(tuple[MaintenanceScope, ...])
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_JSON_TUPLE_ADAPTER = TypeAdapter(tuple[JsonValue, ...])
_RAW_PAYLOAD_PREDICATE = (
    "name IN ('request.json', 'context.json', 'planner/request.json', "
    "'planner/response.json') OR name LIKE 'steps/%/request.json' "
    "OR name LIKE 'steps/%/response.json'"
)
_PAGE_HANDLE = re.compile(r"^page_[A-Za-z0-9_-]{32}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("database timestamp must be text")
    return datetime.fromisoformat(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteStore:
    """Implements catalog/session/trace repositories in one local database."""

    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        if url.drivername != "sqlite":
            raise ValueError("slice 1 supports SQLite database URLs only")
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        engine_options: dict[str, Any] = {
            "connect_args": {"check_same_thread": False},
            "pool_pre_ping": True,
        }
        if url.database == ":memory:":
            engine_options["poolclass"] = StaticPool
        self._engine = create_engine(database_url, **engine_options)
        event.listen(self._engine, "connect", self._configure_connection)

    @staticmethod
    def _configure_connection(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @property
    def engine(self) -> Engine:
        return self._engine

    def initialize(self) -> None:
        with self._engine.connect() as connection:
            migration_config = Config()
            migration_config.set_main_option(
                "script_location", str(Path(__file__).with_name("migrations"))
            )
            migration_config.set_main_option(
                "sqlalchemy.url", self._engine.url.render_as_string(hide_password=False)
            )
            migration_config.attributes["connection"] = connection
            command.upgrade(migration_config, "head")

        with self._engine.connect() as connection:
            mode = str(connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar())
            if self._engine.url.database != ":memory:" and mode.casefold() != "wal":
                raise RuntimeError(f"SQLite WAL could not be enabled: {mode}")

        with self._immediate() as connection:
            revision = connection.execute(
                sql_text("SELECT MAX(revision) FROM catalog_revisions")
            ).scalar_one()
            if revision is None:
                connection.execute(
                    sql_text(
                        "INSERT INTO catalog_revisions "
                        "(revision, snapshot_id, created_at, package_json) "
                        "VALUES (1, :snapshot_id, :created_at, NULL)"
                    ),
                    {"snapshot_id": str(uuid4()), "created_at": _iso(_now())},
                )

    @contextmanager
    def _immediate(self) -> Iterator[Connection]:
        connection = self._engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_catalog(self) -> PinnedCatalog:
        with self._engine.connect() as connection:
            revision_row = connection.execute(
                sql_text(
                    "SELECT revision, snapshot_id FROM catalog_revisions "
                    "ORDER BY revision DESC LIMIT 1"
                )
            ).mappings().one()
            rows = connection.execute(
                sql_text(
                    "SELECT d.document_json FROM catalog_revision_skills r "
                    "JOIN skill_documents d ON d.skill_id=r.skill_id "
                    "AND d.version=r.version WHERE r.revision=:revision "
                    "ORDER BY r.skill_id"
                ),
                {"revision": int(revision_row["revision"])},
            ).scalars()
            skills = {
                skill.skill_id: skill
                for raw in rows
                for skill in (Skill.model_validate_json(cast(str, raw)),)
            }
        return PinnedCatalog.create(
            UUID(cast(str, revision_row["snapshot_id"])),
            int(revision_row["revision"]),
            skills,
        )

    def load_catalog_revision(self, revision: int) -> PinnedCatalog:
        with self._engine.connect() as connection:
            revision_row = connection.execute(
                sql_text(
                    "SELECT revision, snapshot_id FROM catalog_revisions "
                    "WHERE revision=:revision"
                ),
                {"revision": revision},
            ).mappings().one_or_none()
            if revision_row is None:
                raise ApplicationError(
                    "CATALOG_SNAPSHOT_NOT_FOUND",
                    "Pinned revision каталога больше недоступна.",
                    409,
                )
            rows = connection.execute(
                sql_text(
                    "SELECT d.document_json FROM catalog_revision_skills r "
                    "JOIN skill_documents d ON d.skill_id=r.skill_id "
                    "AND d.version=r.version WHERE r.revision=:revision "
                    "ORDER BY r.skill_id"
                ),
                {"revision": revision},
            ).scalars()
            skills = {
                skill.skill_id: skill
                for raw in rows
                for skill in (Skill.model_validate_json(cast(str, raw)),)
            }
        return PinnedCatalog.create(
            UUID(cast(str, revision_row["snapshot_id"])),
            int(revision_row["revision"]),
            skills,
        )

    def known_digest(self, skill_id: str, version: str) -> str | None:
        with self._engine.connect() as connection:
            value = connection.execute(
                sql_text(
                    "SELECT digest FROM skill_documents "
                    "WHERE skill_id=:skill_id AND version=:version"
                ),
                {"skill_id": skill_id, "version": version},
            ).scalar_one_or_none()
        return cast(str | None, value)

    def commit_catalog(
        self,
        *,
        expected_revision: int,
        skills: Mapping[str, Skill],
        package_json: str | None,
    ) -> PinnedCatalog:
        snapshot_id = uuid4()
        with self._immediate() as connection:
            actual_revision = int(
                connection.execute(
                    sql_text("SELECT MAX(revision) FROM catalog_revisions")
                ).scalar_one()
            )
            if actual_revision != expected_revision:
                raise CatalogConflictError(
                    "Каталог изменился параллельно; перечитайте revision и повторите."
                )
            new_revision = actual_revision + 1
            for skill in skills.values():
                existing = connection.execute(
                    sql_text(
                        "SELECT digest FROM skill_documents "
                        "WHERE skill_id=:skill_id AND version=:version"
                    ),
                    {"skill_id": skill.skill_id, "version": skill.version},
                ).scalar_one_or_none()
                if existing is not None and existing != skill.integrity.digest:
                    raise CatalogConflictError(
                        f"Версия {skill.skill_id}@{skill.version} уже известна "
                        "с другим digest."
                    )
                connection.execute(
                    sql_text(
                        "INSERT OR IGNORE INTO skill_documents "
                        "(skill_id, version, digest, document_json) VALUES "
                        "(:skill_id, :version, :digest, :document_json)"
                    ),
                    {
                        "skill_id": skill.skill_id,
                        "version": skill.version,
                        "digest": skill.integrity.digest,
                        "document_json": skill.model_dump_json(by_alias=True),
                    },
                )
            connection.execute(
                sql_text(
                    "INSERT INTO catalog_revisions "
                    "(revision, snapshot_id, created_at, package_json) VALUES "
                    "(:revision, :snapshot_id, :created_at, :package_json)"
                ),
                {
                    "revision": new_revision,
                    "snapshot_id": str(snapshot_id),
                    "created_at": _iso(_now()),
                    "package_json": package_json,
                },
            )
            for skill in sorted(skills.values(), key=lambda item: item.skill_id):
                connection.execute(
                    sql_text(
                        "INSERT INTO catalog_revision_skills "
                        "(revision, skill_id, version, digest) VALUES "
                        "(:revision, :skill_id, :version, :digest)"
                    ),
                    {
                        "revision": new_revision,
                        "skill_id": skill.skill_id,
                        "version": skill.version,
                        "digest": skill.integrity.digest,
                    },
                )
        return PinnedCatalog.create(snapshot_id, new_revision, skills)

    def create_session(self, title: str = "Новый диалог") -> SessionRecord:
        now = _now()
        session = SessionRecord(uuid4(), title, now, now, 1)
        with self._immediate() as connection:
            connection.execute(
                sql_text(
                    "INSERT INTO sessions "
                    "(session_id, title, created_at, updated_at, context_version) "
                    "VALUES (:session_id, :title, :created_at, :updated_at, 1)"
                ),
                {
                    "session_id": str(session.session_id),
                    "title": title,
                    "created_at": _iso(now),
                    "updated_at": _iso(now),
                },
            )
        return session

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql_text("SELECT * FROM sessions ORDER BY updated_at DESC")
            ).mappings()
            return tuple(self._session(row) for row in rows)

    def get_session(self, session_id: UUID) -> SessionRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sql_text("SELECT * FROM sessions WHERE session_id=:session_id"),
                {"session_id": str(session_id)},
            ).mappings().one_or_none()
        return None if row is None else self._session(row)

    def delete_session(self, session_id: UUID) -> bool:
        with self._immediate() as connection:
            result = connection.execute(
                sql_text("DELETE FROM sessions WHERE session_id=:session_id"),
                {"session_id": str(session_id)},
            )
        return bool(result.rowcount)

    def create_turn(
        self,
        *,
        session_id: UUID,
        text: str,
        client_message_id: str,
        expected_context_version: int,
    ) -> tuple[TurnRecord, bool]:
        with self._immediate() as connection:
            existing = connection.execute(
                sql_text(
                    "SELECT * FROM turns WHERE session_id=:session_id "
                    "AND client_message_id=:client_message_id"
                ),
                {
                    "session_id": str(session_id),
                    "client_message_id": client_message_id,
                },
            ).mappings().one_or_none()
            if existing is not None:
                return self._turn(existing), False
            current = connection.execute(
                sql_text(
                    "SELECT context_version FROM sessions WHERE session_id=:session_id"
                ),
                {"session_id": str(session_id)},
            ).scalar_one_or_none()
            if current is None:
                raise ApplicationError("SESSION_NOT_FOUND", "Сессия не найдена.", 404)
            if int(current) != expected_context_version:
                raise ApplicationError(
                    "CONTEXT_VERSION_CONFLICT",
                    "Контекст сессии изменился; перечитайте диалог и повторите.",
                    409,
                )
            now = _now()
            values = {
                "turn_id": str(uuid4()),
                "request_id": str(uuid4()),
                "trace_id": str(uuid4()),
                "session_id": str(session_id),
                "client_message_id": client_message_id,
                "user_text": text,
                "created_at": _iso(now),
                "context_version": expected_context_version,
            }
            connection.execute(
                sql_text(
                    "INSERT INTO turns (turn_id, request_id, trace_id, session_id, "
                    "client_message_id, user_text, status, created_at, context_version) "
                    "VALUES (:turn_id, :request_id, :trace_id, :session_id, "
                    ":client_message_id, :user_text, 'accepted', :created_at, "
                    ":context_version)"
                ),
                values,
            )
            row = connection.execute(
                sql_text("SELECT * FROM turns WHERE turn_id=:turn_id"),
                {"turn_id": values["turn_id"]},
            ).mappings().one()
            return self._turn(row), True

    def pin_turn(self, turn_id: UUID, catalog: PinnedCatalog) -> None:
        with self._immediate() as connection:
            result = connection.execute(
                sql_text(
                    "UPDATE turns SET catalog_snapshot_id=:snapshot_id, "
                    "catalog_revision=:revision, status='running' WHERE turn_id=:turn_id"
                ),
                {
                    "snapshot_id": str(catalog.snapshot_id),
                    "revision": catalog.revision,
                    "turn_id": str(turn_id),
                },
            )
            if not result.rowcount:
                raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)

    def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sql_text("SELECT * FROM turns WHERE turn_id=:turn_id"),
                {"turn_id": str(turn_id)},
            ).mappings().one_or_none()
        return None if row is None else self._turn(row)

    def list_turns(self, session_id: UUID) -> tuple[TurnRecord, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql_text(
                    "SELECT * FROM turns WHERE session_id=:session_id "
                    "ORDER BY created_at"
                ),
                {"session_id": str(session_id)},
            ).mappings()
            return tuple(self._turn(row) for row in rows)

    def recent_user_messages(
        self, session_id: UUID, limit: int, *, exclude_turn_id: UUID | None = None
    ) -> tuple[str, ...]:
        with self._engine.connect() as connection:
            values = connection.execute(
                sql_text(
                    "SELECT user_text FROM turns WHERE session_id=:session_id "
                    "AND (:exclude_turn_id IS NULL OR turn_id<>:exclude_turn_id) "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {
                    "session_id": str(session_id),
                    "limit": limit,
                    "exclude_turn_id": (
                        None if exclude_turn_id is None else str(exclude_turn_id)
                    ),
                },
            ).scalars()
            return tuple(reversed(tuple(cast(str, value) for value in values)))

    def context_facts(self, session_id: UUID) -> tuple[ContextFact, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql_text(
                    "SELECT * FROM context_facts WHERE session_id=:session_id "
                    "ORDER BY created_at"
                ),
                {"session_id": str(session_id)},
            ).mappings()
            return tuple(self._restore_context_fact(connection, row) for row in rows)

    def _restore_context_fact(
        self, connection: Connection, row: RowMapping
    ) -> ContextFact:
        origin_turn_id = UUID(cast(str, row["origin_turn_id"]))
        origin_fact_instance_id = UUID(cast(str, row["origin_fact_instance_id"]))
        turn = connection.execute(
            sql_text(
                "SELECT evidence_json, catalog_revision FROM turns "
                "WHERE turn_id=:turn_id"
            ),
            {"turn_id": str(origin_turn_id)},
        ).mappings().one_or_none()
        if (
            turn is None
            or turn["evidence_json"] is None
            or turn["catalog_revision"] is None
        ):
            raise _context_provenance_error()
        try:
            evidence = EvidenceBundle.model_validate_json(
                cast(str, turn["evidence_json"])
            )
        except ValueError as error:
            raise _context_provenance_error() from error
        fact = next(
            (
                candidate
                for candidate in evidence.facts
                if candidate.fact_instance_id == origin_fact_instance_id
            ),
            None,
        )
        exported = next(
            (
                candidate
                for candidate in evidence.context_exports
                if candidate.context_handle == row["handle"]
                and candidate.fact_instance_id == origin_fact_instance_id
            ),
            None,
        )
        if fact is None or exported is None or not isinstance(fact.value, EntityRef):
            raise _context_provenance_error()
        step = next(
            (candidate for candidate in evidence.steps if candidate.step_id == fact.step_id),
            None,
        )
        if step is None or not step.operation_ref.startswith("skill://"):
            raise _context_provenance_error()
        try:
            skill_id, skill_version = step.operation_ref.removeprefix(
                "skill://"
            ).rsplit("/", 1)
        except ValueError as error:
            raise _context_provenance_error() from error
        snapshot_skill = next(
            (
                candidate
                for candidate in evidence.catalog_snapshot.skills
                if candidate.skill_id == skill_id and candidate.version == skill_version
            ),
            None,
        )
        skill_row = connection.execute(
            sql_text(
                "SELECT d.document_json, r.digest FROM catalog_revision_skills r "
                "JOIN skill_documents d ON d.skill_id=r.skill_id "
                "AND d.version=r.version WHERE r.revision=:revision "
                "AND r.skill_id=:skill_id AND r.version=:version"
            ),
            {
                "revision": int(turn["catalog_revision"]),
                "skill_id": skill_id,
                "version": skill_version,
            },
        ).mappings().one_or_none()
        if skill_row is None or snapshot_skill is None:
            raise _context_provenance_error()
        try:
            skill = Skill.model_validate_json(cast(str, skill_row["document_json"]))
        except ValueError as error:
            raise _context_provenance_error() from error
        if (
            skill.integrity.digest != skill_row["digest"]
            or skill.integrity.digest != snapshot_skill.digest
            or not isinstance(skill.operation, DataQueryOperation)
        ):
            raise _context_provenance_error()
        definition = next(
            (
                item
                for item in skill.output_contract.facts
                if item.fact_id == fact.fact_id
            ),
            None,
        )
        bindings = [
            binding
            for binding in skill.operation.column_bindings
            if binding.fact_id == fact.fact_id
            and binding.column == fact.source_locator.reference
        ]
        if (
            definition is None
            or definition.semantic_type != fact.semantic_type
            or definition.value_type is not FactValueType.ENTITY_REF
            or len(bindings) != 1
            or bindings[0].converter != "object_ref"
            or fact.value.object_type not in bindings[0].accepted_mcp_types
        ):
            raise _context_provenance_error()
        binding = bindings[0]
        origin = EntityFactOrigin(
            fact=fact,
            skill_id=skill.skill_id,
            skill_version=skill.version,
            skill_digest=skill.integrity.digest,
            column=binding.column,
            accepted_mcp_types=binding.accepted_mcp_types,
        )
        try:
            return ContextFact(
                handle=cast(str, row["handle"]),
                semantic_type=cast(str, row["semantic_type"]),
                value=cast(
                    JsonValue, json.loads(cast(str, row["value_json"]))
                ),
                presentation=cast(str, row["presentation"]),
                origin_turn_id=origin_turn_id,
                origin_fact_instance_id=origin_fact_instance_id,
                origin=origin,
            )
        except ValueError as error:
            raise _context_provenance_error() from error

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
    ) -> TurnRecord:
        completed_at = _now()
        with self._immediate() as connection:
            turn = connection.execute(
                sql_text("SELECT * FROM turns WHERE turn_id=:turn_id"),
                {"turn_id": str(turn_id)},
            ).mappings().one_or_none()
            if turn is None:
                raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)
            connection.execute(
                sql_text(
                    "UPDATE turns SET assistant_text=:assistant_text, status=:status, "
                    "outcome=:outcome, completed_at=:completed_at, plan_json=:plan_json, "
                    "evidence_json=:evidence_json, error_code=:error_code "
                    "WHERE turn_id=:turn_id"
                ),
                {
                    "assistant_text": assistant_text,
                    "status": status,
                    "outcome": outcome,
                    "completed_at": _iso(completed_at),
                    "plan_json": plan_json,
                    "evidence_json": evidence_json,
                    "error_code": error_code,
                    "turn_id": str(turn_id),
                },
            )
            session_id = cast(str, turn["session_id"])
            for fact in context_exports:
                if fact.origin_turn_id != turn_id:
                    raise ValueError("context export must originate from completed turn")
                connection.execute(
                    sql_text(
                        "INSERT INTO context_facts (handle, session_id, semantic_type, "
                        "value_json, presentation, origin_turn_id, "
                        "origin_fact_instance_id, created_at) VALUES "
                        "(:handle, :session_id, :semantic_type, :value_json, "
                        ":presentation, :origin_turn_id, :origin_fact_instance_id, "
                        ":created_at)"
                    ),
                    {
                        "handle": fact.handle,
                        "session_id": session_id,
                        "semantic_type": fact.semantic_type,
                        "value_json": _json(fact.value),
                        "presentation": fact.presentation,
                        "origin_turn_id": str(fact.origin_turn_id),
                        "origin_fact_instance_id": str(
                            fact.origin_fact_instance_id
                        ),
                        "created_at": _iso(completed_at),
                    },
                )
            connection.execute(
                sql_text(
                    "UPDATE sessions SET context_version=context_version+1, "
                    "updated_at=:updated_at WHERE session_id=:session_id"
                ),
                {"updated_at": _iso(completed_at), "session_id": session_id},
            )
            completed = connection.execute(
                sql_text("SELECT * FROM turns WHERE turn_id=:turn_id"),
                {"turn_id": str(turn_id)},
            ).mappings().one()
        return self._turn(completed)

    def append_event(
        self,
        turn_id: UUID,
        event_name: str,
        status: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> TurnEvent:
        now = _now()
        data = dict(payload or {})
        with self._immediate() as connection:
            sequence = int(
                connection.execute(
                    sql_text(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turn_events "
                        "WHERE turn_id=:turn_id"
                    ),
                    {"turn_id": str(turn_id)},
                ).scalar_one()
            )
            connection.execute(
                sql_text(
                    "INSERT INTO turn_events (turn_id, sequence, event_name, "
                    "timestamp, status, payload_json) VALUES (:turn_id, :sequence, "
                    ":event_name, :timestamp, :status, :payload_json)"
                ),
                {
                    "turn_id": str(turn_id),
                    "sequence": sequence,
                    "event_name": event_name,
                    "timestamp": _iso(now),
                    "status": status,
                    "payload_json": _json(data),
                },
            )
        return TurnEvent(turn_id, sequence, event_name, now, status, data)

    def events(self, turn_id: UUID, after: int = 0) -> tuple[TurnEvent, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql_text(
                    "SELECT * FROM turn_events WHERE turn_id=:turn_id "
                    "AND sequence>:after ORDER BY sequence"
                ),
                {"turn_id": str(turn_id), "after": after},
            ).mappings()
            return tuple(
                TurnEvent(
                    turn_id=turn_id,
                    sequence=int(row["sequence"]),
                    event_name=cast(str, row["event_name"]),
                    timestamp=_datetime(row["timestamp"]),
                    status=cast(str, row["status"]),
                    payload=cast(
                        dict[str, JsonValue],
                        json.loads(cast(str, row["payload_json"])),
                    ),
                )
                for row in rows
            )

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
    ) -> PageContinuation:
        created_at = _now()
        expires_at = created_at + timedelta(minutes=30)
        handle = f"page_{secrets.token_urlsafe(24)}"
        arguments_bytes = canonicalize(dict(arguments))
        arguments_json = arguments_bytes.decode("utf-8")
        normalized_params_digest = hashlib.sha256(arguments_bytes).hexdigest()
        with self._immediate() as connection:
            connection.execute(
                sql_text(
                    "INSERT INTO page_continuations (handle, session_id, "
                    "origin_turn_id, step_id, skill_id, skill_version, skill_digest, "
                    "catalog_snapshot_id, catalog_revision, normalized_params_digest, "
                    "arguments_json, plan_json, strategy, page_size, shown, "
                    "database_marker, sort_tuple_json, cursor_values_json, created_at, "
                    "expires_at, consumed_at) VALUES (:handle, :session_id, "
                    ":origin_turn_id, :step_id, :skill_id, :skill_version, "
                    ":skill_digest, :catalog_snapshot_id, :catalog_revision, "
                    ":normalized_params_digest, :arguments_json, :plan_json, "
                    ":strategy, :page_size, :shown, :database_marker, "
                    ":sort_tuple_json, :cursor_values_json, :created_at, "
                    ":expires_at, NULL)"
                ),
                {
                    "handle": handle,
                    "session_id": str(session_id),
                    "origin_turn_id": str(origin_turn_id),
                    "step_id": step_id,
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "skill_digest": skill_digest,
                    "catalog_snapshot_id": str(catalog_snapshot_id),
                    "catalog_revision": catalog_revision,
                    "normalized_params_digest": normalized_params_digest,
                    "arguments_json": arguments_json,
                    "plan_json": plan_json,
                    "strategy": strategy,
                    "page_size": page_size,
                    "shown": shown,
                    "database_marker": database_marker,
                    "sort_tuple_json": _json(list(sort_tuple)),
                    "cursor_values_json": _json(dict(cursor_values)),
                    "created_at": _iso(created_at),
                    "expires_at": _iso(expires_at),
                },
            )
        return PageContinuation(
            handle=handle,
            session_id=session_id,
            origin_turn_id=origin_turn_id,
            step_id=step_id,
            skill_id=skill_id,
            skill_version=skill_version,
            skill_digest=skill_digest,
            catalog_snapshot_id=catalog_snapshot_id,
            catalog_revision=catalog_revision,
            normalized_params_digest=normalized_params_digest,
            arguments=dict(arguments),
            plan_json=plan_json,
            strategy=strategy,
            page_size=page_size,
            shown=shown,
            database_marker=database_marker,
            sort_tuple=tuple(sort_tuple),
            cursor_values=dict(cursor_values),
            created_at=created_at,
            expires_at=expires_at,
        )

    def claim_continuation(
        self,
        handle: str,
        *,
        session_id: UUID,
        active_catalog: PinnedCatalog,
        database_marker: str,
    ) -> tuple[PageContinuation, TurnRecord]:
        if _PAGE_HANDLE.fullmatch(handle) is None:
            raise ApplicationError(
                "CONTINUATION_HANDLE_INVALID",
                "Continuation handle имеет неверный формат.",
                422,
            )
        now = _now()
        with self._immediate() as connection:
            row = connection.execute(
                sql_text(
                    "SELECT * FROM page_continuations WHERE handle=:handle"
                ),
                {"handle": handle},
            ).mappings().one_or_none()
            if row is None:
                raise ApplicationError(
                    "CONTINUATION_NOT_FOUND", "Продолжение списка не найдено.", 404
                )
            continuation = self._continuation(row)
            if continuation.session_id != session_id:
                raise ApplicationError(
                    "CONTINUATION_SESSION_MISMATCH",
                    "Продолжение принадлежит другой сессии.",
                    409,
                )
            if continuation.consumed_at is not None:
                raise ApplicationError(
                    "CONTINUATION_CONSUMED",
                    "Продолжение уже было использовано.",
                    409,
                )
            if continuation.expires_at <= now:
                raise ApplicationError(
                    "CONTINUATION_EXPIRED",
                    "Срок действия продолжения истек; выполните запрос заново.",
                    410,
                )
            arguments_digest = hashlib.sha256(
                canonicalize(dict(continuation.arguments))
            ).hexdigest()
            active_skill = active_catalog.skills.get(continuation.skill_id)
            catalog_changed = (
                continuation.catalog_snapshot_id != active_catalog.snapshot_id
                or continuation.catalog_revision != active_catalog.revision
                or active_skill is None
                or active_skill.version != continuation.skill_version
                or active_skill.integrity.digest != continuation.skill_digest
                or arguments_digest != continuation.normalized_params_digest
            )
            if active_skill is not None and not _continuation_contract_matches(
                continuation, active_skill
            ):
                catalog_changed = True
            if catalog_changed:
                raise ApplicationError(
                    "CONTINUATION_CATALOG_CHANGED",
                    "Каталог навыков изменился; выполните исходный запрос заново.",
                    409,
                )
            if continuation.database_marker != database_marker:
                raise ApplicationError(
                    "CONTINUATION_MARKER_CHANGED",
                    "Состояние базы изменилось; выполните исходный запрос заново.",
                    409,
                )
            context_version = connection.execute(
                sql_text(
                    "SELECT context_version FROM sessions WHERE session_id=:session_id"
                ),
                {"session_id": str(session_id)},
            ).scalar_one_or_none()
            if context_version is None:
                raise ApplicationError("SESSION_NOT_FOUND", "Диалог не найден.", 404)
            turn_id = uuid4()
            request_id = uuid4()
            trace_id = uuid4()
            connection.execute(
                sql_text(
                    "INSERT INTO turns (turn_id, request_id, trace_id, session_id, "
                    "client_message_id, user_text, status, created_at, context_version, "
                    "catalog_snapshot_id, catalog_revision) VALUES (:turn_id, "
                    ":request_id, :trace_id, :session_id, :client_message_id, "
                    ":user_text, 'accepted', :created_at, :context_version, "
                    ":catalog_snapshot_id, :catalog_revision)"
                ),
                {
                    "turn_id": str(turn_id),
                    "request_id": str(request_id),
                    "trace_id": str(trace_id),
                    "session_id": str(session_id),
                    "client_message_id": f"continuation:{handle}",
                    "user_text": "Показать следующую страницу",
                    "created_at": _iso(now),
                    "context_version": int(context_version),
                    "catalog_snapshot_id": str(active_catalog.snapshot_id),
                    "catalog_revision": active_catalog.revision,
                },
            )
            updated = connection.execute(
                sql_text(
                    "UPDATE page_continuations SET consumed_at=:consumed_at, "
                    "accepted_turn_id=:accepted_turn_id "
                    "WHERE handle=:handle AND consumed_at IS NULL"
                ),
                {
                    "handle": handle,
                    "consumed_at": _iso(now),
                    "accepted_turn_id": str(turn_id),
                },
            )
            if updated.rowcount != 1:
                raise ApplicationError(
                    "CONTINUATION_CONSUMED",
                    "Продолжение уже было использовано.",
                    409,
                )
            turn_row = connection.execute(
                sql_text("SELECT * FROM turns WHERE turn_id=:turn_id"),
                {"turn_id": str(turn_id)},
            ).mappings().one()
        claimed = replace(
            continuation, consumed_at=now, accepted_turn_id=turn_id
        )
        return claimed, self._turn(turn_row)

    def get_continuation(self, handle: str) -> PageContinuation | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sql_text("SELECT * FROM page_continuations WHERE handle=:handle"),
                {"handle": handle},
            ).mappings().one_or_none()
        return None if row is None else self._continuation(row)

    def continuation_for_turn(self, turn_id: UUID) -> PageContinuation | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sql_text(
                    "SELECT * FROM page_continuations WHERE accepted_turn_id=:turn_id"
                ),
                {"turn_id": str(turn_id)},
            ).mappings().one_or_none()
        return None if row is None else self._continuation(row)

    def preview_clear(self, scopes: Sequence[str]) -> MaintenancePreview:
        normalized = _normalize_clear_scopes(scopes)
        token = f"clear_{secrets.token_urlsafe(24)}"
        issued_at = _now()
        expires_at = issued_at + timedelta(minutes=5)
        with self._immediate() as connection:
            preview = self._clear_preview(
                connection,
                normalized,
                token=token,
                issued_at=issued_at,
                expires_at=expires_at,
            )
            connection.execute(
                sql_text(
                    "INSERT INTO maintenance_previews "
                    "(token, scopes_json, counts_json, target_fingerprint, "
                    "issued_at, expires_at, consumed_at) VALUES (:token, "
                    ":scopes_json, :counts_json, :target_fingerprint, :issued_at, "
                    ":expires_at, NULL)"
                ),
                {
                    "token": token,
                    "scopes_json": _json(list(normalized)),
                    "counts_json": _json(dict(preview.counts)),
                    "target_fingerprint": preview.target_fingerprint,
                    "issued_at": _iso(issued_at),
                    "expires_at": _iso(expires_at),
                },
            )
        return preview

    def confirm_clear(
        self, confirmation_token: str, scopes: Sequence[str]
    ) -> MaintenancePreview:
        normalized = _normalize_clear_scopes(scopes)
        now = _now()
        with self._immediate() as connection:
            row = connection.execute(
                sql_text(
                    "SELECT * FROM maintenance_previews WHERE token=:token"
                ),
                {"token": confirmation_token},
            ).mappings().one_or_none()
            if row is None:
                raise ApplicationError(
                    "CLEAR_CONFIRMATION_NOT_FOUND",
                    "Confirmation token не найден.",
                    404,
                )
            if row["consumed_at"] is not None:
                raise ApplicationError(
                    "CLEAR_CONFIRMATION_CONSUMED",
                    "Confirmation token уже использован.",
                    409,
                )
            if _datetime(row["expires_at"]) <= now:
                raise ApplicationError(
                    "CLEAR_CONFIRMATION_EXPIRED",
                    "Confirmation token истек; запросите новый preview.",
                    410,
                )
            scopes_value = json.loads(cast(str, row["scopes_json"]))
            counts_value = json.loads(cast(str, row["counts_json"]))
            if not isinstance(scopes_value, list) or not isinstance(counts_value, dict):
                raise RuntimeError("stored maintenance token is invalid")
            stored_scopes = _MAINTENANCE_SCOPES_ADAPTER.validate_python(scopes_value)
            if stored_scopes != normalized:
                raise ApplicationError(
                    "CLEAR_SCOPE_MISMATCH",
                    "Scopes confirm не совпадают с preview.",
                    409,
                )
            current = self._clear_preview(
                connection,
                normalized,
                token=confirmation_token,
                issued_at=_datetime(row["issued_at"]),
                expires_at=_datetime(row["expires_at"]),
            )
            expected_counts = {
                "sessions": int(counts_value.get("sessions", -1)),
                "traces": int(counts_value.get("traces", -1)),
                "raw_payloads": int(counts_value.get("raw_payloads", -1)),
            }
            if (
                current.target_fingerprint != row["target_fingerprint"]
                or dict(current.counts) != expected_counts
            ):
                raise ApplicationError(
                    "CLEAR_PREVIEW_STALE",
                    "Target set изменился после preview; повторите preview.",
                    409,
                )
            self._delete_clear_target(connection, normalized)
            connection.execute(
                sql_text(
                    "UPDATE maintenance_previews SET consumed_at=:consumed_at "
                    "WHERE token=:token"
                ),
                {"consumed_at": _iso(now), "token": confirmation_token},
            )
        return replace(current, consumed_at=now)

    def _clear_preview(
        self,
        connection: Connection,
        scopes: tuple[MaintenanceScope, ...],
        *,
        token: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> MaintenancePreview:
        session_ids = (
            tuple(
                cast(str, value)
                for value in connection.execute(
                    sql_text("SELECT session_id FROM sessions ORDER BY session_id")
                ).scalars()
            )
            if "sessions" in scopes
            else ()
        )
        if "sessions" in scopes or "traces" in scopes:
            trace_ids = {
                cast(str, value)
                for value in connection.execute(
                    sql_text("SELECT trace_id FROM turns ORDER BY trace_id")
                ).scalars()
            }
            if "traces" in scopes:
                trace_ids.update(
                    cast(str, value)
                    for value in connection.execute(
                        sql_text(
                            "SELECT DISTINCT trace_id FROM trace_artifacts ORDER BY trace_id"
                        )
                    ).scalars()
                )
        else:
            trace_ids = set()

        raw_filter = self._clear_artifact_filter(scopes)
        raw_keys = tuple(
            (cast(str, row["trace_id"]), cast(str, row["name"]))
            for row in connection.execute(
                sql_text(
                    "SELECT trace_id, name FROM trace_artifacts WHERE "
                    f"({raw_filter}) AND ({_RAW_PAYLOAD_PREDICATE}) "
                    "ORDER BY trace_id, name"
                )
            ).mappings()
        )
        active_count = int(
            connection.execute(
                sql_text(
                    "SELECT COUNT(*) FROM turns WHERE status NOT IN "
                    "('completed', 'failed', 'interrupted') AND ("
                    + self._clear_turn_filter(scopes)
                    + ")"
                )
            ).scalar_one()
        )
        if active_count:
            raise ApplicationError(
                "CLEAR_TARGET_ACTIVE",
                "Очистка затрагивает незавершенный turn.",
                409,
            )

        fingerprint_payload = {
            "scopes": list(scopes),
            "sessions": list(session_ids),
            "turns": list(
                connection.execute(
                    sql_text(
                        "SELECT turn_id FROM turns WHERE "
                        + self._clear_turn_filter(scopes)
                        + " ORDER BY turn_id"
                    )
                ).scalars()
            ),
            "events": [
                [cast(str, row["turn_id"]), int(row["sequence"])]
                for row in connection.execute(
                    sql_text(
                        "SELECT e.turn_id, e.sequence FROM turn_events e JOIN turns t "
                        "ON t.turn_id=e.turn_id WHERE "
                        + self._clear_turn_filter(scopes, turn_alias="t")
                        + " ORDER BY e.turn_id, e.sequence"
                    )
                ).mappings()
            ],
            "context": list(
                connection.execute(
                    sql_text(
                        "SELECT handle FROM context_facts WHERE "
                        + ("1=1" if "sessions" in scopes else "1=0")
                        + " ORDER BY handle"
                    )
                ).scalars()
            ),
            "continuations": list(
                connection.execute(
                    sql_text(
                        "SELECT handle FROM page_continuations WHERE "
                        + ("1=1" if "sessions" in scopes else "1=0")
                        + " ORDER BY handle"
                    )
                ).scalars()
            ),
            "traces": sorted(trace_ids),
            "artifacts": [
                [cast(str, row["trace_id"]), cast(str, row["name"])]
                for row in connection.execute(
                    sql_text(
                        "SELECT trace_id, name FROM trace_artifacts WHERE "
                        + raw_filter
                        + " ORDER BY trace_id, name"
                    )
                ).mappings()
            ],
        }
        target_fingerprint = hashlib.sha256(
            canonicalize(fingerprint_payload)
        ).hexdigest()
        return MaintenancePreview(
            confirmation_token=token,
            scopes=scopes,
            session_count=len(session_ids),
            trace_count=len(trace_ids),
            raw_payload_count=len(raw_keys),
            target_fingerprint=target_fingerprint,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    @staticmethod
    def _clear_turn_filter(
        scopes: tuple[MaintenanceScope, ...], *, turn_alias: str = "turns"
    ) -> str:
        if "sessions" in scopes or "traces" in scopes:
            return "1=1"
        if "raw_payloads" in scopes:
            return (
                f"{turn_alias}.trace_id IN (SELECT trace_id FROM trace_artifacts "
                f"WHERE {_RAW_PAYLOAD_PREDICATE})"
            )
        return "1=0"

    @staticmethod
    def _clear_artifact_filter(scopes: tuple[MaintenanceScope, ...]) -> str:
        if "traces" in scopes:
            return "1=1"
        terms: list[str] = []
        if "sessions" in scopes:
            terms.append("trace_id IN (SELECT trace_id FROM turns)")
        if "raw_payloads" in scopes:
            terms.append(f"({_RAW_PAYLOAD_PREDICATE})")
        return " OR ".join(terms) or "1=0"

    def _delete_clear_target(
        self, connection: Connection, scopes: tuple[MaintenanceScope, ...]
    ) -> None:
        artifact_filter = self._clear_artifact_filter(scopes)
        connection.execute(
            sql_text("DELETE FROM trace_artifacts WHERE " + artifact_filter)
        )
        if "traces" in scopes and "sessions" not in scopes:
            connection.execute(
                sql_text("UPDATE turns SET plan_json=NULL, evidence_json=NULL")
            )
        if "sessions" in scopes:
            connection.execute(sql_text("DELETE FROM sessions"))

    @staticmethod
    def _continuation(row: RowMapping) -> PageContinuation:
        arguments = _JSON_OBJECT_ADAPTER.validate_json(
            cast(str, row["arguments_json"])
        )
        sort_tuple = _JSON_TUPLE_ADAPTER.validate_json(
            cast(str, row["sort_tuple_json"])
        )
        cursor_values = _JSON_OBJECT_ADAPTER.validate_json(
            cast(str, row["cursor_values_json"])
        )
        strategy_raw = cast(str, row["strategy"])
        if strategy_raw not in {"prefix", "keyset"}:
            raise RuntimeError("stored page continuation strategy is invalid")
        strategy = cast(PageStrategy, strategy_raw)
        return PageContinuation(
            handle=cast(str, row["handle"]),
            session_id=UUID(cast(str, row["session_id"])),
            origin_turn_id=UUID(cast(str, row["origin_turn_id"])),
            step_id=cast(str, row["step_id"]),
            skill_id=cast(str, row["skill_id"]),
            skill_version=cast(str, row["skill_version"]),
            skill_digest=cast(str, row["skill_digest"]),
            catalog_snapshot_id=UUID(cast(str, row["catalog_snapshot_id"])),
            catalog_revision=int(row["catalog_revision"]),
            normalized_params_digest=cast(str, row["normalized_params_digest"]),
            arguments=arguments,
            plan_json=cast(str, row["plan_json"]),
            strategy=strategy,
            page_size=int(row["page_size"]),
            shown=int(row["shown"]),
            database_marker=cast(str, row["database_marker"]),
            sort_tuple=sort_tuple,
            cursor_values=cursor_values,
            created_at=_datetime(row["created_at"]),
            expires_at=_datetime(row["expires_at"]),
            consumed_at=(
                None if row["consumed_at"] is None else _datetime(row["consumed_at"])
            ),
            accepted_turn_id=(
                None
                if row["accepted_turn_id"] is None
                else UUID(cast(str, row["accepted_turn_id"]))
            ),
        )

    def put_artifact(self, trace_id: UUID, name: str, content: bytes) -> None:
        with self._immediate() as connection:
            connection.execute(
                sql_text(
                    "INSERT INTO trace_artifacts (trace_id, name, content) "
                    "VALUES (:trace_id, :name, :content) "
                    "ON CONFLICT(trace_id, name) DO UPDATE SET content=excluded.content"
                ),
                {"trace_id": str(trace_id), "name": name, "content": content},
            )

    def artifacts(self, trace_id: UUID) -> Mapping[str, bytes]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql_text(
                    "SELECT name, content FROM trace_artifacts "
                    "WHERE trace_id=:trace_id ORDER BY name"
                ),
                {"trace_id": str(trace_id)},
            ).mappings()
            return {
                cast(str, row["name"]): cast(bytes, row["content"]) for row in rows
            }

    @staticmethod
    def _session(row: RowMapping) -> SessionRecord:
        return SessionRecord(
            session_id=UUID(cast(str, row["session_id"])),
            title=cast(str, row["title"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            context_version=int(row["context_version"]),
        )

    @staticmethod
    def _turn(row: RowMapping) -> TurnRecord:
        snapshot_id = cast(str | None, row["catalog_snapshot_id"])
        completed_at = row["completed_at"]
        return TurnRecord(
            turn_id=UUID(cast(str, row["turn_id"])),
            request_id=UUID(cast(str, row["request_id"])),
            trace_id=UUID(cast(str, row["trace_id"])),
            session_id=UUID(cast(str, row["session_id"])),
            client_message_id=cast(str, row["client_message_id"]),
            user_text=cast(str, row["user_text"]),
            assistant_text=cast(str | None, row["assistant_text"]),
            status=cast(str, row["status"]),
            outcome=cast(str | None, row["outcome"]),
            created_at=_datetime(row["created_at"]),
            completed_at=None if completed_at is None else _datetime(completed_at),
            context_version=int(row["context_version"]),
            catalog_snapshot_id=None if snapshot_id is None else UUID(snapshot_id),
            catalog_revision=cast(int | None, row["catalog_revision"]),
            plan_json=cast(str | None, row["plan_json"]),
            evidence_json=cast(str | None, row["evidence_json"]),
            error_code=cast(str | None, row["error_code"]),
        )


def _continuation_contract_matches(
    continuation: PageContinuation, skill: Skill
) -> bool:
    operation = skill.operation
    if not isinstance(operation, DataQueryOperation):
        return False
    try:
        plan = PlannerOutput.model_validate_json(continuation.plan_json)
    except ValueError:
        return False
    if (
        not isinstance(plan.result, ExecuteResult)
        or plan.catalog_snapshot_id != continuation.catalog_snapshot_id
        or plan.catalog_revision != continuation.catalog_revision
    ):
        return False
    calls = [
        step
        for step in plan.result.steps
        if isinstance(step, SkillCall) and step.step_id == continuation.step_id
    ]
    if (
        len(calls) != 1
        or calls[0].skill_id != continuation.skill_id
        or calls[0].skill_version != continuation.skill_version
        or operation.pagination.strategy != continuation.strategy
        or continuation.page_size < 1
        or continuation.page_size + 1 > operation.query_template.mcp_limit.maximum
        or continuation.shown < continuation.page_size
    ):
        return False
    pagination = operation.pagination
    if isinstance(pagination, PrefixPagination):
        return (
            len(continuation.sort_tuple)
            == len(pagination.stable_order_fact_ids)
            and not continuation.cursor_values
            and continuation.shown <= pagination.maximum_total
        )
    if isinstance(pagination, KeysetPagination):
        return (
            len(continuation.sort_tuple) == len(pagination.sort)
            and set(continuation.cursor_values)
            == {
                binding.query_parameter
                for binding in pagination.cursor_bindings
            }
        )
    return False


def _context_provenance_error() -> ApplicationError:
    return ApplicationError(
        "ENTITY_BINDING_PROVENANCE_MISSING",
        "Сохраненный entity context не имеет проверяемого producer provenance.",
        409,
    )


def _normalize_clear_scopes(
    scopes: Sequence[str],
) -> tuple[MaintenanceScope, ...]:
    raw = tuple(scopes)
    if (
        not raw
        or len(raw) != len(set(raw))
        or not set(raw) <= set(_CLEAR_SCOPES)
    ):
        raise ApplicationError(
            "CLEAR_SCOPES_INVALID",
            "Scopes должны быть непустым уникальным подмножеством sessions, traces, raw_payloads.",
            422,
        )
    typed = _MAINTENANCE_SCOPES_ADAPTER.validate_python(raw)
    return canonical_maintenance_scopes(typed)
