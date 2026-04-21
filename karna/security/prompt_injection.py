"""Prompt-injection detection — pattern library + entry points.

Goose parity item per alpha B2 directive. Detects common injection patterns
in untrusted text before it's fed to the agent loop. Not a guarantee —
sophisticated attackers slip through any pattern set — but catches the
bulk of drive-by attacks and provides a structured surfacing mechanism.

Design choices:
- Pattern set is a flat list of (name, regex) tuples so the return value
  from `detect_prompt_injection` is a stable list of string names that
  callers can log / pass as Sentry tags / surface in error messages.
- No "risk score" / ML classifier — keep the decision explainable to the
  user. A match IS a signal; what the caller does with it is their call.
- Unicode-aware. Patterns use `re.IGNORECASE | re.UNICODE`. Homoglyph
  / zero-width obfuscation is handled by normalizing to NFKC before
  matching (catches cases like "IGNORE\u200bPREVIOUS").

Wiring (per alpha's directive):
- `mcp_server.server._run_nellie_agent` scans `prompt` up-front AND any
  tool-result text fed back into the loop. On hit → `halt: injection_detected`
  + `isError: True`.
- `tui` entry point scans user messages, logs a warning, but doesn't
  refuse — UX is not interrupted; the pattern name surfaces in logs so
  ops can spot campaigns.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# ─── Pattern library ────────────────────────────────────────────────────────

_FLAGS = re.IGNORECASE | re.UNICODE

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Direct instruction overrides
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\b[^.!?]{0,40}\b(?:previous|prior|above|system)\b[^.!?]{0,40}\b(?:instructions?|prompts?|rules?|directives?)",
            _FLAGS,
        ),
    ),
    (
        "ignore_all_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\b\s+(?:all|any|every)\s+(?:instructions?|prompts?|rules?|guidelines?)",
            _FLAGS,
        ),
    ),
    (
        "reveal_system_prompt",
        re.compile(
            r"\b(?:reveal|show|print|output|display|dump|reproduce|repeat|tell\s+me|give\s+me|share)\b[^.!?]{0,40}\b(?:system\s*prompt|original\s+instructions?|hidden\s+instructions?|prompt\s+template)",
            _FLAGS,
        ),
    ),
    # Role hijacking
    (
        "role_hijack_you_are_now",
        re.compile(
            r"\b(?:you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as\s+a?|pretend\s+(?:to\s+be|you\s+are)|assume\s+the\s+role\s+of|roleplay\s+as)\b[^.!?]{0,60}",
            _FLAGS,
        ),
    ),
    (
        "role_hijack_dan",
        re.compile(r"\b(?:DAN|DUDE|STAN|AIM|developer\s+mode|unfiltered\s+mode|godmode|jailbreak)\b", _FLAGS),
    ),
    (
        "role_hijack_new_persona",
        re.compile(r"\bswitch\s+(?:to|into)\s+(?:\w+\s+){0,3}(?:persona|character|mode|role)\b", _FLAGS),
    ),
    # System-prompt overrides / delimiter injection
    (
        "delim_system",
        re.compile(r"(?:^|\n)\s*(?:\[|\<|\#|---)\s*(?:system|instructions?|assistant|user)\s*(?:\]|\>|\#|---)", _FLAGS),
    ),
    ("delim_im_start", re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>", _FLAGS)),
    ("delim_chatml", re.compile(r"<\|?\s*role\s*=\s*[\"']?(?:system|assistant)[\"']?\s*\|?>", _FLAGS)),
    # Tool-call / function-call spoofing
    (
        "tool_call_spoof_json",
        re.compile(
            r"\"(?:name|tool|function)\"\s*:\s*\"(?:bash|write|edit|read|execute|shell|eval|exec|os\.system|subprocess)\"",
            _FLAGS,
        ),
    ),
    ("tool_call_spoof_xml", re.compile(r"<\s*(?:tool_?call|function_?call|invoke|use_tool)\b[^>]*>", _FLAGS)),
    (
        "tool_call_spoof_shell",
        re.compile(
            r"(?:```|`)\s*(?:bash|sh|shell|powershell|cmd)\s*\n[^`]{0,500}(?:rm\s+-rf|curl[^`]*\|\s*(?:sh|bash)|wget[^`]*\|\s*(?:sh|bash)|eval\b|exec\b)",
            _FLAGS,
        ),
    ),
    # Output manipulation
    ("base64_blob", re.compile(r"\b(?:base64|b64|atob)\b\s*[:\(][^)]{0,20}[A-Za-z0-9+/]{100,}={0,2}", _FLAGS)),
    (
        "repeat_forever",
        re.compile(
            r"\b(?:repeat|output|print|loop)\b\s+(?:the\s+word\s+)?[\"']?\w+[\"']?\s+(?:forever|infinitely|a\s+thousand\s+times|until\s+token\s+limit)",
            _FLAGS,
        ),
    ),
    # Social engineering
    (
        "urgent_override_auth",
        # Require the authority word to appear AS a subject/pronoun (to exclude
        # benign patterns like "admin needs to bypass the firewall") — must have
        # "as (the) admin" or "I am (the) admin" etc. plus an override verb.
        re.compile(
            r"\b(?:as\s+(?:the\s+)?|i\s+am\s+(?:the\s+)?|this\s+is\s+(?:the\s+)?)(?:admin|root|superuser|owner|developer|anthropic|openai|google)\b[^.!?]{0,30}\b(?:override|unlock|authoriz\w+|grant\s+access|bypass)\b",
            _FLAGS,
        ),
    ),
    (
        "grandma_trick",
        re.compile(
            r"\b(?:my\s+grandma|my\s+grandmother|my\s+late\s+\w+)\b[^.!?]{0,80}\b(?:used\s+to|always|would)\s+(?:tell|read|recite)",
            _FLAGS,
        ),
    ),
    # Data exfiltration markers
    (
        "exfil_encode_url",
        re.compile(
            r"\b(?:encode|embed|exfil\w*)\b[^.!?]{0,30}\b(?:into|in|to)\s+(?:the\s+)?(?:url|image|link|href|filename)\b",
            _FLAGS,
        ),
    ),
    (
        "exfil_send_to_attacker",
        re.compile(
            r"\b(?:send|post|upload|exfil\w*|leak)\b[^.!?]{0,30}\b(?:https?://(?!localhost|127\.0\.|0\.0\.)|attacker\.com|evil\.com|malicious\.)",
            _FLAGS,
        ),
    ),
)


# ─── Public API ─────────────────────────────────────────────────────────────


def detect_prompt_injection(text: str) -> list[str]:
    """Return the names of injection patterns matched in ``text``.

    Empty list = clean. Multi-hit possible — a single payload can trigger
    e.g. both ``ignore_previous_instructions`` and ``role_hijack_dan``.

    Runs on a unicode-normalized copy so homoglyphs and zero-width
    obfuscation don't bypass pattern matches. Original ``text`` is not
    modified.
    """
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    # Strip zero-width characters that attackers use to break simple regex
    zero_width = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060))
    stripped = normalized.translate({ord(c): None for c in zero_width})

    hits: list[str] = []
    for name, pat in _PATTERNS:
        if pat.search(stripped):
            hits.append(name)
    return hits


def is_likely_injection(text: str) -> bool:
    """Convenience wrapper: True if any pattern matched."""
    return bool(detect_prompt_injection(text))


def pattern_names() -> list[str]:
    """Return the full pattern-name registry. Useful for docs + telemetry tags."""
    return [name for name, _ in _PATTERNS]
