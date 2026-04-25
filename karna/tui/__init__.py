"""Karna TUI — Rich-based terminal interface with streaming output.

Public API:
    run_repl(config)  — the main interactive REPL loop
"""

# Flip to False to fall back to the legacy full-screen Application REPL.
# The Hermes port runs prompt_toolkit in non-full-screen mode wrapped in
# ``patch_stdout`` so Windows Terminal's native scrollbar scrolls through
# past conversation output naturally.
_USE_HERMES_REPL = True


if _USE_HERMES_REPL:
    from karna.tui.hermes_repl import run_hermes_repl as _hermes_run_repl

    async def run_repl(config, resume_conversation=None, resume_session_id=None):  # type: ignore[no-untyped-def]
        """Dispatch to the Hermes-style REPL."""
        return await _hermes_run_repl(
            config,
            resume_conversation=resume_conversation,
            resume_session_id=resume_session_id,
        )
else:
    from karna.tui.repl import run_repl  # type: ignore[assignment]


__all__ = ["run_repl"]
