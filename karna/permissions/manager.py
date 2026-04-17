"""Permission manager — 3-tier ask/allow/deny per tool.

Controls whether a tool call is auto-approved, requires user
confirmation, or is unconditionally blocked.  Supports:

- Per-tool default levels loaded from ``config.toml``
- Regex deny/allow patterns for argument inspection (e.g. bash commands)
- Session-scoped "always allow" grants
- Named profiles: safe, standard, yolo

Ported from cc-src permission patterns with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from karna.config import KarnaConfig

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------- #
#  Core types
# ----------------------------------------------------------------------- #


class PermissionLevel(Enum):
    """Three-tier permission level for tool execution."""

    ALLOW = "allow"  # Auto-approve, no prompt
    ASK = "ask"  # Prompt user for approval
    DENY = "deny"  # Always reject


@dataclass
class PermissionRule:
    """A single permission rule binding a tool (+ optional arg pattern) to a level."""

    tool: str  # Tool name or "*" for wildcard
    pattern: str | None = None  # Regex pattern matched against serialised arguments
    level: PermissionLevel = PermissionLevel.ASK

    # Compiled regex — lazily built on first use
    _compiled: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    @property
    def regex(self) -> re.Pattern[str] | None:
        if self.pattern is None:
            return None
        if self._compiled is None:
            self._compiled = re.compile(self.pattern)
        return self._compiled


# ----------------------------------------------------------------------- #
#  Built-in profiles
# ----------------------------------------------------------------------- #

PROFILES: dict[str, dict[str, str]] = {
    "safe": {
        "default": "ask",
        "read": "allow",
        "grep": "allow",
        "glob": "allow",
        "bash": "ask",
        "write": "ask",
        "edit": "ask",
        "web_search": "allow",
        "web_fetch": "ask",
    },
    "standard": {
        "default": "ask",
        "read": "allow",
        "grep": "allow",
        "glob": "allow",
        "bash": "ask",
        "write": "ask",
        "edit": "ask",
        "web_search": "allow",
        "web_fetch": "allow",
    },
    "yolo": {
        "default": "allow",
    },
}


# ----------------------------------------------------------------------- #
#  Permission manager
# ----------------------------------------------------------------------- #


class PermissionManager:
    """Evaluate and enforce per-tool permission rules.

    Lifecycle:
        1. Created once per session with the loaded ``KarnaConfig``
        2. ``check()`` is called before every tool execution
        3. If ``check()`` returns ``ASK``, the caller must invoke
           ``request_approval()`` before proceeding
        4. Session-scoped "always" grants accumulate in ``session_allows``
    """

    def __init__(self, config: "KarnaConfig | None" = None) -> None:
        self.rules: list[PermissionRule] = self._load_rules(config)
        self.session_allows: set[str] = set()  # tool or "tool:pattern" keys

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionLevel:
        """Return the permission level for a proposed tool call.

        Evaluation order (first match wins):
            1. Deny patterns — if any deny-pattern matches, return DENY
            2. Session allows — if tool (or tool+pattern) was previously
               approved with "always", return ALLOW
            3. Allow patterns — if an allow-pattern matches the args, return ALLOW
            4. Per-tool level from config rules
            5. Wildcard ``*`` rule
            6. Fallback: ASK
        """
        arg_str = _serialise_args(tool_name, arguments)

        # 1. Deny patterns
        for rule in self.rules:
            if rule.level != PermissionLevel.DENY:
                continue
            if rule.tool not in (tool_name, "*"):
                continue
            if rule.regex and rule.regex.search(arg_str):
                return PermissionLevel.DENY

        # 2. Session allows
        if tool_name in self.session_allows:
            return PermissionLevel.ALLOW

        # 3. Allow patterns (argument-specific overrides)
        for rule in self.rules:
            if rule.level != PermissionLevel.ALLOW:
                continue
            if rule.tool != tool_name:
                continue
            if rule.pattern is not None and rule.regex and rule.regex.search(arg_str):
                return PermissionLevel.ALLOW

        # 4. Per-tool level (rules without a pattern)
        for rule in self.rules:
            if rule.pattern is not None:
                continue
            if rule.tool == tool_name:
                return rule.level

        # 5. Wildcard rule
        for rule in self.rules:
            if rule.pattern is not None:
                continue
            if rule.tool == "*":
                return rule.level

        # 6. Fallback
        return PermissionLevel.ASK

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        console: Any = None,
    ) -> bool:
        """Prompt the user for approval of a tool call.

        Displays a summary of the call and waits for ``y`` / ``n`` / ``always``.
        Returns True if approved.

        If *console* is a ``rich.console.Console``, uses Rich formatting;
        otherwise falls back to plain ``input()``.
        """
        summary = _format_call_summary(tool_name, arguments)

        if console is not None:
            try:
                console.print(f"\n[bold yellow]Permission required[/bold yellow]")
                console.print(f"  Tool:  [cyan]{tool_name}[/cyan]")
                console.print(f"  Args:  {summary}")
                response = console.input("[bold]Allow? [y/N/always] [/bold]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
        else:
            try:
                print(f"\nPermission required")
                print(f"  Tool:  {tool_name}")
                print(f"  Args:  {summary}")
                response = input("Allow? [y/N/always] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False

        if response in ("always", "a"):
            self.session_allows.add(tool_name)
            logger.info("Session-allow granted for tool: %s", tool_name)
            return True

        return response in ("y", "yes")

    # ------------------------------------------------------------------ #
    #  Profile management
    # ------------------------------------------------------------------ #

    @property
    def active_profile_name(self) -> str | None:
        """Return the name of the matching built-in profile, or None."""
        current = self._rules_as_profile_dict()
        for name, profile in PROFILES.items():
            if current == profile:
                return name
        return None

    def apply_profile(self, profile_name: str) -> None:
        """Replace current rules with a named profile."""
        if profile_name not in PROFILES:
            raise ValueError(f"Unknown profile: {profile_name!r}. Choose from: {', '.join(PROFILES)}")
        self.rules = _profile_to_rules(PROFILES[profile_name])
        self.session_allows.clear()

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _load_rules(self, config: "KarnaConfig | None") -> list[PermissionRule]:
        """Build rule list from config, falling back to *standard* profile."""
        if config is None:
            return _profile_to_rules(PROFILES["standard"])

        # Read [permissions] section from config if present
        perm_data = getattr(config, "permissions", None)
        if perm_data is None:
            return _profile_to_rules(PROFILES["standard"])

        if isinstance(perm_data, dict):
            return _load_from_dict(perm_data)

        # perm_data might be a Pydantic sub-model — dump it
        try:
            return _load_from_dict(perm_data.model_dump())
        except AttributeError:
            return _profile_to_rules(PROFILES["standard"])

    def _rules_as_profile_dict(self) -> dict[str, str]:
        """Serialise non-pattern rules into profile-comparable dict."""
        d: dict[str, str] = {}
        for rule in self.rules:
            if rule.pattern is not None:
                continue
            key = "default" if rule.tool == "*" else rule.tool
            d[key] = rule.level.value
        return d


# ----------------------------------------------------------------------- #
#  Helpers
# ----------------------------------------------------------------------- #


def _serialise_args(tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a flat string from tool arguments for pattern matching.

    For bash, uses the ``command`` argument directly.  For other tools,
    joins all string-valued arguments with spaces.
    """
    if tool_name == "bash":
        return arguments.get("command", "")

    parts: list[str] = []
    for v in arguments.values():
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts)


def _format_call_summary(tool_name: str, arguments: dict[str, Any], max_len: int = 120) -> str:
    """Build a user-facing one-line summary of the tool call."""
    if tool_name == "bash":
        cmd = arguments.get("command", "")
        if len(cmd) > max_len:
            return cmd[:max_len] + "..."
        return cmd

    parts: list[str] = []
    for k, v in arguments.items():
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f"{k}={sv}")
    summary = ", ".join(parts)
    if len(summary) > max_len:
        summary = summary[:max_len] + "..."
    return summary


def _profile_to_rules(profile: dict[str, str]) -> list[PermissionRule]:
    """Convert a profile dict to a list of PermissionRule objects."""
    rules: list[PermissionRule] = []
    for key, level_str in profile.items():
        level = PermissionLevel(level_str)
        tool = "*" if key == "default" else key
        rules.append(PermissionRule(tool=tool, level=level))
    return rules


def _load_from_dict(data: dict[str, Any]) -> list[PermissionRule]:
    """Parse a ``[permissions]`` config dict into rules.

    Expected shape::

        {
          "default": "ask",
          "rules": {"bash": "ask", "read": "allow", ...},
          "deny_patterns": [{"tool": "bash", "pattern": "rm -rf /"}],
          "allow_patterns": [{"tool": "bash", "pattern": "ls|cat|echo|pwd|..."}],
        }
    """
    rules: list[PermissionRule] = []

    # Default level
    default_str = data.get("default", "ask")
    rules.append(PermissionRule(tool="*", level=PermissionLevel(default_str)))

    # Per-tool rules
    tool_rules = data.get("rules", {})
    if isinstance(tool_rules, dict):
        for tool_name, level_str in tool_rules.items():
            rules.append(PermissionRule(tool=tool_name, level=PermissionLevel(level_str)))

    # Deny patterns
    for entry in data.get("deny_patterns", []):
        if isinstance(entry, dict):
            rules.append(
                PermissionRule(
                    tool=entry.get("tool", "*"),
                    pattern=entry.get("pattern"),
                    level=PermissionLevel.DENY,
                )
            )

    # Allow patterns
    for entry in data.get("allow_patterns", []):
        if isinstance(entry, dict):
            rules.append(
                PermissionRule(
                    tool=entry.get("tool", "*"),
                    pattern=entry.get("pattern"),
                    level=PermissionLevel.ALLOW,
                )
            )

    return rules
