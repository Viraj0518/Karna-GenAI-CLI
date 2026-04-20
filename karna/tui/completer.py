"""Tab completion for the Nellie REPL input buffer.

Provides context-aware completions:
- Slash commands: typing ``/`` shows all available commands
- File paths: when the input starts with ``./``, ``~/``, or ``/``
- Model names: after ``/model ``, completes provider:model strings
- Provider names: completes the provider prefix before the ``:``

Uses prompt_toolkit's ``Completer`` / ``Completion`` protocol so it
plugs directly into a ``Buffer`` or ``BufferControl``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

# -- Known model names per provider (popular defaults) ----------------------

_KNOWN_MODELS: dict[str, list[str]] = {
    "openrouter": [
        "openrouter/auto",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/o3-mini",
        "anthropic/claude-sonnet-4-20250514",
        "anthropic/claude-opus-4-20250514",
        "anthropic/claude-haiku-3.5",
        "google/gemini-2.5-pro-preview",
        "google/gemini-2.5-flash-preview",
        "meta-llama/llama-4-maverick",
        "meta-llama/llama-4-scout",
        "deepseek/deepseek-r1",
        "deepseek/deepseek-chat-v3-0324",
        "mistralai/mistral-large-2411",
    ],
    "anthropic": [
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-haiku-3.5",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        "o3",
        "o4-mini",
    ],
    "azure": [
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "vertex": [
        "gemini-2.5-pro-preview",
        "gemini-2.5-flash-preview",
    ],
    "bedrock": [
        "anthropic.claude-sonnet-4-20250514-v1:0",
        "anthropic.claude-haiku-3.5-v1:0",
    ],
    "local": [
        "llama3",
        "mistral",
        "codellama",
    ],
}

_ALL_PROVIDERS = [
    "openrouter",
    "anthropic",
    "openai",
    "azure",
    "vertex",
    "bedrock",
    "local",
    "failover",
    "moa",
    "router",
]


def _all_model_strings() -> list[str]:
    """Return all known provider:model strings."""
    results: list[str] = []
    for provider, models in _KNOWN_MODELS.items():
        for m in models:
            if ":" not in m and "/" not in m:
                results.append(f"{provider}:{m}")
            else:
                # Already qualified (e.g. "openrouter/auto") -- convert / to :
                results.append(m.replace("/", ":", 1) if "/" in m else m)
    return results


class NellieCompleter(Completer):
    """Context-aware completer for the Nellie REPL.

    Completions offered:
    1. Slash commands when the line starts with ``/``
    2. Model names after ``/model ``
    3. File/directory paths when text starts with ``./``, ``~/``, or ``/``
       (but only when *not* preceded by a slash-command prefix)
    """

    def __init__(self, slash_commands: Iterable[str] | None = None) -> None:
        # Build slash command list from the COMMANDS registry
        if slash_commands is not None:
            self._slash_commands = sorted(set(slash_commands))
        else:
            try:
                from karna.tui.slash import COMMANDS

                self._slash_commands = sorted(f"/{name}" for name in COMMANDS if name != "quit")
            except Exception:  # noqa: BLE001
                self._slash_commands = [
                    "/help",
                    "/model",
                    "/clear",
                    "/history",
                    "/cost",
                    "/compact",
                    "/tools",
                    "/skills",
                    "/memory",
                    "/sessions",
                    "/resume",
                    "/system",
                    "/paste",
                    "/copy",
                    "/loop",
                    "/plan",
                    "/do",
                    "/tasks",
                    "/exit",
                ]
        self._model_strings = _all_model_strings()

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        stripped = text.lstrip()

        # -- 1. Slash command completion (/...) --------------------------------
        if stripped.startswith("/") and " " not in stripped:
            prefix = stripped
            for cmd in self._slash_commands:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display_meta=self._slash_meta(cmd),
                    )
            return

        # -- 2. Model name completion after "/model " -------------------------
        if stripped.lower().startswith("/model "):
            model_part = stripped[len("/model ") :]

            # If no colon yet, complete provider names
            if ":" not in model_part:
                for provider in _ALL_PROVIDERS:
                    label = f"{provider}:"
                    if label.startswith(model_part) or provider.startswith(model_part):
                        yield Completion(
                            label,
                            start_position=-len(model_part),
                            display_meta="provider",
                        )
                # Also match full provider:model strings
                for ms in self._model_strings:
                    if ms.startswith(model_part):
                        yield Completion(
                            ms,
                            start_position=-len(model_part),
                        )
            else:
                # Colon present -- complete the model part
                for ms in self._model_strings:
                    if ms.startswith(model_part):
                        yield Completion(
                            ms,
                            start_position=-len(model_part),
                        )
            return

        # -- 3. File path completion (./  ~/  /path) ---------------------------
        word = document.get_word_before_cursor(WORD=True)
        if word and (word.startswith("./") or word.startswith("~/") or word.startswith("/")):
            yield from self._complete_path(word)
            return

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _slash_meta(cmd: str) -> str:
        """Return short help text for a slash command."""
        try:
            from karna.tui.slash import COMMANDS

            name = cmd.lstrip("/")
            entry = COMMANDS.get(name)
            if entry:
                return entry.help_text
        except Exception:  # noqa: BLE001
            pass
        return ""

    @staticmethod
    def _complete_path(prefix: str) -> Iterable[Completion]:
        """Yield file/directory completions for *prefix*."""
        expanded = os.path.expanduser(prefix)
        parent_str, partial = os.path.split(expanded)
        parent = Path(parent_str) if parent_str else Path(".")

        if not parent.is_dir():
            return

        try:
            entries = sorted(parent.iterdir())
        except PermissionError:
            return

        for entry in entries:
            name = entry.name
            if name.startswith(".") and not partial.startswith("."):
                continue  # hide dotfiles unless user typed a dot
            if not name.lower().startswith(partial.lower()):
                continue

            display = name + ("/" if entry.is_dir() else "")
            # Build the completion text preserving the user's prefix style
            if parent_str:
                full = os.path.join(parent_str, name)
            else:
                full = name
            if entry.is_dir():
                full += "/"

            # Reconstruct with the original prefix style (~/... vs /home/...)
            if prefix.startswith("~/"):
                home = os.path.expanduser("~")
                if full.startswith(home):
                    full = "~" + full[len(home) :]

            yield Completion(
                full,
                start_position=-len(prefix),
                display=display,
                display_meta="dir" if entry.is_dir() else "",
            )
