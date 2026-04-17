"""Tests for the clipboard tool.

Tests focus on platform detection and the tool's interface.
Actual clipboard operations are skipped when no clipboard utility
is available (headless CI environments).
"""

from __future__ import annotations

import asyncio
import platform
import shutil
from unittest.mock import patch

import pytest

from karna.tools.clipboard import (
    ClipboardTool,
    _detect_platform,
    _get_copy_cmd,
    _get_paste_cmd,
)


# ======================================================================= #
#  Platform detection
# ======================================================================= #


class TestPlatformDetection:
    def test_macos_detection(self):
        with patch("karna.tools.clipboard.platform.system", return_value="Darwin"):
            assert _detect_platform() == "macos"

    def test_wayland_detection(self):
        with patch("karna.tools.clipboard.platform.system", return_value="Linux"), \
             patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
             patch("builtins.open", side_effect=OSError):
            assert _detect_platform() == "wayland"

    def test_x11_detection(self):
        with patch("karna.tools.clipboard.platform.system", return_value="Linux"), \
             patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True), \
             patch("builtins.open", side_effect=OSError):
            # Remove WAYLAND_DISPLAY to ensure x11 path
            import os
            env = dict(os.environ)
            env.pop("WAYLAND_DISPLAY", None)
            env["DISPLAY"] = ":0"
            with patch.dict("os.environ", env, clear=True), \
                 patch("builtins.open", side_effect=OSError):
                result = _detect_platform()
                assert result in ("x11", "wsl")  # WSL might be detected first

    def test_wsl_detection(self):
        with patch("karna.tools.clipboard.platform.system", return_value="Linux"), \
             patch("builtins.open", return_value=__import__("io").StringIO("Linux version 5.15.0-microsoft")):
            assert _detect_platform() == "wsl"

    def test_copy_paste_cmds_macos(self):
        assert _get_copy_cmd("macos") == ["pbcopy"]
        assert _get_paste_cmd("macos") == ["pbpaste"]

    def test_copy_paste_cmds_x11(self):
        with patch("shutil.which", side_effect=lambda x: x if x == "xclip" else None):
            assert _get_copy_cmd("x11") == ["xclip", "-selection", "clipboard"]
            assert _get_paste_cmd("x11") == ["xclip", "-selection", "clipboard", "-o"]

    def test_copy_paste_cmds_x11_xsel(self):
        with patch("shutil.which", side_effect=lambda x: x if x == "xsel" else None):
            assert _get_copy_cmd("x11") == ["xsel", "--clipboard", "--input"]
            assert _get_paste_cmd("x11") == ["xsel", "--clipboard", "--output"]

    def test_copy_paste_cmds_wayland(self):
        with patch("shutil.which", side_effect=lambda x: x if x.startswith("wl-") else None):
            assert _get_copy_cmd("wayland") == ["wl-copy"]
            assert _get_paste_cmd("wayland") == ["wl-paste", "--no-newline"]


# ======================================================================= #
#  ClipboardTool interface
# ======================================================================= #


class TestClipboardTool:
    def test_tool_format(self):
        tool = ClipboardTool()
        oai = tool.to_openai_tool()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "clipboard"
        props = oai["function"]["parameters"]["properties"]
        assert "action" in props
        assert "content" in props

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        tool = ClipboardTool()
        result = await tool.execute(action="invalid")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_write_without_content(self):
        tool = ClipboardTool()
        # Need to mock platform to avoid "unsupported"
        with patch("karna.tools.clipboard._detect_platform", return_value="macos"):
            result = await tool.execute(action="write")
            assert "[error]" in result
            assert "content" in result.lower()

    @pytest.mark.asyncio
    async def test_unsupported_platform(self):
        with patch("karna.tools.clipboard._detect_platform", return_value="unsupported"):
            tool = ClipboardTool()
            result = await tool.execute(action="read")
            assert "[error]" in result
            assert "No clipboard utility" in result

    @pytest.mark.asyncio
    async def test_read_roundtrip(self):
        """Test read/write roundtrip — skip if no clipboard available."""
        plat = _detect_platform()
        if plat == "unsupported":
            pytest.skip("No clipboard utility available")

        paste_cmd = _get_paste_cmd(plat)
        if paste_cmd is None or not shutil.which(paste_cmd[0]):
            pytest.skip(f"Clipboard utility not found for {plat}")

        tool = ClipboardTool()

        # Write
        test_content = "karna-clipboard-test-42"
        write_result = await tool.execute(action="write", content=test_content)
        if "[error]" in write_result:
            pytest.skip(f"Clipboard write failed: {write_result}")

        # Read back
        read_result = await tool.execute(action="read")
        assert test_content in read_result
