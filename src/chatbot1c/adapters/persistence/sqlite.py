"""SQLite repositories with WAL and Alembic-managed schema upgrades."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from pydantic import JsonValue
from sqlalchemy import Engine, create_engine, event
from sqlalchemy import text as sql_text
from sqlalchemy.engine import Connection, RowMapping, make_url
from sqlalchemy.pool import StaticPool

from chatbot1c.application.errors import ApplicationError, CatalogConflictError
from chatbot1c.application.models import (
    ContextFact,
    EntityFactOrigin,
    PinnedCatalog,
    SessionRecord,
    TurnEvent,
    TurnRecord,
)
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.skill import DataQueryOperation, FactValueType, Skill
from chatbot1c.domain.types import EntityRef


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


def _context_provenance_error() -> ApplicationError:
    return ApplicationError(
        "ENTITY_BINDING_PROVENANCE_MISSING",
        "Сохраненный entity context не имеет проверяемого producer provenance.",
        409,
    )
