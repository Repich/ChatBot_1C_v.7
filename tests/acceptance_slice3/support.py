from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PROFILE = REPO_ROOT / "tests/fixtures/slice3/configuration-profile.json"
TERMINAL_STAGES = {"request.completed", "request.failed"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body)
        assert isinstance(value, dict), value
        return value


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> HttpResponse:
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                    body=response.read(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers={key.lower(): value for key, value in error.headers.items()},
                body=error.read(),
            )

    def json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> HttpResponse:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self.request(method, path, body, headers=headers, timeout=timeout)


class FixtureClient:
    def __init__(self, base_url: str) -> None:
        self.http = HttpClient(base_url)

    def configure(self, scenario: str, **options: Any) -> dict[str, Any]:
        response = self.http.json(
            "POST", "/__fixture__/scenario", {"name": scenario, **options}
        )
        assert response.status == 200, response.body.decode("utf-8", "replace")
        return response.json()

    def state(self) -> dict[str, Any]:
        response = self.http.request("GET", "/__fixture__/state")
        assert response.status == 200
        return response.json()

    def requests(self, kind: str | None = None) -> list[dict[str, Any]]:
        requests = self.state()["requests"]
        assert isinstance(requests, list)
        if kind is None:
            return requests
        return [item for item in requests if item.get("kind") == kind]


