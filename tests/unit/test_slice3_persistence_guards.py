from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import (
    ClarificationResponse,
    PendingChoice,
    PendingClarificationDraft,
)
from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.domain.outcomes import Outcome


def test_clarification_claim_checks_latest_catalog_inside_transaction(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        Settings(app_data_dir=tmp_path, auto_import_builtin_skills=True),
        auto_import=True,
    )
    stale_catalog = runtime.catalog.pin()
    marker = runtime.marker.capture(stale_catalog)
    session = runtime.store.create_session()
    turn, _ = runtime.store.create_turn(
        session_id=session.session_id,
        text="Неоднозначный вопрос",
        client_message_id="catalog-race-origin",
        expected_context_version=1,
    )
    runtime.store.pin_turn(turn.turn_id, stale_catalog)
    draft = PendingClarificationDraft(
        kind="interpretation_choice",
        question_ru="Какой показатель выбрать?",
        original_question=turn.user_text,
        plan_json="{}",
        resolver_step_id=None,
        choices=(
            PendingChoice(
                choice_id="c1",
                label_ru="Количество",
                binding={
                    "source": "literal",
                    "value_type": "enum",
                    "value": "quantity",
                },
            ),
        ),
        has_more_candidates=False,
        catalog_snapshot_id=stale_catalog.snapshot_id,
        catalog_revision=stale_catalog.revision,
        database_marker=marker.digest,
    )
    runtime.store.complete_turn(
        turn_id=turn.turn_id,
        assistant_text=draft.question_ru,
        status="completed",
        outcome=Outcome.CLARIFICATION_REQUIRED,
        plan_json="{}",
        evidence_json=None,
        context_exports=(),
        pending_clarification=draft,
    )
    pending = runtime.store.active_pending(session.session_id)
    assert pending is not None

    runtime.store.commit_catalog(
        expected_revision=stale_catalog.revision,
        skills=stale_catalog.skills,
        package_json=None,
    )

    with pytest.raises(ApplicationError) as rejected:
        runtime.store.claim_clarification(
            session_id=session.session_id,
            text="Количество",
            client_message_id="catalog-race-claim",
            expected_context_version=2,
            response=ClarificationResponse(
                handle=pending.handle,
                action="choose",
                choice_id="c1",
            ),
            active_catalog=stale_catalog,
            database_marker=marker.digest,
        )
    assert rejected.value.code == "CLARIFICATION_CATALOG_CHANGED"
    asyncio.run(runtime.close())
