"""Tests for the provider registry and provider implementations.

Phase 2A tests cover:
- Registry returns correct classes
- resolve_model() parsing
- Each provider constructs with mock key
- Each provider's complete() raises correctly without real API key
- Model aliases for OpenRouter
- Cost estimation
- Base provider retry logic
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from karna.models import Message, ModelInfo, StreamEvent, Usage, estimate_cost
from karna.providers import (
    PROVIDERS,
    get_provider,
    get_provider_class,
    resolve_model,
)
from karna.providers.base import BaseProvider, _jittered_backoff


# --------------------------------------------------------------------------- #
#  Registry tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected_class_name",
    [
        ("openrouter", "OpenRouterProvider"),
        ("openai", "OpenAIProvider"),
        ("azure", "AzureOpenAIProvider"),
        ("anthropic", "AnthropicProvider"),
        ("local", "LocalProvider"),
    ],
)
def test_registry_returns_correct_class(name: str, expected_class_name: str) -> None:
    cls = get_provider_class(name)
    assert cls.__name__ == expected_class_name
    assert issubclass(cls, BaseProvider)


def test_registry_case_insensitive() -> None:
    assert get_provider_class("OpenRouter") == get_provider_class("openrouter")


def test_registry_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown provider"):
        get_provider_class("nonexistent")


def test_providers_dict_has_all() -> None:
    assert set(PROVIDERS.keys()) == {"openrouter", "openai", "azure", "anthropic", "local"}


# --------------------------------------------------------------------------- #
#  resolve_model tests
# --------------------------------------------------------------------------- #


def test_resolve_model_with_provider() -> None:
    assert resolve_model("openrouter:gpt-oss-120b") == ("openrouter", "gpt-oss-120b")


def test_resolve_model_anthropic() -> None:
    assert resolve_model("anthropic:claude-sonnet-4-20250514") == (
        "anthropic",
        "claude-sonnet-4-20250514",
    )


def test_resolve_model_openai() -> None:
    assert resolve_model("openai:gpt-4o") == ("openai", "gpt-4o")


def test_resolve_model_azure() -> None:
    assert resolve_model("azure:gpt-4o") == ("azure", "gpt-4o")


def test_resolve_model_local() -> None:
    assert resolve_model("local:my-model") == ("local", "my-model")


def test_resolve_model_default_provider() -> None:
    """When no provider prefix is given, defaults to openrouter."""
    assert resolve_model("gpt-4o") == ("openrouter", "gpt-4o")


def test_resolve_model_unknown_prefix_treated_as_default() -> None:
    """Unknown prefix is not a known provider, so full string goes to default."""
    provider, model = resolve_model("unknown:some-model")
    assert provider == "openrouter"
    assert model == "unknown:some-model"


# --------------------------------------------------------------------------- #
#  Provider construction tests (mock key via env var)
# --------------------------------------------------------------------------- #


def test_openrouter_constructs_with_env_key() -> None:
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), \
         patch("karna.providers.openrouter.OpenRouterProvider._load_credential", return_value={}):
        p = get_provider("openrouter", model="openrouter/auto")
    assert p.name == "openrouter"
    assert p._api_key == "test-key"
    assert p.model == "openrouter/auto"


def test_anthropic_constructs_with_env_key() -> None:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        p = get_provider("anthropic", model="claude-sonnet-4-20250514")
    assert p.name == "anthropic"
    assert p._api_key == "sk-ant-test"


def test_openai_constructs_with_env_key() -> None:
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
        p = get_provider("openai", model="gpt-4o")
    assert p.name == "openai"
    assert p._api_key == "sk-test"


def test_azure_constructs_with_env() -> None:
    with patch.dict(os.environ, {
        "AZURE_OPENAI_API_KEY": "az-key",
        "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
    }):
        p = get_provider("azure", model="gpt-4o")
    assert p.name == "azure"
    assert p._api_key == "az-key"
    assert p.endpoint == "https://my-resource.openai.azure.com"


def test_local_constructs_with_defaults() -> None:
    p = get_provider("local", model="default")
    assert p.name == "local"
    assert p.base_url == "http://localhost:8080/v1"
    # Local provider doesn't require an API key
    assert p._api_key is None


# --------------------------------------------------------------------------- #
#  Provider complete() without API key raises ValueError
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_complete_raises_without_key() -> None:
    with patch.dict(os.environ, {}, clear=True):
        p = get_provider("openai", model="gpt-4o")
        p._api_key = None  # Ensure no key
    with pytest.raises(ValueError, match="No API key configured"):
        await p.complete([Message(role="user", content="test")])


@pytest.mark.asyncio
async def test_anthropic_complete_raises_without_key() -> None:
    with patch.dict(os.environ, {}, clear=True):
        p = get_provider("anthropic")
        p._api_key = None
    with pytest.raises(ValueError, match="No API key configured"):
        await p.complete([Message(role="user", content="test")])


@pytest.mark.asyncio
async def test_azure_complete_raises_without_endpoint() -> None:
    with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "az-key"}, clear=True):
        p = get_provider("azure", model="gpt-4o")
        p.endpoint = ""  # No endpoint
    with pytest.raises(ValueError, match="No Azure endpoint configured"):
        await p.complete([Message(role="user", content="test")])


# --------------------------------------------------------------------------- #
#  OpenRouter model aliases
# --------------------------------------------------------------------------- #


def test_openrouter_alias_resolution() -> None:
    from karna.providers.openrouter import _resolve_alias

    assert _resolve_alias("gpt-4o") == "openai/gpt-4o"
    assert _resolve_alias("claude-opus-4") == "anthropic/claude-opus-4-20250514"
    assert _resolve_alias("deepseek-chat") == "deepseek/deepseek-chat"
    # Unknown alias passes through unchanged
    assert _resolve_alias("some/custom-model") == "some/custom-model"


def test_openrouter_provider_resolves_alias() -> None:
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        p = get_provider("openrouter", model="gpt-4o")
    assert p.model == "openai/gpt-4o"


# --------------------------------------------------------------------------- #
#  Cost estimation
# --------------------------------------------------------------------------- #


def test_estimate_cost_anthropic() -> None:
    cost = estimate_cost("anthropic", "claude-sonnet-4-20250514", 1000, 500)
    assert cost is not None
    # 1000 input at $3/M + 500 output at $15/M
    expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
    assert abs(cost - expected) < 0.0001


def test_estimate_cost_openai() -> None:
    cost = estimate_cost("openai", "gpt-4o", 1000, 500)
    assert cost is not None
    expected = (1000 * 2.5 / 1_000_000) + (500 * 10.0 / 1_000_000)
    assert abs(cost - expected) < 0.0001


def test_estimate_cost_unknown_model() -> None:
    cost = estimate_cost("unknown", "unknown-model", 1000, 500)
    assert cost is None


# --------------------------------------------------------------------------- #
#  Usage model
# --------------------------------------------------------------------------- #


def test_usage_total_tokens() -> None:
    u = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=20)
    assert u.total_tokens == 170


# --------------------------------------------------------------------------- #
#  StreamEvent model
# --------------------------------------------------------------------------- #


def test_stream_event_text() -> None:
    evt = StreamEvent(type="text", text="hello")
    assert evt.type == "text"
    assert evt.text == "hello"


def test_stream_event_done() -> None:
    evt = StreamEvent(type="done", usage=Usage(input_tokens=10, output_tokens=5))
    assert evt.type == "done"
    assert evt.usage is not None
    assert evt.usage.total_tokens == 15


# --------------------------------------------------------------------------- #
#  ModelInfo model
# --------------------------------------------------------------------------- #


def test_model_info() -> None:
    mi = ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai", context_window=128000)
    assert mi.id == "gpt-4o"
    assert mi.context_window == 128000


# --------------------------------------------------------------------------- #
#  Base provider retry backoff
# --------------------------------------------------------------------------- #


def test_jittered_backoff_increases() -> None:
    d1 = _jittered_backoff(1, base_delay=1.0, max_delay=60.0, jitter_ratio=0.0)
    d2 = _jittered_backoff(2, base_delay=1.0, max_delay=60.0, jitter_ratio=0.0)
    d3 = _jittered_backoff(3, base_delay=1.0, max_delay=60.0, jitter_ratio=0.0)
    assert d1 <= d2 <= d3


def test_jittered_backoff_capped() -> None:
    d = _jittered_backoff(100, base_delay=1.0, max_delay=10.0, jitter_ratio=0.0)
    assert d <= 10.0


# --------------------------------------------------------------------------- #
#  Anthropic message serialization
# --------------------------------------------------------------------------- #


def test_anthropic_serialize_messages() -> None:
    from karna.providers.anthropic import AnthropicProvider

    msgs = [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi there"),
    ]
    result = AnthropicProvider._serialize_messages(msgs)
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "Hello"}
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == [{"type": "text", "text": "Hi there"}]


def test_anthropic_serialize_tools() -> None:
    from karna.providers.anthropic import AnthropicProvider

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                },
            },
        }
    ]
    result = AnthropicProvider._serialize_tools(tools)
    assert len(result) == 1
    assert result[0]["name"] == "get_weather"
    assert "input_schema" in result[0]


# --------------------------------------------------------------------------- #
#  Anthropic output limits
# --------------------------------------------------------------------------- #


def test_anthropic_output_limits() -> None:
    from karna.providers.anthropic import _get_max_output_tokens

    assert _get_max_output_tokens("claude-opus-4-20250514") == 128_000
    assert _get_max_output_tokens("claude-sonnet-4-20250514") == 64_000
    assert _get_max_output_tokens("claude-3-5-sonnet-20241022") == 8_192
    assert _get_max_output_tokens("unknown-model") == 16_384


# --------------------------------------------------------------------------- #
#  Azure URL construction
# --------------------------------------------------------------------------- #


def test_azure_completions_url() -> None:
    with patch.dict(os.environ, {
        "AZURE_OPENAI_API_KEY": "key",
        "AZURE_OPENAI_ENDPOINT": "https://myresource.openai.azure.com",
    }):
        p = get_provider("azure", model="gpt-4o")
    url = p._completions_url()
    assert "myresource.openai.azure.com" in url
    assert "deployments/gpt-4o" in url
    assert "api-version=" in url


# --------------------------------------------------------------------------- #
#  get_provider (instantiation variant) tests
# --------------------------------------------------------------------------- #


def test_get_provider_instantiates() -> None:
    """get_provider returns an instance, not a class."""
    p = get_provider("local", model="test-model")
    assert isinstance(p, BaseProvider)
    assert p.model == "test-model"


def test_get_provider_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown provider"):
        get_provider("nonexistent")
