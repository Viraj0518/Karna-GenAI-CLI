"""Abstract base class for all Karna tools.

Every concrete tool must define ``name``, ``description``,
``parameters`` (JSON Schema dict), and implement ``execute()``.

"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Base class for agent tools.

    Subclasses must set ``name``, ``description``, and ``parameters``
    (a JSON Schema dict describing the tool's input), then implement
    the async ``execute`` method.

    Tools with ``sequential = True`` must never run concurrently with
    other tool calls — they are executed one-at-a-time even when the
    model requests multiple tools in a single turn.  File-mutating
    tools (bash, write, edit) should set this flag.
    """

    name: str = ""
    description: str = ""
    # Verbatim upstream reference tool prompt when available (see
    # ``karna/prompts/cc_tool_prompts.py``). Takes precedence over
    # ``description`` for the model-facing surfaces: API tool schemas
    # (OpenAI / Anthropic) and the system-prompt tool-docs section.
    # ``description`` stays short for UI display (web, TUI, slash help).
    cc_prompt: str = ""
    parameters: dict[str, Any] = {}  # JSON Schema
    sequential: bool = False  # If True, never run in parallel with other calls

    @property
    def model_facing_description(self) -> str:
        """Rich LLM-facing prompt — prefer ``cc_prompt`` when set."""
        return self.cc_prompt or self.description

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
                "description": self.model_facing_description,
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
            "description": self.model_facing_description,
            "input_schema": self.parameters,
        }
