"""Image/vision tool — includes images in the conversation for analysis.

Reads a local image file, validates it, converts to base64, and returns
a structured marker that the provider layer can convert into a proper
image content block for vision-capable models.

Privacy: reads local files only.  Base64 data is sent only to the
user's configured provider endpoint.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from karna.tools.base import BaseTool

# Supported image MIME types
_SUPPORTED_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Max image size: 20 MB
_MAX_IMAGE_SIZE = 20 * 1024 * 1024

# Marker prefix used to signal the provider layer that this result
# contains an image content block rather than plain text.
IMAGE_MARKER_PREFIX = "<<IMAGE_CONTENT_BLOCK>>"


class ImageTool(BaseTool):
    """Include an image in the conversation for vision-capable models."""

    name = "image"
    description = "Include an image in the conversation for vision-capable models to analyze."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to image file (PNG, JPG, GIF, WebP)",
            },
        },
        "required": ["path"],
    }

    async def execute(self, **kwargs: Any) -> str:
        """Read image, validate, and return base64 marker or error."""
        path_str = kwargs.get("path", "")
        if not path_str:
            return "[error] No path provided."

        path = Path(path_str).expanduser().resolve()

        # Existence check
        if not path.exists():
            return f"[error] File not found: {path}"

        if not path.is_file():
            return f"[error] Not a file: {path}"

        # Extension / MIME check
        suffix = path.suffix.lower()
        media_type = _SUPPORTED_TYPES.get(suffix)
        if media_type is None:
            return (
                f"[error] Unsupported image format: {suffix}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_TYPES))}"
            )

        # Size check
        file_size = path.stat().st_size
        if file_size > _MAX_IMAGE_SIZE:
            size_mb = file_size / (1024 * 1024)
            return f"[error] Image too large: {size_mb:.1f} MB (max {_MAX_IMAGE_SIZE // (1024 * 1024)} MB)"

        if file_size == 0:
            return "[error] Image file is empty."

        # Read and encode
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return f"[error] Failed to read image: {exc}"

        b64 = base64.b64encode(raw).decode("ascii")
        size_kb = file_size / 1024

        # Return a structured marker that the provider layer can parse.
        # Format: <<IMAGE_CONTENT_BLOCK>>|<media_type>|<base64_data>|<metadata>
        metadata = f"{path.name} ({size_kb:.0f} KB)"
        return f"{IMAGE_MARKER_PREFIX}|{media_type}|{b64}|{metadata}"


def parse_image_marker(text: str) -> dict[str, str] | None:
    """Parse an image marker string into its components.

    Returns a dict with ``media_type``, ``data`` (base64), and
    ``metadata`` if *text* is a valid image marker, else ``None``.
    """
    if not text.startswith(IMAGE_MARKER_PREFIX):
        return None

    parts = text.split("|", 3)
    if len(parts) != 4:
        return None

    return {
        "media_type": parts[1],
        "data": parts[2],
        "metadata": parts[3],
    }


def make_image_content_block(media_type: str, data: str) -> dict[str, Any]:
    """Build a provider-agnostic image content block.

    For Anthropic Messages API::

        {"type": "image", "source": {"type": "base64",
         "media_type": "image/png", "data": "..."}}

    For OpenAI Chat API::

        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    This returns the Anthropic format; providers can convert as needed.
    """
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }
