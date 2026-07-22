from __future__ import annotations

from typing import Any

import pytest

from chatbot1c.mcp_proxy import cli


def test_launcher_uses_local_single_worker_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        observed.update({"app": app, **kwargs})

    monkeypatch.delenv("MCP_PROXY_HOST", raising=False)
    monkeypatch.delenv("MCP_PROXY_PORT", raising=False)
    monkeypatch.delenv("MCP_PROXY_LOG_LEVEL", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    assert cli.main([]) == 0
    assert observed == {
        "app": "chatbot1c.mcp_proxy:create_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 6003,
        "workers": 1,
        "log_level": "info",
    }


def test_launcher_accepts_explicit_network_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: observed.update({"app": app, **kwargs}),
    )

    assert cli.main(["--host", "0.0.0.0", "--port", "6103", "--log-level", "debug"]) == 0
    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 6103
    assert observed["workers"] == 1
    assert observed["log_level"] == "debug"


def test_launcher_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda *_args, **_kwargs: None)
    with pytest.raises(SystemExit, match="2"):
        cli.main(["--port", "0"])
