"""Tests for Nellie's thinking-mode toggle.

Covers the auto-detection logic in :func:`karna.config.effective_thinking`
and makes sure the ``thinking`` / ``thinking_budget`` kwargs plumb through
to provider ``complete`` / ``stream`` methods end-to-end via a MockProvider.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from karna.config import KarnaConfig, effective_thinking
from karna.models import Conversation, Message, ModelInfo, StreamEvent, Usage
from karna.providers.base import BaseProvider

# --------------------------------------------------------------------------- #
#  effective_thinking: auto-detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5",
        "claude-opus-4-7-thinking",
        "openai/gpt-oss-120b",
        "openai/o1-mini",
        "o3-pro",
        "kimi-k2",
        "deepseek-r1",
        "deepseek/deepseek-r1-distill-70b",
        "qwen-reasoning",
    ],
)
def test_effective_thinking_auto_true(model: str) -> None:
    """Models with reasoning hints auto-default to thinking=True."""
    assert effective_thinking(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "llama-3.3-70b",
        "meta-llama/llama-3.3-70b-instruct",
        "gpt-4o",
        "gpt-4o-mini",
        "mistral-large",
        "claude-3-5-sonnet-20241022",
        "gemini-2.0-flash",
        "",  # empty/missing model never triggers
    ],
)
def test_effective_thinking_auto_false(model: str) -> None:
    """Non-reasoning models auto-default to thinking=False."""
    assert effective_thinking(model) is False


def test_effective_thinking_specific_cases() -> None:
    """Exact-case checks called out in the task spec."""
    assert effective_thinking("claude-sonnet-4-5") is True
    assert effective_thinking("llama-3.3-70b") is False
    assert effective_thinking("openai/gpt-oss-120b") is True


# --------------------------------------------------------------------------- #
#  effective_thinking: explicit overrides beat auto
# --------------------------------------------------------------------------- #


def test_explicit_true_overrides_auto_false() -> None:
    cfg = KarnaConfig(active_model="llama-3.3-70b", thinking_enabled=True)
    assert effective_thinking("llama-3.3-70b", cfg) is True


def test_explicit_false_overrides_auto_true() -> None:
    cfg = KarnaConfig(active_model="claude-sonnet-4-5", thinking_enabled=False)
    assert effective_thinking("claude-sonnet-4-5", cfg) is False


def test_none_falls_back_to_auto() -> None:
    """``thinking_enabled=None`` means 'use the per-model default'."""
    cfg = KarnaConfig(thinking_enabled=None)
    assert effective_thinking("claude-sonnet-4-5", cfg) is True
    assert effective_thinking("llama-3.3-70b", cfg) is False


def test_default_config_is_auto() -> None:
    """Fresh config ships with thinking_enabled=None (auto)."""
    cfg = KarnaConfig()
    assert cfg.thinking_enabled is None
    assert cfg.thinking_budget_tokens == 10000


# --------------------------------------------------------------------------- #
#  MockProvider — confirm thinking kwargs flow to complete/stream
# --------------------------------------------------------------------------- #


class MockProvider(BaseProvider):
    """Records the kwargs every complete()/stream() call was made with."""

    name = "mock"
    base_url = ""

    def __init__(self) -> None:
        super().__init__(max_retries=1, timeout=5.0)
        self.complete_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        self.complete_calls.append({"thinking": thinking, "thinking_budget": thinking_budget})
        return Message(role="assistant", content="ok")

    async def stream(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.stream_calls.append({"thinking": thinking, "thinking_budget": thinking_budget})
        yield StreamEvent(type="text", text="ok")
        yield StreamEvent(type="done", usage=Usage())

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock", name="mock", provider="mock")]


def test_mock_provider_receives_thinking_kwargs_when_enabled() -> None:
    provider = MockProvider()

    async def _run() -> None:
        await provider.complete([Message(role="user", content="hi")], thinking=True, thinking_budget=5000)
        async for _ in provider.stream([Message(role="user", content="hi")], thinking=True, thinking_budget=5000):
            pass

    asyncio.run(_run())

    assert provider.complete_calls == [{"thinking": True, "thinking_budget": 5000}]
    assert provider.stream_calls == [{"thinking": True, "thinking_budget": 5000}]


def test_mock_provider_thinking_defaults_to_false() -> None:
    provider = MockProvider()

    async def _run() -> None:
        await provider.complete([Message(role="user", content="hi")])
        async for _ in provider.stream([Message(role="user", content="hi")]):
            pass

    asyncio.run(_run())

    assert provider.complete_calls == [{"thinking": False, "thinking_budget": None}]
    assert provider.stream_calls == [{"thinking": False, "thinking_budget": None}]


# --------------------------------------------------------------------------- #
#  agent_loop threads thinking through to the provider
# --------------------------------------------------------------------------- #


def test_agent_loop_forwards_thinking_kwargs() -> None:
    """Streaming ``agent_loop`` forwards thinking/budget to provider.stream."""
    from karna.agents.loop import agent_loop

    provider = MockProvider()
    conv = Conversation(messages=[Message(role="user", content="hi")])

    async def _run() -> None:
        async for _ in agent_loop(
            provider,
            conv,
            tools=[],
            thinking=True,
            thinking_budget=7777,
        ):
            pass

    asyncio.run(_run())

    assert provider.stream_calls, "agent_loop should have called provider.stream at least once"
    assert provider.stream_calls[0] == {"thinking": True, "thinking_budget": 7777}


def test_agent_loop_sync_forwards_thinking_kwargs() -> None:
    """Non-streaming ``agent_loop_sync`` forwards thinking/budget to complete."""
    from karna.agents.loop import agent_loop_sync

    provider = MockProvider()
    conv = Conversation(messages=[Message(role="user", content="hi")])

    async def _run() -> None:
        await agent_loop_sync(
            provider,
            conv,
            tools=[],
            thinking=True,
            thinking_budget=1234,
        )

    asyncio.run(_run())

    assert provider.complete_calls, "agent_loop_sync should have called provider.complete"
    assert provider.complete_calls[0] == {"thinking": True, "thinking_budget": 1234}


# --------------------------------------------------------------------------- #
#  Provider-specific: payload-shape assertions (no HTTP, just _apply_thinking)
# --------------------------------------------------------------------------- #


def test_anthropic_apply_thinking_on_supported_model() -> None:
    from karna.providers.anthropic import AnthropicProvider

    prov = AnthropicProvider(model="claude-sonnet-4-5-20250929")
    payload: dict[str, Any] = {"temperature": 0.5}
    prov._apply_thinking(payload, thinking=True, thinking_budget=8000)
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 8000}
    # Extended thinking drops temperature overrides.
    assert "temperature" not in payload


def test_anthropic_apply_thinking_noop_on_legacy_model() -> None:
    from karna.providers.anthropic import AnthropicProvider

    prov = AnthropicProvider(model="claude-3-5-sonnet-20241022")
    payload: dict[str, Any] = {"temperature": 0.5}
    prov._apply_thinking(payload, thinking=True, thinking_budget=8000)
    assert "thinking" not in payload
    assert payload["temperature"] == 0.5  # untouched


def test_anthropic_apply_thinking_off() -> None:
    from karna.providers.anthropic import AnthropicProvider

    prov = AnthropicProvider(model="claude-sonnet-4-5-20250929")
    payload: dict[str, Any] = {}
    prov._apply_thinking(payload, thinking=False, thinking_budget=8000)
    assert "thinking" not in payload


def test_openai_apply_thinking_on_o3() -> None:
    from karna.providers.openai import OpenAIProvider

    prov = OpenAIProvider(model="o3-mini")
    payload: dict[str, Any] = {}
    prov._apply_thinking(payload, thinking=True)
    assert payload["reasoning_effort"] == "high"


def test_openai_apply_thinking_noop_on_gpt4o() -> None:
    from karna.providers.openai import OpenAIProvider

    prov = OpenAIProvider(model="gpt-4o")
    payload: dict[str, Any] = {}
    prov._apply_thinking(payload, thinking=True)
    assert "reasoning_effort" not in payload


def test_openrouter_apply_thinking_passthrough() -> None:
    from karna.providers.openrouter import OpenRouterProvider

    payload: dict[str, Any] = {}
    OpenRouterProvider._apply_thinking(payload, thinking=True, thinking_budget=9000)
    assert payload["reasoning"] == {"enabled": True, "max_tokens": 9000}


def test_openrouter_apply_thinking_off() -> None:
    from karna.providers.openrouter import OpenRouterProvider

    payload: dict[str, Any] = {}
    OpenRouterProvider._apply_thinking(payload, thinking=False, thinking_budget=9000)
    assert "reasoning" not in payload


def test_local_apply_thinking_passthrough() -> None:
    from karna.providers.local import LocalProvider

    payload: dict[str, Any] = {}
    LocalProvider._apply_thinking(payload, thinking=True, thinking_budget=2048)
    assert payload["reasoning"] == {"enabled": True, "max_tokens": 2048}


def test_bedrock_anthropic_payload_includes_thinking() -> None:
    from karna.providers.bedrock import BedrockProvider

    payload = BedrockProvider._build_anthropic_payload(
        messages=[Message(role="user", content="hi")],
        tools=None,
        system_prompt=None,
        max_tokens=1024,
        temperature=0.7,
        thinking=True,
        thinking_budget=4096,
    )
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    # Temperature is dropped when extended thinking is on.
    assert "temperature" not in payload


def test_bedrock_anthropic_payload_without_thinking_keeps_temperature() -> None:
    from karna.providers.bedrock import BedrockProvider

    payload = BedrockProvider._build_anthropic_payload(
        messages=[Message(role="user", content="hi")],
        tools=None,
        system_prompt=None,
        max_tokens=1024,
        temperature=0.7,
        thinking=False,
    )
    assert "thinking" not in payload
    assert payload["temperature"] == 0.7


def test_vertex_payload_includes_thinking_for_gemini_25() -> None:
    from karna.providers.vertex import VertexProvider

    prov = VertexProvider(model="gemini-2.5-pro", project_id="test")
    payload = prov._build_payload(
        messages=[Message(role="user", content="hi")],
        tools=None,
        system_prompt=None,
        max_tokens=None,
        temperature=None,
        thinking=True,
        thinking_budget=3000,
    )
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 3000}


def test_vertex_payload_noop_for_gemini_15() -> None:
    from karna.providers.vertex import VertexProvider

    prov = VertexProvider(model="gemini-1.5-pro", project_id="test")
    payload = prov._build_payload(
        messages=[Message(role="user", content="hi")],
        tools=None,
        system_prompt=None,
        max_tokens=None,
        temperature=None,
        thinking=True,
        thinking_budget=3000,
    )
    gen_cfg = payload.get("generationConfig", {})
    assert "thinkingConfig" not in gen_cfg


def test_azure_apply_thinking_on_o3_deployment() -> None:
    from karna.providers.azure import AzureOpenAIProvider

    prov = AzureOpenAIProvider(model="o3-mini")
    payload: dict[str, Any] = {}
    prov._apply_thinking(payload, thinking=True)
    assert payload["reasoning_effort"] == "high"


def test_azure_apply_thinking_off() -> None:
    from karna.providers.azure import AzureOpenAIProvider

    prov = AzureOpenAIProvider(model="gpt-4o")
    payload: dict[str, Any] = {}
    prov._apply_thinking(payload, thinking=True)
    assert "reasoning_effort" not in payload
