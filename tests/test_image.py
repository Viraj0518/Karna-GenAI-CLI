"""Tests for the image/vision tool."""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from pathlib import Path

import pytest

from karna.tools.image import (
    IMAGE_MARKER_PREFIX,
    ImageTool,
    make_image_content_block,
    parse_image_marker,
)


# ======================================================================= #
#  ImageTool tests
# ======================================================================= #


class TestImageTool:
    @pytest.mark.asyncio
    async def test_read_png(self):
        """Should read a PNG and return base64 marker."""
        tool = ImageTool()

        # Create a minimal valid PNG (1x1 red pixel)
        # PNG header + IHDR + IDAT + IEND
        png_data = (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"  # 1x1 RGB
            b"\x00\x00\x00\x0cIDATx"
            b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_data)
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert result.startswith(IMAGE_MARKER_PREFIX)
            parts = result.split("|", 3)
            assert len(parts) == 4
            assert parts[1] == "image/png"
            # Verify base64 is valid
            decoded = base64.b64decode(parts[2])
            assert decoded == png_data
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_jpg(self):
        """Should accept .jpg extension."""
        tool = ImageTool()

        # Minimal JPEG (not a valid image but has correct extension)
        jpg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 50 + b"\xff\xd9"

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpg_data)
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert result.startswith(IMAGE_MARKER_PREFIX)
            assert "|image/jpeg|" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_reject_non_image(self):
        """Should reject non-image file extensions."""
        tool = ImageTool()

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not an image")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert "[error]" in result
            assert "Unsupported" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_reject_py_file(self):
        """Should reject .py files."""
        tool = ImageTool()

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"print('hello')")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert "[error]" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        """Should return error for missing files."""
        tool = ImageTool()
        result = await tool.execute(path="/nonexistent/image.png")
        assert "[error]" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_file(self):
        """Should reject empty files."""
        tool = ImageTool()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert "[error]" in result
            assert "empty" in result.lower()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_size_limit(self):
        """Should reject files over 20 MB."""
        tool = ImageTool()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            # Write just over 20 MB
            f.write(b"\x89PNG" + b"\x00" * (20 * 1024 * 1024 + 100))
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert "[error]" in result
            assert "too large" in result.lower()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_no_path(self):
        """Should return error when no path is given."""
        tool = ImageTool()
        result = await tool.execute()
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_webp_accepted(self):
        """Should accept .webp extension."""
        tool = ImageTool()

        with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as f:
            f.write(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 20)
            f.flush()
            path = f.name

        try:
            result = await tool.execute(path=path)
            assert result.startswith(IMAGE_MARKER_PREFIX)
            assert "|image/webp|" in result
        finally:
            os.unlink(path)

    def test_tool_format(self):
        """Should have valid OpenAI and Anthropic tool definitions."""
        tool = ImageTool()

        oai = tool.to_openai_tool()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "image"
        assert "path" in oai["function"]["parameters"]["properties"]

        anth = tool.to_anthropic_tool()
        assert anth["name"] == "image"
        assert "path" in anth["input_schema"]["properties"]


# ======================================================================= #
#  Parsing helpers
# ======================================================================= #


class TestImageMarkerParsing:
    def test_parse_valid_marker(self):
        marker = f"{IMAGE_MARKER_PREFIX}|image/png|AAAA|test.png (1 KB)"
        parsed = parse_image_marker(marker)
        assert parsed is not None
        assert parsed["media_type"] == "image/png"
        assert parsed["data"] == "AAAA"
        assert parsed["metadata"] == "test.png (1 KB)"

    def test_parse_non_marker(self):
        assert parse_image_marker("just plain text") is None

    def test_parse_malformed(self):
        assert parse_image_marker(f"{IMAGE_MARKER_PREFIX}|missing_parts") is None

    def test_make_content_block(self):
        block = make_image_content_block("image/png", "AAAA")
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"
        assert block["source"]["data"] == "AAAA"
