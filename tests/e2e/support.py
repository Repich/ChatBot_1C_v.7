from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("Expected an HTTP JSON object")
        return value


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> HttpResponse:
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            response = urlopen(request, timeout=timeout)  # noqa: S310
        except HTTPError as error:
            return HttpResponse(
                error.code,
                {key.lower(): value for key, value in error.headers.items()},
                error.read(),
            )
        with response:
            return HttpResponse(
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )

    def json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> tuple[HttpResponse, dict[str, Any]]:
        encoded = None
        request_headers = dict(headers or {})
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        response = self.request(
            method,
            path,
            body=encoded,
            headers=request_headers,
            timeout=timeout,
        )
        return response, response.json()


class FixtureDriver:
    def __init__(self, base_url: str) -> None:
        self.http = HttpClient(base_url)

    def configure(
        self,
        name: str,
        *,
        delay_ms: int | None = None,
        clear_requests: bool = True,
        deepseek_content: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "clear_requests": clear_requests,
        }
        if delay_ms is not None:
            payload["delay_ms"] = delay_ms
        if deepseek_content is not None:
            payload["deepseek_content"] = deepseek_content
        response, result = self.http.json(
            "POST", "/__fixture__/scenario", payload, timeout=5
        )
        assert response.status == 200, result
        return result

    def state(self) -> dict[str, Any]:
        response, result = self.http.json("GET", "/__fixture__/state", timeout=5)
        assert response.status == 200, result
        return result

    def wait_for_boundary(self, boundary: str, timeout: float = 5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matching = [
                request
                for request in self.state()["requests"]
                if request["boundary"] == boundary
            ]
            if matching:
                return matching[-1]
            time.sleep(0.05)
        raise AssertionError(f"Fixture did not observe {boundary!r} within {timeout}s")


class ScenarioController:
    def __init__(
        self, driver: FixtureDriver, planner_responses: dict[str, dict[str, Any]]
    ) -> None:
        self.driver = driver
        self.planner_responses = planner_responses

    def set(
        self,
        name: str,
        *,
        delay_ms: int | None = None,
        clear_requests: bool = True,
    ) -> dict[str, Any]:
        planner = self.planner_responses.get(name)
        content = None
        if planner is not None:
            content = json.dumps(planner, ensure_ascii=False, separators=(",", ":"))
        return self.driver.configure(
            name,
            delay_ms=delay_ms,
            clear_requests=clear_requests,
            deepseek_content=content,
        )


class AppClient:
    def __init__(self, base_url: str, contract: dict[str, Any]) -> None:
        self.http = HttpClient(base_url)
        self.contract = contract
        self.prefix = contract["api_prefix"]

    def _path(self, path: str) -> str:
        return f"{self.prefix}{path}"

    def create_session(self) -> dict[str, Any]:
        spec = self.contract["session_create"]
        response, result = self.http.json(
            spec["method"], self._path(spec["path"]), {}
        )
        assert response.status == spec["status"], result
        assert_fields(result, spec["required_fields"])
        return result

    def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        spec = self.contract["message_create"]
        path = spec["path"].format(session_id=session_id)
        response, result = self.http.json(
            spec["method"], self._path(path), {"text": text}
        )
        assert response.status == spec["status"], result
        assert_fields(result, spec["required_fields"])
        assert result["status"] == spec["accepted_status"]
        return result

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        spec = self.contract["turn"]
        path = spec["path"].format(turn_id=turn_id)
        response, result = self.http.json(spec["method"], self._path(path))
        assert response.status == 200, result
        assert_fields(result, spec["required_fields"])
        return result

    def wait_turn(self, turn_id: str, timeout: float = 30) -> dict[str, Any]:
        terminal = set(self.contract["turn"]["terminal_statuses"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            turn = self.get_turn(turn_id)
            if turn["status"] in terminal:
                return turn
            time.sleep(0.1)
        raise AssertionError(f"Turn {turn_id} did not finish within {timeout}s")

    def ask(
        self, session_id: str, text: str, timeout: float = 30
    ) -> tuple[dict[str, Any], float]:
        started = time.monotonic()
        accepted = self.send_message(session_id, text)
        turn = self.wait_turn(accepted["turn_id"], timeout=timeout)
        return turn, time.monotonic() - started

    def get_session(self, session_id: str) -> dict[str, Any]:
        spec = self.contract["session"]
        path = spec["path"].format(session_id=session_id)
        response, result = self.http.json(spec["method"], self._path(path))
        assert response.status == 200, result
        assert_fields(result, spec["required_fields"])
        return result

    def list_skills(self) -> dict[str, Any]:
        spec = self.contract["skills_list"]
        response, result = self.http.json(spec["method"], self._path(spec["path"]))
        assert response.status == 200, result
        assert_fields(result, spec["required_fields"])
        return result

    def import_package(
        self,
        package_path: Path,
        *,
        mode: str,
        if_match: str | None = None,
    ) -> tuple[HttpResponse, dict[str, Any]]:
        boundary = "slice1-acceptance-multipart-boundary"
        file_bytes = package_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="package.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if if_match is not None:
            headers["If-Match"] = if_match
        method = self.contract["package_import"][mode]["method"]
        path = self.contract["package_import"][mode]["path"]
        response = self.http.request(
            method,
            self._path(path),
            body=body,
            headers=headers,
            timeout=30,
        )
        return response, response.json()

    def diagnostic_zip(self, trace_id: str) -> HttpResponse:
        spec = self.contract["diagnostic_zip"]
        path = spec["path"].format(trace_id=trace_id)
        return self.http.request(spec["method"], self._path(path), timeout=30)

    def sse_events(self, turn_id: str, timeout: float = 30) -> list[dict[str, Any]]:
        spec = self.contract["sse"]
        path = self._path(spec["path"].format(turn_id=turn_id))
        request = Request(
            f"{self.http.base_url}{path}",
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        events: list[dict[str, Any]] = []
        data_lines: list[str] = []
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            content_type = response.headers.get("Content-Type", "")
            assert spec["content_type"] in content_type
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    event = json.loads("\n".join(data_lines))
                    assert isinstance(event, dict)
                    events.append(event)
                    data_lines.clear()
                    if event.get("stage") in spec["terminal_stages"]:
                        break
        return events


class CliClient:
    def __init__(self, command: str, fixture_url: str) -> None:
        self.argv = shlex.split(command)
        if not self.argv:
            raise ValueError("SLICE1_CLI must not be empty")
        self.fixture_url = fixture_url.rstrip("/")

    def run(self, args: list[str], data_dir: Path, timeout: float = 30) -> dict[str, Any]:
        environment = os.environ.copy()
        environment.update(
            {
                "APP_DATA_DIR": str(data_dir),
                "DEEPSEEK_BASE_URL": self.fixture_url,
                "DEEPSEEK_API_KEY": "SYNTHETIC-SLICE1-CLI-CANARY",
                "MCP_URL": f"{self.fixture_url}/mcp",
            }
        )
        completed = subprocess.run(
            [*self.argv, *args],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=timeout,
        )
        assert completed.returncode == 0, {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, completed.stdout
        value = json.loads(lines[0])
        assert isinstance(value, dict)
        return value


def assert_fields(value: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in value]
    assert not missing, {"missing_fields": missing, "value": value}


def nested_object_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("_objectRef") is True:
            refs.append(value)
        for child in value.values():
            refs.extend(nested_object_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(nested_object_refs(child))
    return refs


def nested_bindings(value: Any, source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("source") == source:
            bindings.append(value)
        for child in value.values():
            bindings.extend(nested_bindings(child, source))
    elif isinstance(value, list):
        for child in value:
            bindings.extend(nested_bindings(child, source))
    return bindings
