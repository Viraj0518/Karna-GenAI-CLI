# Third-Party Notices

This project incorporates code and patterns from:

## Hermes Agent
- Source: https://github.com/nousresearch/hermes-agent
- License: MIT
- Copyright: Nous Research
- Used: Provider abstraction patterns, tool-use loop structure

## OpenClaw
- Source: https://github.com/openclaw/openclaw
- License: MIT
- Copyright: OpenClaw contributors
- Used: Credentials management patterns, model registry design

## Claude Code (cc-src)
- Source: Anthropic's Claude Code CLI (``/c/cc-src/src/components/``)
- Used: UX patterns for search / history / quick-open dialogs in
  ``karna/tui/cc_components/search.py``. Specifically ported:
  ``HistorySearchDialog.tsx``, ``GlobalSearchDialog.tsx``,
  ``QuickOpenDialog.tsx``, ``SearchBox.tsx``, and ``TagTabs.tsx`` — all
  rewritten for Nellie's prompt_toolkit stack, skinned to the
  ``#3C73BD`` brand palette.
