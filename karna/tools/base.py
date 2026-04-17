"""Abstract base class for all Karna tools.

Every concrete tool must define ``name``, ``description``,
``parameters`` (JSON Schema dict), and implement ``execute()``.

Ported from cc-src/src/Tool.ts with attribution to the Anthropic
Claude Code codebase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Base class for agent tools.

    Subclasses must set ``name``, ``description``, and ``parameters``
    (a JSON Schema dict describing the tool's input), then implement
    the async ``execute`` method.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}  # JSON Schema

    # ------------------------------------------------------------------ #
    #  Core interface
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """Run the tool with the given keyword arguments.

        Returns a textual result.  Must not raise for normal operation —
        capture errors and return them as error strings so the agent can
        recover.
        """
        ...

    # ------------------------------------------------------------------ #
    #  Format converters
    # ------------------------------------------------------------------ #

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling format.

        Returns the ``{"type": "function", "function": {...}}`` envelope
        expected by the Chat Completions API.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict[str, Any]:
        """Convert to Anthropic tool-use format.

        Returns the ``{"name": ..., "description": ..., "input_schema": ...}``
        dict expected by the Anthropic Messages API.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
