"""Security regression tests for the comms tool.

Covers the P2 message-size finding from
``research/karna/NEW_TOOLS_AUDIT_20260420.md`` — the send/reply paths
must reject oversized bodies before they reach inbox storage.
"""

from __future__ import annotations

import pytest

from karna.tools.comms import _MAX_MESSAGE_BYTES, CommsTool


class TestCommsSizeLimit:
    @pytest.mark.asyncio
    async def test_send_rejects_oversized_body(self):
        tool = CommsTool(agent_name="alpha")
        huge = "x" * (_MAX_MESSAGE_BYTES + 1)
        result = await tool.execute(
            action="send",
            to="beta",
            subject="huge",
            body=huge,
        )
        assert "[error]" in result
        assert "limit" in result.lower()

    @pytest.mark.asyncio
    async def test_reply_rejects_oversized_body(self):
        tool = CommsTool(agent_name="alpha")
        huge = "x" * (_MAX_MESSAGE_BYTES + 1)
        # Reply refusal happens on size check before we touch storage, so
        # an arbitrary message_id is fine here.
        result = await tool.execute(
            action="reply",
            message_id="nonexistent-id",
            body=huge,
        )
        assert "[error]" in result
        assert "limit" in result.lower()