class AppClient:
    def __init__(self, base_url: str) -> None:
        self.http = HttpClient(base_url)

    def create_session(self, title: str = "Slice 3 acceptance") -> dict[str, Any]:
        response = self.http.json("POST", "/api/v1/sessions", {"title": title})
        assert response.status == 201, response.body.decode("utf-8", "replace")
        return response.json()

    def session(self, session_id: str) -> dict[str, Any]:
        response = self.http.request("GET", f"/api/v1/sessions/{session_id}")
        assert response.status == 200, response.body.decode("utf-8", "replace")
        return response.json()

    def submit(
        self,
        session_id: str,
        text: str,
        *,
        context_version: int | None = None,
        clarification: dict[str, Any] | None = None,
        client_message_id: str | None = None,
    ) -> HttpResponse:
        payload: dict[str, Any] = {
            "text": text,
            "client_message_id": client_message_id or f"s3-{uuid.uuid4()}",
        }
        if context_version is not None:
            payload["expected_context_version"] = context_version
        if clarification is not None:
            payload["clarification_response"] = clarification
        return self.http.json(
            "POST", f"/api/v1/sessions/{session_id}/messages", payload
        )

    def submit_context_action(
        self, session_id: str, handle: str, *, context_version: int
    ) -> HttpResponse:
        return self.http.json(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            {
                "expected_context_version": context_version,
                "context_action": {"kind": "remove", "handle": handle},
            },
        )

    def ask(
        self,
        session_id: str,
        text: str,
        *,
        context_version: int | None = None,
        clarification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        accepted = self.submit(
            session_id,
            text,
            context_version=context_version,
            clarification=clarification,
        )
        assert accepted.status == 202, accepted.body.decode("utf-8", "replace")
        turn_id = accepted.json()["turn_id"]
        events = self.events(turn_id)
        assert events, turn_id
        assert events[-1]["stage"] in TERMINAL_STAGES, events
        turn = self.turn(turn_id)
        turn["_events"] = events
        return turn

    def events(self, turn_id: str, timeout: float = 30) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            self.http.base_url + f"/api/v1/turns/{turn_id}/events",
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        with urllib.request.urlopen(request, timeout=timeout) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            data: list[str] = []
            while time.monotonic() < deadline:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    value = json.loads("\n".join(data))
                    assert isinstance(value, dict)
                    events.append(value)
                    data.clear()
                    if value.get("stage") in TERMINAL_STAGES:
                        break
        return events

    def turn(self, turn_id: str) -> dict[str, Any]:
        response = self.http.request("GET", f"/api/v1/turns/{turn_id}")
        assert response.status == 200, response.body.decode("utf-8", "replace")
        return response.json()

    def details(self, turn_id: str) -> dict[str, Any]:
        response = self.http.request("GET", f"/api/v1/turns/{turn_id}/details")
        assert response.status == 200, response.body.decode("utf-8", "replace")
        return response.json()

    def import_package(self, payload: bytes, mode: str = "create") -> HttpResponse:
        boundary = f"----slice3-{uuid.uuid4().hex}"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="slice3.package.json"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode()
            + payload
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.http.request(
            "POST",
            f"/api/v1/skill-packages/import?mode={mode}",
            body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def diagnostic_members(self, trace_id: str) -> dict[str, bytes]:
        response = self.http.request("GET", f"/api/v1/traces/{trace_id}/export")
        assert response.status == 200, response.body.decode("utf-8", "replace")
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def evidence(self, trace_id: str) -> dict[str, Any]:
        members = self.diagnostic_members(trace_id)
        value = json.loads(members["evidence.json"])
        assert isinstance(value, dict)
        return value


@dataclass
class RunningApp:
    data_dir: Path
    fixture_url: str
    source_root: Path = REPO_ROOT
    now: str = "2026-07-21T12:00:00Z"
    marker: str = "slice3-marker-a"
    process: subprocess.Popen[bytes] | None = None
    base_url: str = ""
    api: AppClient | None = None
    log_path: Path = field(init=False)
    _log: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.log_path = self.data_dir / "acceptance-app.log"

    def start(self) -> "RunningApp":
        assert self.process is None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        port = free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        source = str(self.source_root / "src")
        environment["PYTHONPATH"] = (
            source
            if not environment.get("PYTHONPATH")
            else source + os.pathsep + environment["PYTHONPATH"]
        )
        environment.update(
            {
                "APP_DATA_DIR": str(self.data_dir),
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(port),
                "AUTO_IMPORT_BUILTIN_SKILLS": "false",
                "BUILD_HELP_INDEX_ON_START": "false",
                "DATABASE_PROFILE_PATH": str(FIXTURE_PROFILE),
                "DATABASE_STATE_MARKER": self.marker,
                "DEEPSEEK_API_KEY": "SLICE3-DEEPSEEK-SECRET-CANARY",
                "DEEPSEEK_BASE_URL": self.fixture_url,
                "DEEPSEEK_MODEL": "deepseek-chat",
                "LOG_LEVEL": "WARNING",
                "MCP_CHANNEL": "slice3-acceptance",
                "MCP_URL": f"{self.fixture_url}/mcp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "REQUEST_DEADLINE_SECONDS": "30",
                "SLICE3_ACCEPTANCE_MODE": "true",
                "SLICE3_ACCEPTANCE_NOW": self.now,
            }
        )
        self._log = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "chatbot1c.cli",
                "--env-file",
                str(self.data_dir / "absent.env"),
                "start",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=self.source_root,
            env=environment,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self._wait_live()
        self.api = AppClient(self.base_url)
        return self

    def restart(
        self, *, now: str | None = None, marker: str | None = None
    ) -> "RunningApp":
        self.stop()
        if now is not None:
            self.now = now
        if marker is not None:
            self.marker = marker
        return self.start()

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        self.api = None

    def _wait_live(self) -> None:
        assert self.process is not None
        client = HttpClient(self.base_url)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.log_path.read_text(encoding="utf-8"))
            try:
                response = client.request("GET", "/api/v1/health/live", timeout=1)
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
                continue
            if response.status == 200:
                return
            time.sleep(0.05)
        raise AssertionError(f"application did not start; log={self.log_path}")


def json_member(members: dict[str, bytes], name: str) -> dict[str, Any]:
    value = json.loads(members[name])
    assert isinstance(value, dict)
    return value


def context_slots(session: dict[str, Any]) -> list[dict[str, Any]]:
    context = session.get("context")
    assert isinstance(context, dict), session
    slots = context.get("slots")
    assert isinstance(slots, list), context
    assert all(isinstance(slot, dict) for slot in slots)
    return slots


def slot_by_key(session: dict[str, Any], key: str) -> dict[str, Any]:
    matches = [slot for slot in context_slots(session) if slot.get("slot_key") == key]
    assert len(matches) == 1, {"slot_key": key, "matches": matches, "session": session}
    return matches[0]


def error_code(response: HttpResponse) -> str:
    payload = response.json()
    error = payload.get("error")
    assert isinstance(error, dict), payload
    code = error.get("code")
    assert isinstance(code, str), payload
    return code


def canonical_ref(ref: dict[str, Any]) -> bytes:
    return json.dumps(
        ref,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
