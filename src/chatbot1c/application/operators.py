"""Deterministic operators allowed in planner DAGs."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from chatbot1c.domain.types import Period

MOSCOW = ZoneInfo("Europe/Moscow")
_DATE_RU = re.compile(r"^(?P<day>[0-3]?\d)\.(?P<month>[01]?\d)\.(?P<year>\d{4})$")


def normalize_period(value: object, *, turn_time: datetime) -> Period:
    """Normalize a closed set of Russian period expressions."""

    if isinstance(value, Period):
        return value
    local_now = turn_time.astimezone(MOSCOW)
    if isinstance(value, datetime):
        local = value.astimezone(MOSCOW)
        return _day(local.date())
    if isinstance(value, date):
        return _day(value)
    if not isinstance(value, str):
        raise ValueError("normalize_period accepts Period, date, datetime or text")
    normalized = " ".join(value.casefold().replace("ё", "е").split())
    if normalized in {"сегодня", "за сегодня", "текущий день"}:
        return _day(local_now.date())
    if normalized in {"вчера", "за вчера", "предыдущий день"}:
        return _day(local_now.date() - timedelta(days=1))
    if normalized in {"текущий месяц", "этот месяц", "за текущий месяц"}:
        return _month(local_now.year, local_now.month)
    if normalized in {"прошлый месяц", "предыдущий месяц", "за прошлый месяц"}:
        previous = local_now.replace(day=1) - timedelta(days=1)
        return _month(previous.year, previous.month)
    match = _DATE_RU.fullmatch(normalized)
    if match:
        return _day(
            date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        )
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"Неподдерживаемое выражение периода: {value!r}") from error
    return _day(parsed)


def _day(value: date) -> Period:
    start = datetime.combine(value, time.min, MOSCOW)
    return Period(
        start=start,
        end_exclusive=start + timedelta(days=1),
        timezone="Europe/Moscow",
        precision="day",
    )


def _month(year: int, month: int) -> Period:
    start = datetime(year, month, 1, tzinfo=MOSCOW)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=MOSCOW)
    else:
        end = datetime(year, month + 1, 1, tzinfo=MOSCOW)
    return Period(
        start=start,
        end_exclusive=end,
        timezone="Europe/Moscow",
        precision="month",
    )
