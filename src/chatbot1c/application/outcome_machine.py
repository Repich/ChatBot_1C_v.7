"""M03 deterministic state machine for the eight execution outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from chatbot1c.domain.outcomes import Outcome

EXECUTION_OUTCOMES = frozenset(
    {
        Outcome.SUCCESS_WITH_ROWS,
        Outcome.SUCCESS_EMPTY,
        Outcome.ZERO_AGGREGATE,
        Outcome.PARTIAL,
        Outcome.QUERY_ERROR,
        Outcome.MCP_UNAVAILABLE,
        Outcome.LLM_UNAVAILABLE,
        Outcome.CONTRACT_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class DataOutcomeSignal:
    transport: Literal["available", "mcp_unavailable"] = "available"
    contract_valid: bool = True
    query_success: bool = True
    row_count: int = 0
    effective_empty: bool = False
    required_fact_null: bool = False
    empty_semantics: Literal[
        "confirmed_not_found",
        "confirmed_no_rows",
        "not_applicable",
        "error_if_empty",
    ] = "confirmed_no_rows"
    aggregate_zero: bool = False
    coverage_sufficient: bool = True
    confirmed_fact_count: int = 0
    truncated: bool = False
    truncation_policy: Literal[
        "page_is_complete", "partial_until_all_pages", "error_if_truncated"
    ] = "page_is_complete"


def classify_data(signal: DataOutcomeSignal) -> Outcome:
    if signal.transport == "mcp_unavailable":
        return Outcome.MCP_UNAVAILABLE
    if not signal.contract_valid:
        return Outcome.CONTRACT_ERROR
    if not signal.query_success:
        return Outcome.QUERY_ERROR
    if signal.required_fact_null:
        return Outcome.CONTRACT_ERROR
    if signal.row_count == 0 or signal.effective_empty:
        return (
            Outcome.SUCCESS_EMPTY
            if signal.empty_semantics
            in {"confirmed_not_found", "confirmed_no_rows"}
            else Outcome.CONTRACT_ERROR
        )
    if signal.truncated and signal.truncation_policy == "error_if_truncated":
        return Outcome.CONTRACT_ERROR
    if signal.truncated and signal.truncation_policy == "partial_until_all_pages":
        return Outcome.PARTIAL
    if not signal.coverage_sufficient:
        return (
            Outcome.PARTIAL
            if signal.confirmed_fact_count > 0
            else Outcome.CONTRACT_ERROR
        )
    if signal.aggregate_zero:
        return Outcome.ZERO_AGGREGATE
    return Outcome.SUCCESS_WITH_ROWS


def classify_failure(
    code: str, *, stage: Literal["planning", "mcp"] = "planning"
) -> Outcome:
    if code == "REQUEST_DEADLINE_EXCEEDED":
        return (
            Outcome.LLM_UNAVAILABLE
            if stage == "planning"
            else Outcome.MCP_UNAVAILABLE
        )
    if code in {
        "LLM_UNAVAILABLE",
        "PLANNER_DEADLINE_EXCEEDED",
    }:
        return Outcome.LLM_UNAVAILABLE
    if code in {"MCP_UNAVAILABLE", "MCP_DEADLINE_EXCEEDED"}:
        return Outcome.MCP_UNAVAILABLE
    return Outcome.CONTRACT_ERROR


def combine_step_outcomes(outcomes: Sequence[Outcome], *, has_facts: bool) -> Outcome:
    if not outcomes:
        return Outcome.CONTRACT_ERROR
    failures = {
        Outcome.QUERY_ERROR,
        Outcome.MCP_UNAVAILABLE,
        Outcome.LLM_UNAVAILABLE,
        Outcome.CONTRACT_ERROR,
    }
    if Outcome.CONTRACT_ERROR in outcomes:
        return Outcome.CONTRACT_ERROR
    first_failure = next((outcome for outcome in outcomes if outcome in failures), None)
    if first_failure is not None:
        return Outcome.PARTIAL if has_facts else first_failure
    if Outcome.PARTIAL in outcomes:
        return Outcome.PARTIAL
    if Outcome.ZERO_AGGREGATE in outcomes:
        return Outcome.ZERO_AGGREGATE
    if Outcome.SUCCESS_WITH_ROWS in outcomes:
        return Outcome.SUCCESS_WITH_ROWS
    if Outcome.SUCCESS_EMPTY in outcomes:
        return Outcome.SUCCESS_EMPTY
    return Outcome.CONTRACT_ERROR
