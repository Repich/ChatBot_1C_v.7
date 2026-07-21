from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.blocked
@pytest.mark.skip(reason="Blocked until the live read-only MCP test environment exists")
def test_live_mcp_executes_linked_temp_batch_in_one_manager_context() -> None:
    """Reserved acceptance point for the slice that introduces the MCP adapter."""
