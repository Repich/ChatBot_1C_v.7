# Slice 3 black-box acceptance

The suite exercises the built application only through public HTTP/SSE, the
portable package import API, diagnostic ZIP exports, and captured DeepSeek/MCP
traffic. Test code does not import `chatbot1c` modules as an oracle.

Run the target contract (red on baseline `85acc63`, green after slice 3):

```bash
uv run pytest -q tests/acceptance_slice3 --tb=short
```

Run only the four baseline gaps:

```bash
uv run pytest -q tests/acceptance_slice3 -k 'g01 or pending or replacement or scalar' --tb=short
```

The harness starts disposable application processes with clean `APP_DATA_DIR`
values and a local fixture transport. It sets
`SLICE3_ACCEPTANCE_NOW` to an RFC 3339 UTC instant. This is a test-only clock
boundary: changing the value and restarting the same data directory must move
TTL checks without sleeping for 30 minutes. The application must ignore or
reject this variable outside its acceptance/test composition mode.

Expected baseline failures are contract failures, not fixture failures:

- portable schema/package `1.1.0` is rejected;
- the message API rejects `clarification_response` and `context_action`;
- session and turn DTOs omit typed context/pending/proof summaries;
- selected-only and confirmed-filter retention do not exist;
- lexical hard-coded shortlist signals remain in application source.

To separate harness defects from expected red behavior, first run:

```bash
uv run pytest -q tests/acceptance_slice3/test_harness.py --tb=short
```

That file tests only the independent fixture server, JSON signing, HTTP client,
and package structure. It must pass on the baseline. All target tests remain
ordinary assertions (not `xfail`) so a green implementation cannot be hidden by
stale expected-failure annotations.

The optional legacy migration probe needs commit `2d40bd5` available in the
local Git object database. It is skipped, with a precise reason, when the old
source cannot be materialized as an external process.
