"""Smoke test for the ACP (Agent Communication Protocol) server —
blocks on alpha's ACP PR.

Exercises:
- ACP server starts on a deterministic port
- Client can `agents/list` and get at least the nellie entry
- `agents/run` dispatches a prompt and returns a result
- Server cleanly handles client disconnect mid-stream

Unskip by removing the `_available()` guard when alpha ships ACP.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _acp_available() -> bool:
    try:
        import karna.acp_server  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def acp_module():
    if not _acp_available():
        pytest.skip("karna.acp_server not available — blocked on alpha's ACP PR")
    import karna.acp_server as acp

    return acp


def test_agents_list_has_nellie(acp_module):
    # Placeholder shape — tighten once alpha's client is published
    agents = acp_module.list_agents()  # type: ignore[attr-defined]
    assert any("nellie" in str(a).lower() for a in agents)


def test_agents_run_returns_result(acp_module):
    result = acp_module.run_agent(  # type: ignore[attr-defined]
        "nellie",
        prompt="say hi and stop",
    )
    assert result is not None
