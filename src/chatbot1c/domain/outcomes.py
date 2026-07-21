"""Outcome and coverage classifications kept distinct by the domain."""

from enum import StrEnum


class Outcome(StrEnum):
    SUCCESS_WITH_ROWS = "success_with_rows"
    SUCCESS_EMPTY = "success_empty"
    ZERO_AGGREGATE = "zero_aggregate"
    DOCUMENTATION_FOUND = "documentation_found"
    DOCUMENTATION_EMPTY = "documentation_empty"
    PARTIAL = "partial"
    QUERY_ERROR = "query_error"
    CONTRACT_ERROR = "contract_error"
    MCP_UNAVAILABLE = "mcp_unavailable"
    LLM_UNAVAILABLE = "llm_unavailable"
    CLARIFICATION_REQUIRED = "clarification_required"
    REFUSED = "refused"
    CAPABILITY_GAP = "capability_gap"


class CoverageStatus(StrEnum):
    COVERED = "covered"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE_UNIT = "incompatible_unit"
    WRONG_CARDINALITY = "wrong_cardinality"
    WRONG_TIME_SCOPE = "wrong_time_scope"
