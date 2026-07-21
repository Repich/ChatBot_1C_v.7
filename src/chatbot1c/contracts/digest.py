"""RFC 8785 canonical JSON and SHA-256 document integrity."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

import rfc8785

from chatbot1c.contracts.errors import ContractIssue, ContractValidationError

INTEGRITY_DESCRIPTOR = {
    "algorithm": "sha256",
    "canonicalization": "RFC8785",
    "scope": "document_without_integrity",
}


def canonicalize(value: object) -> bytes:
    """Return RFC 8785/JCS bytes or a stable contract error."""

    try:
        # The public boundary deliberately accepts arbitrary input so unsupported
        # values become contract issues instead of leaking library exceptions.
        return rfc8785.dumps(cast(Any, value))
    except (TypeError, ValueError, rfc8785.CanonicalizationError) as error:
        raise ContractValidationError(
            (
                ContractIssue(
                    code="CANONICALIZATION_ERROR",
                    json_pointer="",
                    message_ru=f"Документ нельзя канонизировать по RFC 8785: {error}",
                ),
            )
        ) from error


def document_without_integrity(document: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(document))
    payload.pop("integrity", None)
    return payload


def compute_digest(document: Mapping[str, Any]) -> str:
    payload = document_without_integrity(document)
    return hashlib.sha256(canonicalize(payload)).hexdigest()


def generate_integrity(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied document with a freshly generated top-level digest."""

    generated = document_without_integrity(document)
    generated["integrity"] = {
        **INTEGRITY_DESCRIPTOR,
        "digest": hashlib.sha256(canonicalize(generated)).hexdigest(),
    }
    return generated


def verify_digest(document: Mapping[str, Any], pointer_prefix: str = "") -> str:
    integrity = document.get("integrity")
    pointer = f"{pointer_prefix}/integrity/digest"
    if not isinstance(integrity, Mapping) or not isinstance(
        integrity.get("digest"), str
    ):
        raise ContractValidationError(
            (
                ContractIssue(
                    code="DIGEST_MISSING",
                    json_pointer=pointer,
                    message_ru="В документе отсутствует integrity.digest.",
                ),
            )
        )

    expected = str(integrity["digest"])
    actual = compute_digest(document)
    if not hmac.compare_digest(expected, actual):
        raise ContractValidationError(
            (
                ContractIssue(
                    code="DIGEST_MISMATCH",
                    json_pointer=pointer,
                    message_ru=(
                        "SHA-256 не совпадает с RFC 8785 canonical document "
                        "без top-level integrity."
                    ),
                ),
            )
        )
    return actual
