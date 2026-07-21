from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from conftest import FixtureClient
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNER_SCHEMA = json.loads(
    (REPO_ROOT / "schemas/planner-output.schema.json").read_text(encoding="utf-8")
)


def _rpc(
    fixture_transport: FixtureClient,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: int = 1,
) -> dict[str, Any]:
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    status, response = fixture_transport.request("POST", "/mcp", request)
    assert status == 200
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _call_tool(
    fixture_transport: FixtureClient,
    name: str,
    arguments: dict[str, Any],
    request_id: int = 1,
) -> dict[str, Any]:
    response = _rpc(
        fixture_transport,
        "tools/call",
        {"name": name, "arguments": arguments},
        request_id,
    )
    return response["result"]


def test_deepseek_fixture_is_openai_compatible_and_planner_schema_valid(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q011")
    request = {
        "model": "deepseek-chat",
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": "SYNTHETIC planner input"}],
    }
    status, response = fixture_transport.request(
        "POST",
        "/chat/completions",
        request,
        headers={"Authorization": "Bearer SYNTHETIC-SECRET-CANARY"},
    )

    assert status == 200
    content = response["choices"][0]["message"]["content"]
    planner_output = json.loads(content)
    Draft202012Validator(PLANNER_SCHEMA).validate(planner_output)

    state = fixture_transport.state()
    assert state["requests"][-1]["body"] == request
    assert "authorization" in state["requests"][-1]["header_names"]
    assert "SYNTHETIC-SECRET-CANARY" not in json.dumps(state)


def test_observed_mcp_query_is_absent_from_observed_deepseek_request(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q011")
    query = "SYNTHETIC FIXED QUERY TEXT THAT MUST STAY OUTSIDE THE LLM"
    fixture_transport.request(
        "POST",
        "/chat/completions",
        {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "SYNTHETIC semantic catalog"}],
        },
    )
    _call_tool(
        fixture_transport,
        "execute_query",
        {"query": query, "params": {}, "limit": 100, "include_schema": True},
    )
    state = fixture_transport.state()
    deepseek_requests = [
        request for request in state["requests"] if request["boundary"] == "deepseek"
    ]
    mcp_requests = [
        request for request in state["requests"] if request["boundary"] == "mcp"
    ]
    assert query in json.dumps(mcp_requests)
    assert query not in json.dumps(deepseek_requests)


