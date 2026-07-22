from __future__ import annotations

import sqlite3

from .support import FixtureClient, RunningApp, context_slots


def test_corrupt_origin_evidence_invalidates_slot_without_breaking_session(
    app: RunningApp, fixture: FixtureClient
) -> None:
    assert app.api is not None
    fixture.configure("resolve_one", asset_count=1)
    session = app.api.create_session("Corrupt context")
    selected = app.api.ask(
        session["session_id"],
        "Найди один лазурный актив и покажи его контрольный снимок",
        context_version=session["context_version"],
    )
    assert context_slots(app.api.session(session["session_id"]))

    app.stop()
    database_path = app.data_dir / "chatbot1c.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE turns SET evidence_json=NULL WHERE turn_id=?",
            (selected["turn_id"],),
        )
        connection.commit()

    app.start()
    assert app.api is not None
    restored = app.api.session(session["session_id"])
    assert context_slots(restored) == []
    with sqlite3.connect(database_path) as connection:
        status, reason = connection.execute(
            "SELECT status,reason FROM context_slots WHERE session_id=?",
            (session["session_id"],),
        ).fetchone()
    assert (status, reason) == ("invalidated", "provenance_missing")
