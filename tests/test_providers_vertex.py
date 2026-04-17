"""Tests for the Vertex AI provider.

Covers:
- Construction with project_id / region from args, env, and config file
- URL construction for complete and streaming endpoints
- Message serialization (contents format, system_instruction promotion)
- complete() happy-path with mocked httpx + mocked google-auth
- list_models() fallback when auth or discovery fails
- Lazy ImportError when google-auth is missing
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from karna.models import Message
from karna.providers import get_provider_class
from karna.providers.vertex import (
    _FALLBACK_MODELS,
    VertexProvider,
    _load_google_auth,
)


class _FakeCreds:
    def __init__(self, token: str = "fake-bearer") -> None:
        self.token = token

    def refresh(self, request: object) -> None:  # noqa: D401
        self.token = self.token or "fake-bearer"


def _patch_google_auth(project: str = "test-project") -> MagicMock:
    """Patch ``_load_google_auth`` to return a fake google.auth module."""
    fake_google_auth = MagicMock()
    fake_google_auth.default.return_value = (_FakeCreds(), project)
    fake_requests = MagicMock()
    fake_requests.Request.return_value = object()
    return patch(
        "karna.providers.vertex._load_google_auth",
        return_value=(fake_google_auth, fake_requests),
    )


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #


def test_vertex_registered() -> None:
    cls = get_provider_class("vertex")
    assert cls is VertexProvider


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #


def test_vertex_construct_with_kwargs() -> None:
    with patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider(project_id="proj-a", region="us-west1")
    assert p.project_id == "proj-a"
    assert p.region == "us-west1"
    assert p.model == "gemini-2.5-pro"


def test_vertex_construct_from_env() -> None:
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-proj"}, clear=False), \
         patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider()
    assert p.project_id == "env-proj"


def test_vertex_endpoint_url() -> None:
    with patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider(project_id="proj", region="us-central1", model="gemini-2.5-pro")
    assert p._endpoint() == (
        "https://us-central1-aiplatform.googleapis.com/v1/"
        "projects/proj/locations/us-central1/publishers/google/"
        "models/gemini-2.5-pro:generateContent"
    )
    assert ":streamGenerateContent" in p._endpoint(streaming=True)


def test_vertex_require_project_raises() -> None:
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider()
    with pytest.raises(ValueError, match="no project_id configured"):
        p._require_project()


# --------------------------------------------------------------------------- #
#  Serialization
# --------------------------------------------------------------------------- #


def test_vertex_serialize_messages_promotes_system() -> None:
    msgs = [
        Message(role="system", content="you are helpful"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]
    contents = VertexProvider._serialize_messages(msgs)
    # System is skipped; roles map to user/model.
    assert [c["role"] for c in contents] == ["user", "model"]
    assert contents[0]["parts"] == [{"text": "hi"}]


def test_vertex_build_payload_has_system_instruction() -> None:
    with patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider(project_id="p")
    payload = p._build_payload(
        [Message(role="system", content="be brief"), Message(role="user", content="go")],
        tools=None,
        system_prompt=None,
        max_tokens=50,
        temperature=0.2,
    )
    assert payload["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert payload["generationConfig"] == {"maxOutputTokens": 50, "temperature": 0.2}


# --------------------------------------------------------------------------- #
#  complete() — mocked httpx + google-auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vertex_complete_happy_path() -> None:
    fake_response = {
        "candidates": [
            {"content": {"parts": [{"text": "hi from gemini"}]}}
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
    }

    async def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["contents"][0]["role"] == "user"
        return httpx.Response(200, json=fake_response)

    transport = httpx.MockTransport(_handler)

    with _patch_google_auth(), \
         patch.object(VertexProvider, "_load_credential", return_value={}), \
         patch.object(
            VertexProvider, "_make_client",
            lambda self, **kw: httpx.AsyncClient(transport=transport, **kw),
         ):
        p = VertexProvider(project_id="proj")
        msg = await p.complete([Message(role="user", content="go")])

    assert msg.role == "assistant"
    assert msg.content == "hi from gemini"
    assert p.cumulative_usage.input_tokens == 10
    assert p.cumulative_usage.output_tokens == 4


# --------------------------------------------------------------------------- #
#  list_models
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vertex_list_models_fallback_without_project() -> None:
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(VertexProvider, "_load_credential", return_value={}):
        p = VertexProvider()
    models = await p.list_models()
    assert len(models) == len(_FALLBACK_MODELS)
    assert any(m.id == "gemini-2.5-pro" for m in models)


# --------------------------------------------------------------------------- #
#  Lazy-import ImportError
# --------------------------------------------------------------------------- #


def test_vertex_lazy_import_raises_when_missing() -> None:
    # Simulate google.auth being unavailable by poisoning the module cache.
    saved = {k: sys.modules.get(k) for k in ("google", "google.auth", "google.auth.transport", "google.auth.transport.requests")}
    try:
        for k in saved:
            sys.modules[k] = None  # type: ignore[assignment]
        with pytest.raises(ImportError, match="google-auth"):
            _load_google_auth()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
