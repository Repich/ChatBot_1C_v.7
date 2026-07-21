from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from chatbot1c.adapters.persistence import SQLiteStore


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
            "0002_context_fact_origin"
        )
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
