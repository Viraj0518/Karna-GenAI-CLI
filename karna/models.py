"""Core data models shared across Karna.

Contains Pydantic models for messages, tool interactions, conversations,
streaming events, usage tracking, and the ``Provider`` protocol that
every backend must satisfy.

Portions adapted from cc-src (Claude Code) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
#  Tool-use primitives
# --------------------------------------------------------------------------- #


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    id: str = Field(..., description="Unique call identifier")
    name: str = Field(..., description="Tool function name")
    arguments: dict[str, Any] = Field(default_factory=dict, description="JSON-decoded arguments")


class ToolResult(BaseModel):
    """Result returned after executing a tool call."""

    tool_call_id: str = Field(..., description="ID of the originating ToolCall")
    content: str = Field(default="", description="Textual result content")
    is_error: bool = Field(default=False, description="Whether the tool execution errored")


# --------------------------------------------------------------------------- #
#  Message & Conversation
# --------------------------------------------------------------------------- #


class Message(BaseModel):
    """A single message in a conversation turn."""

    role: str = Field(..., description="One of: system, user, assistant, tool")
    content: str = Field(default="", description="Text body of the message")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Tool calls made by the assistant")
    tool_results: list[ToolResult] = Field(default_factory=list, description="Results from tool execution")


class Conversation(BaseModel):
    """An ordered list of messages plus metadata."""

    messages: list[Message] = Field(default_factory=list)
    model: str = Field(default="")
    provider: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
#  Streaming events
# --------------------------------------------------------------------------- #


class StreamEvent(BaseModel):
    """A single event emitted during streaming completions.

    ``type`` determines which optional fields are populated:
    - ``text``: partial assistant text delta
    - ``tool_call_start``: beginning of a tool call (id + name)
    - ``tool_call_delta``: argument fragment for an in-progress tool call
    - ``tool_call_end``: tool call arguments complete
    - ``done``: stream finished, usage available
    - ``error``: an error occurred
    """

    type: Literal[
        "text",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "done",
        "error",
    ]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: "Usage | None" = None
    error: str | None = None


# --------------------------------------------------------------------------- #
#  Usage & cost tracking
# --------------------------------------------------------------------------- #


class Usage(BaseModel):
    """Token usage and cost for a single API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


# --------------------------------------------------------------------------- #
#  Model info
# --------------------------------------------------------------------------- #


class ModelInfo(BaseModel):
    """Metadata for a single model exposed by a provider."""

    id: str
    name: str = ""
    provider: str = ""
    context_window: int | None = None
    pricing: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
#  Pricing table (ported from hermes-agent usage_pricing.py, MIT)
# --------------------------------------------------------------------------- #

_ZERO = Decimal("0")
_ONE_MILLION = Decimal("1000000")

# Per-million-token pricing.  Keys are (provider, model_prefix).
PRICING_TABLE: dict[tuple[str, str], dict[str, Decimal]] = {
    # Anthropic
    ("anthropic", "claude-opus-4"): {
        "input": Decimal("15.00"),
        "output": Decimal("75.00"),
    },
    ("anthropic", "claude-sonnet-4"): {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
    },
    ("anthropic", "claude-3-5-sonnet"): {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
    },
    ("anthropic", "claude-3-5-haiku"): {
        "input": Decimal("0.80"),
        "output": Decimal("4.00"),
    },
    # OpenAI
    ("openai", "gpt-4o"): {
        "input": Decimal("2.50"),
        "output": Decimal("10.00"),
    },
    ("openai", "gpt-4o-mini"): {
        "input": Decimal("0.15"),
        "output": Decimal("0.60"),
    },
    ("openai", "gpt-4.1"): {
        "input": Decimal("2.00"),
        "output": Decimal("8.00"),
    },
    ("openai", "gpt-4.1-mini"): {
        "input": Decimal("0.40"),
        "output": Decimal("1.60"),
    },
    ("openai", "gpt-4.1-nano"): {
        "input": Decimal("0.10"),
        "output": Decimal("0.40"),
    },
    ("openai", "o3"): {
        "input": Decimal("10.00"),
        "output": Decimal("40.00"),
    },
    ("openai", "o3-mini"): {
        "input": Decimal("1.10"),
        "output": Decimal("4.40"),
    },
}


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Estimate USD cost for a completion call.

    Uses longest-prefix matching against ``PRICING_TABLE``.
    Returns ``None`` if no pricing data is available.
    """
    provider_lower = provider.lower()
    model_lower = model.lower()

    # Try longest-prefix match
    best_match: dict[str, Decimal] | None = None
    best_len = 0
    for (p, prefix), pricing in PRICING_TABLE.items():
        if p == provider_lower and prefix in model_lower and len(prefix) > best_len:
            best_match = pricing
            best_len = len(prefix)

    if best_match is None:
        return None

    input_cost = Decimal(input_tokens) * best_match.get("input", _ZERO) / _ONE_MILLION
    output_cost = Decimal(output_tokens) * best_match.get("output", _ZERO) / _ONE_MILLION
    return float(input_cost + output_cost)


# --------------------------------------------------------------------------- #
#  Provider protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class Provider(Protocol):
    """Structural interface every model provider must satisfy."""

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        """Send messages and return an assistant response."""
        ...

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[StreamEvent]:
        """Stream events from the model as an async iterator."""
        ...

    async def list_models(self) -> list[ModelInfo]:
        """Return model info for all models this provider exposes."""
        ...
