"""Voice tool -- text-to-speech and speech-to-text.

Two modes:
- ``speak``: synthesize *text* via :mod:`pyttsx3` (SAPI on Windows,
  NSSpeechSynthesizer on macOS, espeak on Linux).
- ``listen``: capture microphone audio via :mod:`speech_recognition`
  and return the transcribed text.

Both dependencies are OPTIONAL. When they aren't importable the tool
stays registered (so discovery doesn't explode) but returns a
``pip install`` hint instead of executing. The tool is deliberately
NOT wired into ``tools/__init__.py`` -- another agent owns the
registry and will add it explicitly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from karna.tools.base import BaseTool

# Optional dependencies -- absence must not break imports.
try:  # pragma: no cover - platform-dependent import
    import pyttsx3  # type: ignore

    _TTS_AVAILABLE = True
except Exception:  # pragma: no cover
    pyttsx3 = None  # type: ignore
    _TTS_AVAILABLE = False

try:  # pragma: no cover - platform-dependent import
    import speech_recognition as _sr  # type: ignore

    _STT_AVAILABLE = True
except Exception:  # pragma: no cover
    _sr = None  # type: ignore
    _STT_AVAILABLE = False


_INSTALL_HINT = (
    "Voice tool unavailable -- install optional deps: "
    "pip install 'karna[voice]' "
    "(or manually: pip install pyttsx3 SpeechRecognition)"
)


class VoiceTool(BaseTool):
    """Speak text or listen for speech, depending on ``action``."""

    name = "voice"
    description = (
        "Text-to-speech or speech-to-text. "
        "args: {action: 'speak'|'listen', text?: str, timeout?: int}. "
        "'speak' reads *text* aloud; 'listen' captures microphone audio "
        "and returns the transcription."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["speak", "listen"],
                "description": "Whether to synthesize speech or transcribe it.",
            },
            "text": {
                "type": "string",
                "description": "Text to speak. Required when action='speak'.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to listen for speech. Used when action='listen'.",
                "default": 5,
            },
        },
        "required": ["action"],
    }
    sequential = True  # audio hardware is single-user

    # ------------------------------------------------------------------ #
    #  Execute
    # ------------------------------------------------------------------ #

    async def execute(  # type: ignore[override]
        self,
        action: str = "",
        text: str = "",
        timeout: int = 5,
        **_: Any,
    ) -> str:
        action = (action or "").lower().strip()
        if action == "speak":
            return await self._speak(text)
        if action == "listen":
            return await self._listen(timeout=timeout)
        return (
            "Error: unknown action. Use action='speak' (with text=...) or action='listen' (with optional timeout=...)."
        )

    # ------------------------------------------------------------------ #
    #  speak
    # ------------------------------------------------------------------ #

    async def _speak(self, text: str) -> str:
        if not _TTS_AVAILABLE:
            return _INSTALL_HINT
        text = (text or "").strip()
        if not text:
            return "Error: 'speak' requires non-empty text."

        def _run() -> None:
            engine = pyttsx3.init()  # type: ignore[union-attr]
            try:
                engine.say(text)
                engine.runAndWait()
            finally:
                try:
                    engine.stop()
                except Exception:
                    pass

        try:
            await asyncio.to_thread(_run)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            return f"Error: TTS failed: {exc}"
        return f"Spoke {len(text)} chars."

    # ------------------------------------------------------------------ #
    #  listen
    # ------------------------------------------------------------------ #

    async def _listen(self, *, timeout: int = 5) -> str:
        if not _STT_AVAILABLE:
            return _INSTALL_HINT

        def _run() -> str:
            recognizer = _sr.Recognizer()  # type: ignore[union-attr]
            try:
                with _sr.Microphone() as source:  # type: ignore[union-attr]
                    recognizer.adjust_for_ambient_noise(source, duration=0.3)
                    audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=timeout)
            except Exception as exc:
                return f"Error: microphone unavailable: {exc}"
            try:
                return recognizer.recognize_google(audio)  # type: ignore[attr-defined]
            except Exception as exc:
                return f"Error: transcription failed: {exc}"

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:  # pragma: no cover
            return f"Error: STT failed: {exc}"