def test_deepseek_malformed_and_schema_invalid_are_distinct_wire_cases(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("deepseek_malformed")
    _, malformed = fixture_transport.request("POST", "/chat/completions", {})
    malformed_content = malformed["choices"][0]["message"]["content"]
    try:
        json.loads(malformed_content)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("Malformed DeepSeek content unexpectedly parsed")

    fixture_transport.configure("deepseek_schema_invalid")
    _, invalid = fixture_transport.request("POST", "/chat/completions", {})
    invalid_planner = json.loads(invalid["choices"][0]["message"]["content"])
    assert list(Draft202012Validator(PLANNER_SCHEMA).iter_errors(invalid_planner))


def test_q037_planner_fixtures_use_context_or_clarify_without_literal_ref(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q037")
    _, response = fixture_transport.request("POST", "/chat/completions", {})
    execute = json.loads(response["choices"][0]["message"]["content"])
    Draft202012Validator(PLANNER_SCHEMA).validate(execute)
    slot_binding = execute["interpretation"]["slots"][0]["binding"]
    argument_binding = execute["result"]["steps"][0]["arguments"][0]["binding"]
    assert slot_binding == argument_binding
    assert slot_binding == {
        "source": "context",
        "context_handle": "ctx_SYNTHETICORDER0001",
        "expected_semantic_type": "document.sales_order",
    }
    assert "_objectRef" not in json.dumps(execute)

    fixture_transport.configure("q037_missing_context")
    _, response = fixture_transport.request("POST", "/chat/completions", {})
    clarify = json.loads(response["choices"][0]["message"]["content"])
    Draft202012Validator(PLANNER_SCHEMA).validate(clarify)
    assert clarify["decision"] == "clarify"
    assert clarify["interpretation"]["slots"][0]["status"] == "missing"
    assert not [
        request
        for request in fixture_transport.state()["requests"]
        if request["boundary"] == "mcp"
    ]


def test_mcp_initialize_lists_only_slice1_read_boundaries(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q011")
    initialized = _rpc(
        fixture_transport,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "acceptance", "version": "1"},
        },
    )
    assert initialized["result"]["serverInfo"]["name"] == "slice1-fixture"

    listed = _rpc(fixture_transport, "tools/list", request_id=2)
    assert {tool["name"] for tool in listed["result"]["tools"]} == {
        "execute_query",
        "get_metadata",
    }

    metadata = _call_tool(fixture_transport, "get_metadata", {}, request_id=3)
    assert metadata["structuredContent"]["data"]["supports_linked_temp_batch"] is True


def test_mcp_retains_exact_object_ref_across_fixture_turns(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q036")
    q036_result = _call_tool(
        fixture_transport,
        "execute_query",
        {
            "query": "SYNTHETIC Q036 FIXED QUERY",
            "params": {"number": "SYNTHETIC-ORDER-NUMBER"},
            "limit": 100,
            "include_schema": True,
        },
    )
    returned_ref = q036_result["structuredContent"]["data"][0]["Заказ"]
    assert returned_ref == fixture_transport.state()["object_refs"]["sales_order"]

    fixture_transport.configure("q037", clear_requests=False)
    _call_tool(
        fixture_transport,
        "execute_query",
        {
            "query": "SYNTHETIC Q037 FIXED QUERY",
            "params": {"selected_sales_order": returned_ref},
            "limit": 100,
            "include_schema": True,
        },
    )
    mcp_request = fixture_transport.state()["requests"][-1]["body"]
    observed_ref = mcp_request["params"]["arguments"]["params"][
        "selected_sales_order"
    ]
    assert observed_ref == returned_ref
    mcp_calls = [
        request
        for request in fixture_transport.state()["requests"]
        if request["boundary"] == "mcp"
        and request["body"].get("method") == "tools/call"
    ]
    assert len(mcp_calls) == 2
    follow_up_arguments = mcp_calls[-1]["body"]["params"]["arguments"]
    assert "number" not in follow_up_arguments["params"]
    assert "SYNTHETIC-ORDER-NUMBER" not in json.dumps(follow_up_arguments)


def test_q036_ambiguous_fixture_does_not_collapse_distinct_refs(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q036_ambiguous")
    result = _call_tool(
        fixture_transport,
        "execute_query",
        {
            "query": "SYNTHETIC Q036 FIXED QUERY",
            "params": {"number": "SYNTHETIC-ORDER-NUMBER"},
            "limit": 100,
            "include_schema": True,
        },
    )["structuredContent"]
    refs = [row["Заказ"] for row in result["data"]]
    assert result["success"] is True
    assert result["count"] == 2
    assert refs == [
        fixture_transport.state()["object_refs"]["sales_order"],
        fixture_transport.state()["object_refs"]["other_sales_order"],
    ]
    assert refs[0] != refs[1]


def test_q037_ref_mismatch_fixture_is_observable_at_mcp_boundary(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q037_ref_mismatch")
    bound_ref = fixture_transport.state()["object_refs"]["sales_order"]
    result = _call_tool(
        fixture_transport,
        "execute_query",
        {
            "query": "SYNTHETIC Q037 FIXED QUERY",
            "params": {"selected_sales_order": bound_ref},
            "limit": 100,
            "include_schema": True,
        },
    )["structuredContent"]
    returned_ref = result["data"][0]["Заказ"]
    assert returned_ref == fixture_transport.state()["object_refs"][
        "other_sales_order"
    ]
    assert returned_ref != bound_ref


def test_mcp_success_empty_is_not_query_error(
    fixture_transport: FixtureClient,
) -> None:
    arguments = {
        "query": "SYNTHETIC FIXED QUERY",
        "params": {},
        "limit": 100,
        "include_schema": True,
    }
    fixture_transport.configure("q102")
    empty = _call_tool(fixture_transport, "execute_query", arguments)[
        "structuredContent"
    ]

    fixture_transport.configure("mcp_query_error")
    failed = _call_tool(fixture_transport, "execute_query", arguments)[
        "structuredContent"
    ]

    assert empty["success"] is True
    assert empty["data"] == []
    assert empty["count"] == 0
    assert failed["success"] is False
    assert failed.get("error")
    assert empty != failed


def test_real_mcp_minimal_success_and_error_only_envelopes_are_preserved(
    fixture_transport: FixtureClient,
) -> None:
    arguments = {
        "query": "SYNTHETIC FIXED QUERY",
        "params": {},
        "limit": 100,
        "include_schema": True,
    }
    fixture_transport.configure("q011_real_mcp_minimal")
    success = _call_tool(fixture_transport, "execute_query", arguments)[
        "structuredContent"
    ]
    assert set(success) == {"success", "data", "schema"}
    assert success["success"] is True
    assert len(success["data"]) == 1
    assert "count" not in success
    assert "truncated" not in success
    assert "has_more" not in success

    fixture_transport.configure("mcp_real_error_only")
    error = _call_tool(fixture_transport, "execute_query", arguments)[
        "structuredContent"
    ]
    assert set(error) == {"success", "error"}
    assert error["success"] is False
    assert error["error"]


def test_mcp_malformed_wrapper_is_deliberately_ambiguous(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("mcp_malformed")
    result = _call_tool(
        fixture_transport,
        "execute_query",
        {
            "query": "SYNTHETIC FIXED QUERY",
            "params": {},
            "limit": 100,
            "include_schema": True,
        },
    )
    assert "structuredContent" not in result
    assert len(result["content"]) == 2
    assert result["content"][0]["text"] != result["content"][1]["text"]


def test_fixture_transport_itself_does_not_consume_product_slo(
    fixture_transport: FixtureClient,
) -> None:
    fixture_transport.configure("q011")
    started = time.monotonic()
    fixture_transport.request("POST", "/chat/completions", {})
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
