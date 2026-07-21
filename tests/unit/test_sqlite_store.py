from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from chatbot1c.adapters.persistence import SQLiteStore
from chatbot1c.application.errors import ApplicationError


def test_clean_database_repeat_startup_wal_and_create_turn(tmp_path: Path) -> None:
    store = SQLiteStore(f"sqlite:///{tmp_path / 'state.sqlite3'}")
    store.initialize()
    store.initialize()

    session = store.create_session("Проверка SQLite")
    turn, created = store.create_turn(
        session_id=session.session_id,
        text="Проверочный вопрос",
        client_message_id="sqlite-message-0001",
        expected_context_version=1,
    )
    assert created
    assert store.get_turn(turn.turn_id) == turn
    with store.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0003_slice2"
        )
    store.engine.dispose()


def test_upgrade_from_0002_preserves_existing_dialogue_rows(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'upgrade.sqlite3'}"
    store = SQLiteStore(database_url)
    migration_config = Config()
    migration_config.set_main_option(
        "script_location",
        str(
            Path(__file__).parents[2]
            / "src/chatbot1c/adapters/persistence/migrations"
        ),
    )
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "0002_context_fact_origin")

    session_id = uuid4()
    turn_id = uuid4()
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO catalog_revisions "
                "(revision, snapshot_id, created_at, package_json) "
                "VALUES (1, :snapshot_id, :created_at, NULL)"
            ),
            {"snapshot_id": str(uuid4()), "created_at": "2026-07-21T09:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(session_id, title, created_at, updated_at, context_version) "
                "VALUES (:session_id, 'До миграции', :created_at, :created_at, 1)"
            ),
            {"session_id": str(session_id), "created_at": "2026-07-21T09:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO turns (turn_id, request_id, trace_id, session_id, "
                "client_message_id, user_text, status, created_at, context_version) "
                "VALUES (:turn_id, :request_id, :trace_id, :session_id, "
                "'before-migration', 'Сохраненный вопрос', 'completed', "
                ":created_at, 1)"
            ),
            {
                "turn_id": str(turn_id),
                "request_id": str(uuid4()),
                "trace_id": str(uuid4()),
                "session_id": str(session_id),
                "created_at": "2026-07-21T09:00:00+00:00",
            },
        )

    store.initialize()

    assert store.get_session(session_id) is not None
    restored = store.get_turn(turn_id)
    assert restored is not None
    assert restored.user_text == "Сохраненный вопрос"
    with store.engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0003_slice2"
        assert connection.execute(
            text("SELECT COUNT(*) FROM page_continuations")
        ).scalar_one() == 0
    store.engine.dispose()


def test_in_memory_store_uses_one_static_database_across_connections() -> None:
    store = SQLiteStore("sqlite:///:memory:")
    store.initialize()
    session = store.create_session()
    turn, _ = store.create_turn(
        session_id=session.session_id,
        text="Проверка памяти",
        client_message_id="memory-message-0001",
        expected_context_version=1,
    )
    assert store.get_session(session.session_id) == session
    assert store.get_turn(turn.turn_id) == turn
    store.engine.dispose()


def test_application_error_survives_immediate_rollback(tmp_path: Path) -> None:
    store = SQLiteStore(f"sqlite:///{tmp_path / 'rollback.sqlite3'}")
    store.initialize()
    session = store.create_session("До rollback")

    with pytest.raises(ApplicationError) as rejected:
        with store._immediate() as connection:  # noqa: SLF001
            connection.execute(
                text("UPDATE sessions SET title='После rollback' WHERE session_id=:id"),
                {"id": str(session.session_id)},
            )
            raise ApplicationError("ROLLBACK_PROBE", "Проверка rollback.", 409)

    assert rejected.value.code == "ROLLBACK_PROBE"
    restored = store.get_session(session.session_id)
    assert restored is not None
    assert restored.title == "До rollback"
    store.engine.dispose()
