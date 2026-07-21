"""Deterministic diagnostic ZIP export with mandatory secret redaction."""

from __future__ import annotations

import hashlib
import io
import platform
import re
import zipfile
from collections.abc import Iterable, Mapping
from uuid import UUID

from chatbot1c import __version__
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.ports import TraceRepository
from chatbot1c.contracts.digest import canonicalize

_AUTH_RE = re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\s\"']+")
_SECRET_RE = re.compile(
    r"(?i)(DEEPSEEK_API_KEY|api[_-]?key|cookie)([\"']?\s*[:=]\s*[\"']?)[^\s,}\"']+"
)
_UNIX_PATH_RE = re.compile(r"/(?:Users|home)/[^\s\"']+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")


class DiagnosticExporter:
    def __init__(
        self,
        repository: TraceRepository,
        *,
        secret_values: Iterable[str] = (),
        app_version: str = __version__,
    ) -> None:
        self._repository = repository
        self._secret_values = tuple(
            value.encode("utf-8") for value in secret_values if value
        )
        self._app_version = app_version

    def export(self, trace_id: UUID) -> bytes:
        artifacts = self._repository.artifacts(trace_id)
        if not artifacts:
            raise ApplicationError("TRACE_NOT_FOUND", "Trace не найден.", 404)
        entries = {
            self._safe_name(name): self._redact(content)
            for name, content in artifacts.items()
        }
        entries["environment-summary.json"] = canonicalize(
            {
                "app_version": self._app_version,
                "python": platform.python_version(),
                "platform": platform.system(),
                "trace_id": str(trace_id),
            }
        )
        manifest = {
            "format_version": "1.0.0",
            "trace_id": str(trace_id),
            "sensitive_business_data": True,
            "files": [
                {
                    "name": name,
                    "media_type": (
                        "application/json"
                        if name.endswith(".json")
                        else "application/octet-stream"
                    ),
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in sorted(entries.items())
            ],
        }
        entries["manifest.json"] = canonicalize(manifest)
        checksum_lines = [
            f"{hashlib.sha256(content).hexdigest()}  {name}"
            for name, content in sorted(entries.items())
        ]
        entries["checksums.sha256"] = ("\n".join(checksum_lines) + "\n").encode()
        for secret in self._secret_values:
            if any(secret in content for content in entries.values()):
                raise ApplicationError(
                    "DIAGNOSTIC_REDACTION_FAILED",
                    "Диагностический пакет содержит secret canary и не выгружен.",
                    500,
                )
        return _zip(entries)

    def _redact(self, content: bytes) -> bytes:
        redacted = content
        for secret in self._secret_values:
            redacted = redacted.replace(secret, b"[REDACTED]")
        try:
            text_value = redacted.decode("utf-8")
        except UnicodeDecodeError:
            return redacted
        text_value = _AUTH_RE.sub(r"\1[REDACTED]", text_value)
        text_value = _SECRET_RE.sub(r"\1\2[REDACTED]", text_value)
        text_value = _UNIX_PATH_RE.sub("[LOCAL_PATH]", text_value)
        text_value = _WINDOWS_PATH_RE.sub("[LOCAL_PATH]", text_value)
        return text_value.encode("utf-8")

    @staticmethod
    def _safe_name(name: str) -> str:
        normalized = name.replace("\\", "/").lstrip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ApplicationError(
                "TRACE_ARTIFACT_NAME_INVALID",
                "Trace содержит небезопасное имя файла.",
                500,
            )
        return normalized


def _zip(entries: Mapping[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return target.getvalue()
