"""Tests for karna.tools.voice.VoiceTool."""

from __future__ import annotations

import pytest

from karna.tools import voice as voice_mod
from karna.tools.base import BaseTool
from karna.tools.voice import VoiceTool


class TestImportability:
    def test_module_imports_without_pyttsx3(self):
        # The module itself must import even when optional deps are missing.
        assert VoiceTool is not None

    def test_flags_are_booleans(self):
        assert isinstance(voice_mod._TTS_AVAILABLE, bool)
        assert isinstance(voice_mod._STT_AVAILABLE, bool)


class TestBaseToolContract:
    def test_has_name_and_description(self):
        t = VoiceTool()
        assert t.name == "voice"
        assert t.description
        assert "speak" in t.description.lower()
        assert "listen" in t.description.lower()

    def test_is_base_tool(self):
        assert isinstance(VoiceTool(), BaseTool)

    def test_parameters_is_valid_json_schema(self):
        params = VoiceTool().parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert params["properties"]["action"]["enum"] == ["speak", "listen"]

    def test_to_openai_tool(self):
        oai = VoiceTool().to_openai_tool()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "voice"

    def test_to_anthropic_tool(self):
        anth = VoiceTool().to_anthropic_tool()
        assert anth["name"] == "voice"


class TestInstallHint:
    @pytest.mark.asyncio
    async def test_speak_returns_install_hint_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(voice_mod, "_TTS_AVAILABLE", False)
        t = VoiceTool()
        out = await t.execute(action="speak", text="hello")
        assert "pip install" in out

    @pytest.mark.asyncio
    async def test_listen_returns_install_hint_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(voice_mod, "_STT_AVAILABLE", False)
        t = VoiceTool()
        out = await t.execute(action="listen", timeout=1)
        assert "pip install" in out


class TestArgValidation:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        t = VoiceTool()
        out = await t.execute(action="sing")
        assert "unknown action" in out.lower() or "error" in out.lower()

    @pytest.mark.asyncio
    async def test_speak_empty_text(self, monkeypatch):
        monkeypatch.setattr(voice_mod, "_TTS_AVAILABLE", True)
        t = VoiceTool()
        out = await t.execute(action="speak", text="")
        assert "error" in out.lower()
