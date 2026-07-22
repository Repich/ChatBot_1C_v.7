from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from .support import FixtureClient, HttpClient, RunningApp, free_port
from .synthetic_package import package_bytes

FIXTURE_SERVER = Path(__file__).with_name("fixture_server.py")


@pytest.fixture(scope="session")
def fixture_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = free_port()
    log_path = tmp_path_factory.mktemp("slice3-fixture") / "fixture.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(FIXTURE_SERVER), "--port", str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    client = HttpClient(base_url)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(log_path.read_text(encoding="utf-8"))
        try:
            response = client.request("GET", "/__fixture__/health", timeout=0.5)
        except OSError:
            time.sleep(0.05)
            continue
        if response.status == 200:
            break
        time.sleep(0.05)
    else:
        process.terminate()
        pytest.fail(f"slice3 fixture server did not start: {log_path}")
    try:
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def fixture(fixture_server: str) -> FixtureClient:
    return FixtureClient(fixture_server)


@pytest.fixture(scope="session")
def portable_package() -> bytes:
    return package_bytes()


@pytest.fixture
def app_factory(
    tmp_path: Path,
    fixture_server: str,
    portable_package: bytes,
) -> Iterator[Callable[..., RunningApp]]:
    running: list[RunningApp] = []
    ordinal = 0

    def create(
        *,
        name: str = "app",
        now: str = "2026-07-21T12:00:00Z",
        marker: str = "slice3-marker-a",
        import_package: bool = True,
    ) -> RunningApp:
        nonlocal ordinal
        ordinal += 1
        app = RunningApp(
            data_dir=tmp_path / f"{name}-{ordinal}",
            fixture_url=fixture_server,
            now=now,
            marker=marker,
        ).start()
        running.append(app)
        if import_package:
            assert app.api is not None
            response = app.api.import_package(portable_package)
            assert response.status == 200, json.dumps(
                {
                    "status": response.status,
                    "body": response.body.decode("utf-8", "replace"),
                    "contract": "slice3 portable package 1.1 import",
                },
                ensure_ascii=False,
            )
        return app

    try:
        yield create
    finally:
        for app in reversed(running):
            app.stop()


@pytest.fixture
def app(app_factory: Callable[..., RunningApp]) -> RunningApp:
    return app_factory()


@pytest.fixture
def api(app: RunningApp) -> Any:
    assert app.api is not None
    return app.api
