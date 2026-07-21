"""Shared request-deadline helpers for bounded external stages."""

from __future__ import annotations

from datetime import UTC, datetime

from chatbot1c.application.errors import ApplicationError


def remaining_seconds(deadline_at: datetime | None) -> float | None:
    if deadline_at is None:
        return None
    return max(0.0, (deadline_at - datetime.now(UTC)).total_seconds())


def stage_timeout(deadline_at: datetime | None, configured: float) -> float:
    remaining = remaining_seconds(deadline_at)
    if remaining is None:
        return configured
    if remaining <= 0:
        raise ApplicationError(
            "REQUEST_DEADLINE_EXCEEDED",
            "Общий срок обработки запроса исчерпан.",
            504,
        )
    return min(configured, remaining)


def retry_fits(
    deadline_at: datetime | None,
    *,
    backoff: float,
    minimum_attempt: float = 0.1,
) -> bool:
    remaining = remaining_seconds(deadline_at)
    return remaining is None or remaining >= backoff + minimum_attempt
