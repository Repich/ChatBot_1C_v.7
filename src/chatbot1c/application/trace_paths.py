"""Stable diagnostic artifact paths independent of planner display formatting."""

from __future__ import annotations


def step_trace_prefix(step_id: str) -> str:
    number = int(step_id.removeprefix("s"))
    return f"steps/s{number:02d}"
