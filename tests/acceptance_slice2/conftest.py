from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from .support import AppClient, FixtureClient, HttpClient

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SERVER = Path(__file__).with_name("fixture_server.py")
STARTER_PACKAGE = REPO_ROOT / "skills/ut-11.5.27.56/ut.starter.slice-two.package.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


@dataclass
class RunningApp:
    fixture_url: str
    data_dir: Path
    marker: str
    deadline: int
    auto_import: bool = True
    process: subprocess.Popen[bytes] | None = None
    base_url: str = ""
    client: AppClient | None = None
    log_path: Path = field(init=False)
    _log_stream: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.log_path = self.data_dir / "acceptance-app.log"

    def start(self) -> "RunningApp":
        self.data_dir.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        source_path = str(REPO_ROOT / "src")
        environment["PYTHONPATH"] = (
            source_path
            if not environment.get("PYTHONPATH")
            else source_path + os.pathsep + environment["PYTHONPATH"]
        )
        environment.update(
            {
                "APP_DATA_DIR": str(self.data_dir),
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(port),
                "AUTO_IMPORT_BUILTIN_SKILLS": ("true" if self.auto_import else "false"),
                "BUILD_HELP_INDEX_ON_START": "false",
                "DATABASE_STATE_MARKER": self.marker,
                "DEEPSEEK_API_KEY": "slice2-synthetic-secret",
                "DEEPSEEK_BASE_URL": self.fixture_url,
                "DEEPSEEK_MODEL": "deepseek-chat",
                "DEFAULT_LIST_LIMIT": "20",
                "LOG_LEVEL": "WARNING",
                "MCP_CHANNEL": "slice2-acceptance",
                "MCP_URL": f"{self.fixture_url}/mcp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "REQUEST_DEADLINE_SECONDS": str(self.deadline),
                "STARTER_PACKAGE_PATH": str(STARTER_PACKAGE),
            }
        )
        self._log_stream = self.log_path.open("ab")
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
            cwd=REPO_ROOT,
            env=environment,
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
        )
        self._wait_until_live()
        self.client = AppClient(self.base_url)
        return self

    def _wait_until_live(self) -> None:
        deadline = time.monotonic() + 20
        health = HttpClient(self.base_url)
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                break
            try:
                response, payload = health.json("GET", "/api/v1/health/live", timeout=1)
                if response.status == 200 and payload == {"status": "live"}:
                    return
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.05)
        self.stop()
        log = self.log_path.read_text(encoding="utf-8", errors="replace")
        pytest.fail(f"slice2 app failed to start\n{log[-8000:]}")

    def restart(
        self, *, marker: str | None = None, deadline: int | None = None
    ) -> None:
        self.stop()
        if marker is not None:
            self.marker = marker
        if deadline is not None:
            self.deadline = deadline
        self.start()

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None

    @property
    def api(self) -> AppClient:
        assert self.client is not None
        return self.client


class AppFactory:
    def __init__(self, fixture_url: str, root: Path) -> None:
        self.fixture_url = fixture_url
        self.root = root
        self.apps: list[RunningApp] = []

    def start(
        self,
        *,
        marker: str = "slice2-marker-v1",
        deadline: int = 8,
        data_dir: Path | None = None,
        auto_import: bool = True,
    ) -> RunningApp:
        target = data_dir or self.root / f"app-{len(self.apps) + 1}"
        app = RunningApp(
            self.fixture_url,
            target,
            marker,
            deadline,
            auto_import=auto_import,
        ).start()
        self.apps.append(app)
        return app

    def close(self) -> None:
        for app in reversed(self.apps):
            app.stop()


@pytest.fixture(scope="session")
def fixture_transport() -> Iterator[FixtureClient]:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE_SERVER), "--port", "0"],
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
        pytest.fail(f"slice2 fixture failed to start: {stderr}")
    ready = json.loads(ready_line)
    fixture = FixtureClient(ready["base_url"])
    response, payload = fixture.http.json("GET", "/__fixture__/health", timeout=5)
    assert response.status == 200
    assert payload == {"status": "ready"}
    try:
        yield fixture
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def app_factory(fixture_transport: FixtureClient) -> Iterator[AppFactory]:
    fixture_transport.configure("warehouse")
    with tempfile.TemporaryDirectory(prefix="chatbot1c-slice2-") as directory:
        factory = AppFactory(fixture_transport.http.base_url, Path(directory))
        try:
            yield factory
        finally:
            factory.close()


@pytest.fixture
def app(app_factory: AppFactory) -> RunningApp:
    return app_factory.start()
