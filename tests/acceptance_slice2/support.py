from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Callable
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


class FixtureClient:
    def __init__(self, base_url: str) -> None:
        self.http = HttpClient(base_url)

    def configure(self, name: str, **options: Any) -> dict[str, Any]:
        response, payload = self.http.json(
            "POST", "/__fixture__/scenario", {"name": name, **options}, timeout=5
        )
        assert response.status == 200, payload
        return payload

    def state(self) -> dict[str, Any]:
        response, payload = self.http.json("GET", "/__fixture__/state", timeout=5)
        assert response.status == 200, payload
        return payload

    def release(self) -> None:
        response, payload = self.http.json("POST", "/__fixture__/release", {})
        assert response.status == 200, payload

    def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], *, timeout: float = 10
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state()
            if predicate(state):
                return state
            time.sleep(0.05)
        raise AssertionError("Fixture state did not reach the expected boundary")


class AppClient:
    prefix = "/api/v1"

    def __init__(self, base_url: str) -> None:
        self.http = HttpClient(base_url)

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        body = {} if title is None else {"title": title}
        response, payload = self.http.json("POST", f"{self.prefix}/sessions", body)
        assert response.status == 201, payload
        return payload

    def list_sessions(self) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json("GET", f"{self.prefix}/sessions")

    def get_session(self, session_id: str) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json("GET", f"{self.prefix}/sessions/{session_id}")

    def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        response, payload = self.http.json(
            "POST", f"{self.prefix}/sessions/{session_id}/messages", {"text": text}
        )
        assert response.status == 202, payload
        assert payload["status"] == "accepted", payload
        return payload

    def get_turn(self, turn_id: str) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json("GET", f"{self.prefix}/turns/{turn_id}")

    def get_turn_details(self, turn_id: str) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json("GET", f"{self.prefix}/turns/{turn_id}/details")

    def wait_turn(self, turn_id: str, *, timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response, payload = self.get_turn(turn_id)
            assert response.status == 200, payload
            if payload["status"] in {"completed", "failed", "interrupted"}:
                return payload
            time.sleep(0.05)
        raise AssertionError(f"Turn {turn_id} did not finish within {timeout}s")

    def ask(
        self, session_id: str, text: str, *, timeout: float = 30
    ) -> tuple[dict[str, Any], float]:
        started = time.monotonic()
        accepted = self.send_message(session_id, text)
        result = self.wait_turn(accepted["turn_id"], timeout=timeout)
        return result, time.monotonic() - started

    def continue_list(
        self, session_id: str, body: dict[str, Any]
    ) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json(
            "POST", f"{self.prefix}/sessions/{session_id}/continuations", body
        )

    def list_skills(self) -> dict[str, Any]:
        response, payload = self.http.json("GET", f"{self.prefix}/skills")
        assert response.status == 200, payload
        return payload

    def export_skill(self, skill_id: str) -> dict[str, Any]:
        response = self.http.request("GET", f"{self.prefix}/skills/{skill_id}/export")
        assert response.status == 200, response.body
        value = json.loads(response.body)
        assert isinstance(value, dict)
        return value

    def import_package(
        self, package: bytes, *, mode: str = "create", if_match: str | None = None
    ) -> tuple[HttpResponse, dict[str, Any]]:
        boundary = "slice2-independent-acceptance-boundary"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="package.json"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode()
            + package
            + f"\r\n--{boundary}--\r\n".encode()
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if if_match is not None:
            headers["If-Match"] = if_match
        response = self.http.request(
            "POST",
            f"{self.prefix}/skill-packages/import?mode={mode}",
            body=body,
            headers=headers,
        )
        return response, response.json()

    def delete_skill(
        self, skill_id: str, digest: str
    ) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json(
            "DELETE",
            f"{self.prefix}/skills/{skill_id}",
            headers={"If-Match": digest},
        )

    def maintenance(self, body: dict[str, Any]) -> tuple[HttpResponse, dict[str, Any]]:
        return self.http.json("POST", f"{self.prefix}/maintenance/clear", body)

    def diagnostic(self, trace_id: str) -> HttpResponse:
        return self.http.request("GET", f"{self.prefix}/traces/{trace_id}/export")

    def diagnostic_members(self, trace_id: str) -> dict[str, bytes]:
        response = self.diagnostic(trace_id)
        assert response.status == 200, response.body
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            assert archive.testzip() is None
            return {name: archive.read(name) for name in archive.namelist()}

    def evidence(self, trace_id: str) -> dict[str, Any]:
        value = json.loads(self.diagnostic_members(trace_id)["evidence.json"])
        assert isinstance(value, dict)
        return value


def deepseek_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in state["requests"] if item["kind"] == "deepseek"]


def execute_query_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in state["requests"] if item["kind"] == "mcp_execute_query"]


def error_code(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else None


def mcp_arguments(call: dict[str, Any]) -> dict[str, Any]:
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    body = call["body"]
    value = body["params"]["arguments"]
    assert isinstance(value, dict)
    return value


def rejection(
    response: HttpResponse,
    payload: dict[str, Any],
    status: int,
    code: str,
) -> None:
    assert response.status == status, payload
    assert payload["status"] == "rejected", payload
    assert payload["error"] == {
        "code": code,
        "message_ru": payload["error"]["message_ru"],
        "retryable": False,
    }
    assert payload["trace_id"]


def fact_values(evidence: dict[str, Any], fact_id: str) -> list[Any]:
    return [fact["value"] for fact in evidence["facts"] if fact["fact_id"] == fact_id]
