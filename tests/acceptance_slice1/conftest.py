from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_SERVER = REPO_ROOT / "tests/fixtures/slice1/transport_server.py"


@dataclass(frozen=True)
class FixtureClient:
    base_url: str

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        encoded = None
        request_headers = dict(headers or {})
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers=request_headers,
            method=method,
        )
        with urlopen(request, timeout=5) as response:  # noqa: S310
            body = response.read()
            parsed = json.loads(body.decode("utf-8")) if body else {}
            return response.status, parsed

    def configure(self, name: str, **overrides: Any) -> dict[str, Any]:
        status, payload = self.request(
            "POST", "/__fixture__/scenario", {"name": name, **overrides}
        )
        assert status == 200
        return payload

    def state(self) -> dict[str, Any]:
        status, payload = self.request("GET", "/__fixture__/state")
        assert status == 200
        return payload


@pytest.fixture(scope="session")
def fixture_transport() -> Iterator[FixtureClient]:
    process = subprocess.Popen(
        [sys.executable, str(TRANSPORT_SERVER), "--port", "0"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready_line = process.stdout.readline()
    if not ready_line:
        assert process.stderr is not None
        stderr = process.stderr.read()
        process.wait(timeout=5)
        pytest.fail(f"Fixture transport failed to start: {stderr}")

    ready = json.loads(ready_line)
    client = FixtureClient(ready["base_url"])
    status, health = client.request("GET", "/__fixture__/health")
    assert status == 200
    assert health == {"status": "ready"}
    try:
        yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

