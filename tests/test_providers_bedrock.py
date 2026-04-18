"""Tests for the AWS Bedrock provider.

Covers:
- Construction with region from args, env, config file
- Per-family payload shape (Anthropic, Llama, Titan)
- Response parsing for each family
- complete() happy-path with mocked boto3 client
- list_models() fallback when boto3 or list_foundation_models fails
- Lazy ImportError when boto3 is missing
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from karna.models import Message
from karna.providers import get_provider_class
from karna.providers.bedrock import (
    _FALLBACK_MODELS,
    BedrockProvider,
    _load_boto3,
)


def _fake_streaming_body(payload: dict) -> MagicMock:
    stream = MagicMock()
    stream.read.return_value = json.dumps(payload).encode("utf-8")
    return stream


def _fake_runtime_client(response_payload: dict) -> MagicMock:
    client = MagicMock()
    client.invoke_model.return_value = {"body": _fake_streaming_body(response_payload)}
    return client


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #


def test_bedrock_registered() -> None:
    cls = get_provider_class("bedrock")
    assert cls is BedrockProvider


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #


def test_bedrock_construct_with_kwargs() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(region="us-west-2", access_key_id="AKIA", secret_access_key="sk")
    assert p.region == "us-west-2"
    assert p._access_key_id == "AKIA"


def test_bedrock_region_from_env() -> None:
    with (
        patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}, clear=False),
        patch.object(BedrockProvider, "_load_credential", return_value={}),
    ):
        p = BedrockProvider()
    assert p.region == "eu-west-1"


# --------------------------------------------------------------------------- #
#  Payload shape
# --------------------------------------------------------------------------- #


def test_bedrock_anthropic_payload() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="anthropic.claude-sonnet-4-20250514-v1:0")
    payload = p._build_payload(
        [Message(role="user", content="hi")],
        tools=None,
        system_prompt="be brief",
        max_tokens=100,
        temperature=0.5,
    )
    assert payload["anthropic_version"] == "bedrock-2023-05-31"
    assert payload["max_tokens"] == 100
    assert payload["system"] == "be brief"
    assert payload["temperature"] == 0.5
    assert payload["messages"][0]["role"] == "user"


def test_bedrock_llama_payload_flat_prompt() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="meta.llama3-1-70b-instruct-v1:0")
    payload = p._build_payload(
        [Message(role="user", content="hello")],
        tools=None,
        system_prompt="sys",
        max_tokens=50,
        temperature=0.1,
    )
    assert "prompt" in payload
    assert "<<SYS>>" in payload["prompt"]
    assert payload["max_gen_len"] == 50


def test_bedrock_titan_payload() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="amazon.titan-text-express-v1")
    payload = p._build_payload(
        [Message(role="user", content="hi")],
        tools=None,
        system_prompt=None,
        max_tokens=32,
        temperature=0.9,
    )
    assert "inputText" in payload
    assert payload["textGenerationConfig"] == {"maxTokenCount": 32, "temperature": 0.9}


# --------------------------------------------------------------------------- #
#  Response parsing
# --------------------------------------------------------------------------- #


def test_bedrock_parse_anthropic_response() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="anthropic.claude-sonnet-4-20250514-v1:0")
    text, tool_calls, usage = p._parse_response(
        {
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 7, "output_tokens": 1},
        }
    )
    assert text == "hi"
    assert tool_calls == []
    assert usage.input_tokens == 7 and usage.output_tokens == 1


def test_bedrock_parse_llama_response() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="meta.llama3-1-8b-instruct-v1:0")
    text, _, usage = p._parse_response({"generation": "ok", "prompt_token_count": 4, "generation_token_count": 2})
    assert text == "ok"
    assert usage.input_tokens == 4


def test_bedrock_parse_titan_response() -> None:
    with patch.object(BedrockProvider, "_load_credential", return_value={}):
        p = BedrockProvider(model="amazon.titan-text-express-v1")
    text, _, usage = p._parse_response(
        {
            "results": [{"outputText": "titan-out", "tokenCount": 3}],
            "inputTextTokenCount": 6,
        }
    )
    assert text == "titan-out"
    assert usage.input_tokens == 6
    assert usage.output_tokens == 3


# --------------------------------------------------------------------------- #
#  complete() — mocked runtime client
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bedrock_complete_happy_path() -> None:
    resp_payload = {
        "content": [{"type": "text", "text": "claude-on-bedrock says hi"}],
        "usage": {"input_tokens": 10, "output_tokens": 6},
    }
    fake_client = _fake_runtime_client(resp_payload)
    with (
        patch.object(BedrockProvider, "_load_credential", return_value={}),
        patch.object(BedrockProvider, "_get_runtime_client", return_value=fake_client),
    ):
        p = BedrockProvider(model="anthropic.claude-sonnet-4-20250514-v1:0")
        msg = await p.complete([Message(role="user", content="hi")])
    assert msg.content == "claude-on-bedrock says hi"
    assert p.cumulative_usage.input_tokens == 10
    # Confirm invoke_model was called with expected body shape.
    kwargs = fake_client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"
    body = json.loads(kwargs["body"])
    assert body["anthropic_version"] == "bedrock-2023-05-31"


# --------------------------------------------------------------------------- #
#  list_models
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bedrock_list_models_fallback_on_error() -> None:
    def _raise() -> None:
        raise RuntimeError("boom")

    with (
        patch.object(BedrockProvider, "_load_credential", return_value={}),
        patch.object(BedrockProvider, "_get_control_client", side_effect=RuntimeError("nope")),
    ):
        p = BedrockProvider()
        models = await p.list_models()
    assert len(models) == len(_FALLBACK_MODELS)


@pytest.mark.asyncio
async def test_bedrock_list_models_from_boto3() -> None:
    fake_client = MagicMock()
    fake_client.list_foundation_models.return_value = {
        "modelSummaries": [
            {"modelId": "anthropic.claude-3-haiku-20240307-v1:0", "modelName": "Haiku"},
            {"modelId": "meta.llama3-1-8b-instruct-v1:0", "modelName": "Llama"},
        ]
    }
    with (
        patch.object(BedrockProvider, "_load_credential", return_value={}),
        patch.object(BedrockProvider, "_get_control_client", return_value=fake_client),
    ):
        p = BedrockProvider()
        models = await p.list_models()
    ids = {m.id for m in models}
    assert "anthropic.claude-3-haiku-20240307-v1:0" in ids
    assert "meta.llama3-1-8b-instruct-v1:0" in ids


# --------------------------------------------------------------------------- #
#  Lazy-import ImportError
# --------------------------------------------------------------------------- #


def test_bedrock_lazy_import_raises_when_missing() -> None:
    saved = sys.modules.get("boto3")
    try:
        sys.modules["boto3"] = None  # type: ignore[assignment]
        with pytest.raises(ImportError, match="boto3"):
            _load_boto3()
    finally:
        if saved is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved
