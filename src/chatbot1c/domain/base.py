"""Shared Pydantic policy for immutable, closed domain DTOs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    """Base for boundary DTOs; unknown fields are always contract violations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        regex_engine="python-re",
        serialize_by_alias=True,
        validate_default=True,
    )
