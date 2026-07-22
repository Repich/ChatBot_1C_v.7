# Local MCP proxy acceptance

This directory is an independent black-box contract for ADR-0005. It imports no
third-party proxy and no private implementation objects. Both the test oracle
and the product target are started as ASGI processes on an ephemeral local port.

The public product entrypoint expected by the suite is the zero-argument ASGI
factory `chatbot1c.mcp_proxy:create_app`. Deterministic test bounds are supplied
through `MCP_PROXY_*` environment variables in `support.py`.

The same scenarios run against two implementations:

- `proxy_harness`: test-only oracle; must be green before interpreting target
  failures;
- `proxy_target_gap`: production factory; intentionally red until the proxy is
  implemented.

Neither marker requires a real 1C session or port `6003`. A simulated 1C poller
uses only `/1c/poll` and `/1c/result`.

Run separately:

```bash
uv run pytest -q tests/acceptance_proxy -m proxy_harness
uv run pytest -q tests/acceptance_proxy -m proxy_target_gap
```
