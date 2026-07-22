from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from .support import RunningProxy

sys.dont_write_bytecode = True


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "proxy_harness: self-tests for the independent MCP proxy oracle and driver",
    )
    config.addinivalue_line(
        "markers",
        "proxy_target_gap: production MCP proxy behavior expected to be red initially",
    )


@pytest.fixture
def proxy_server(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[RunningProxy]:
    kind = str(request.param)
    server = RunningProxy(kind=kind, root=tmp_path).start()
    try:
        yield server
    finally:
        server.stop()
