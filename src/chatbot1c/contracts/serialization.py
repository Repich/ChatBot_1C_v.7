"""Canonical closed-model serialization for portable wire documents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from chatbot1c.contracts.digest import canonicalize


def wire_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def wire_bytes(model: BaseModel) -> bytes:
    return canonicalize(wire_dict(model))


def wire_json(model: BaseModel) -> str:
    return wire_bytes(model).decode("utf-8")
