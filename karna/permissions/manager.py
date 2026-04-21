"""Permission manager — 3-tier ask/allow/deny per tool.

Controls whether a tool call is auto-approved, requires user
confirmation, or is unconditionally blocked.  Supports:

- Per-tool default levels loaded from ``config.toml``
- Regex deny/allow patterns for argument inspection (e.g. bash commands)
- Session-scoped "always allow" grants
- Named profiles: safe, standard, yolo

"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from karna.config import KarnaConfig

logger = logging.getLogger(__name__)

# Path where persistent "always allow / always deny" decisions live.
# Opt-in writes only — nothing goes here unless the user explicitly picks
# a capital ``Y``/``N`` at the prompt.
PERSISTENT_PERMISSIONS_PATH = Path.home() / ".karna" / "permissions.toml"


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

    def __init__(
        self,
        config: "KarnaConfig | None" = None,
        *,
        persistent_path: Path | None = None,
    ) -> None:
        self.rules: list[PermissionRule] = self._load_rules(config)
        self.session_allows: set[str] = set()  # tool or "tool:pattern" keys
        self.session_denies: set[str] = set()  # tool keys denied for this session
        # Persistent decisions made by the user via the "[Y]es always /
        # [N]o always" prompt.  Loaded at construction and merged on top of
        # the config-derived rules so that the user's explicit choices win.
        self.persistent_path = persistent_path or PERSISTENT_PERMISSIONS_PATH
        self._load_persistent_rules()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionLevel:
        """Return the permission level for a proposed tool call.

        Evaluation order (first match wins):
            1. Deny patterns -- if any deny-pattern matches, return DENY
            2. Session allows -- if tool (or tool+pattern) was previously
               approved with "always", return ALLOW
            3. Allow patterns -- if an allow-pattern matches the args, return ALLOW
            4. Per-tool level from config rules
            5. Wildcard ``*`` rule
            6. Fallback: ASK
        """
        # Serialise arguments into a flat string for regex matching.
        # For bash, this is just the command string.  For other tools,
        # it's all string-valued arguments joined with spaces.
        arg_str = _serialise_args(tool_name, arguments)

        # 1. Deny patterns -- checked first because deny always wins.
        # These are regex rules that match against the serialised arguments
        # (e.g., deny bash calls containing "rm -rf /").
        for rule in self.rules:
            if rule.level != PermissionLevel.DENY:
                continue
            if rule.tool not in (tool_name, "*"):
                continue
            if rule.regex and rule.regex.search(arg_str):
                return PermissionLevel.DENY

        # 2a. Session denies -- user previously said "No always" for this
        # tool in this session.  Session-scoped and does not persist past
        # REPL exit (persistent denies are handled via self.rules above).
        if tool_name in self.session_denies:
            return PermissionLevel.DENY

        # 2b. Session allows -- user previously said "always" for this tool.
        # This is a session-scoped grant that persists until the REPL exits.
        if tool_name in self.session_allows:
            return PermissionLevel.ALLOW

        # 3. Allow patterns -- argument-specific overrides.
        # E.g., allow bash calls matching "ls|cat|echo|pwd" without prompting.
        for rule in self.rules:
            if rule.level != PermissionLevel.ALLOW:
                continue
            if rule.tool != tool_name:
                continue
            if rule.pattern is not None and rule.regex and rule.regex.search(arg_str):
                return PermissionLevel.ALLOW

        # 4. Per-tool level -- rules without a pattern (bare tool name -> level).
        # E.g., "read" -> ALLOW, "bash" -> ASK from the active profile.
        for rule in self.rules:
            if rule.pattern is not None:
                continue  # skip pattern rules — already handled above
            if rule.tool == tool_name:
                return rule.level

        # 5. Wildcard rule -- the "default" entry in the permission profile.
        # Applies to any tool not explicitly listed.
        for rule in self.rules:
            if rule.pattern is not None:
                continue
            if rule.tool == "*":
                return rule.level

        # 6. Fallback -- if no rules matched at all, require user approval.
        return PermissionLevel.ASK

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        console: Any = None,
    ) -> bool:
        """Prompt the user for approval of a tool call.

        Options presented:
            * ``y`` / ``yes``     — allow this call only
            * ``Y`` / ``always``  — allow this call and remember permanently
                                    (persists to ``~/.karna/permissions.toml``)
            * ``n`` / ``no``      — deny this call only (default)
            * ``N`` / ``never``   — deny this call and remember for the session

        Returns True if approved.

        If *console* is a ``rich.console.Console``, uses Rich formatting;
        otherwise falls back to plain ``input()``.
        """
        summary = _format_call_summary(tool_name, arguments)
        prompt_text = "Allow? [y]es / [Y]es always / [n]o / [N]o always "

        # We keep case-sensitivity for the "always" variants so the user can
        # say "yes this time" vs "yes forever" with a single keystroke.
        raw: str
        if console is not None:
            try:
                console.print("\n[bold yellow]Permission required[/bold yellow]")
                console.print(f"  Tool:  [cyan]{tool_name}[/cyan]")
                console.print(f"  Args:  {summary}")
                raw = console.input(f"[bold]{prompt_text}[/bold]").strip()
            except (EOFError, KeyboardInterrupt):
                return False
        else:
            try:
                print("\nPermission required")
                print(f"  Tool:  {tool_name}")
                print(f"  Args:  {summary}")
                raw = input(prompt_text).strip()
            except (EOFError, KeyboardInterrupt):
                return False

        # Case-sensitive variants first so "Y" isn't swallowed by the
        # case-insensitive match below.
        if raw == "Y" or raw.lower() == "always":
            self.session_allows.add(tool_name)
            self._persist_decision(tool_name, PermissionLevel.ALLOW)
            logger.info("Permanent-allow granted for tool: %s", tool_name)
            return True
        if raw == "N" or raw.lower() == "never":
            self.session_denies.add(tool_name)
            logger.info("Session-deny set for tool: %s", tool_name)
            return False

        return raw.lower() in ("y", "yes")

    # ------------------------------------------------------------------ #
    #  Persistent decisions (~/.karna/permissions.toml)
    # ------------------------------------------------------------------ #

    def _load_persistent_rules(self) -> None:
        """Merge decisions from ``~/.karna/permissions.toml`` into self.rules.

        File format (TOML)::

            [allow]
            read = true
            grep = true

            [deny]
            bash = true

        Silently skipped if the file does not exist or cannot be parsed —
        persistent permissions are a convenience, not a source of truth.
        """
        path = self.persistent_path
        if not path.exists():
            return

        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover — older Python
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                logger.warning(
                    "Cannot read %s — neither tomllib nor tomli is available.",
                    path,
                )
                return

        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except Exception as exc:
            logger.warning("Failed to load persistent permissions from %s: %s", path, exc)
            return

        allow_section = data.get("allow", {})
        if isinstance(allow_section, dict):
            for tool, flag in allow_section.items():
                if flag:
                    # Remove any prior rule for this tool so the persistent
                    # decision wins, then append the new ALLOW rule.
                    self.rules = [r for r in self.rules if r.tool != tool or r.pattern is not None]
                    self.rules.append(PermissionRule(tool=tool, level=PermissionLevel.ALLOW))

        deny_section = data.get("deny", {})
        if isinstance(deny_section, dict):
            for tool, flag in deny_section.items():
                if flag:
                    self.rules = [r for r in self.rules if r.tool != tool or r.pattern is not None]
                    self.rules.append(PermissionRule(tool=tool, level=PermissionLevel.DENY))

    def _persist_decision(self, tool_name: str, level: PermissionLevel) -> None:
        """Write a permanent allow/deny decision to the permissions file.

        Only ALLOW and DENY are persistable — ASK never needs saving.  We
        rewrite the whole file each time (it's tiny) to avoid TOML-patching
        complexity.  Errors are logged but not raised — a write failure
        shouldn't crash the tool call.
        """
        if level not in (PermissionLevel.ALLOW, PermissionLevel.DENY):
            return

        path = self.persistent_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            existing = self._read_persistent_raw()

            # Move the tool out of any existing section, then add to the
            # target section.
            for section in ("allow", "deny"):
                existing.setdefault(section, {}).pop(tool_name, None)
            target_section = "allow" if level is PermissionLevel.ALLOW else "deny"
            existing.setdefault(target_section, {})[tool_name] = True

            path.write_text(_render_permissions_toml(existing), encoding="utf-8")

            # Keep the in-memory rule list in sync so subsequent check()
            # calls in this session reflect the new decision without a
            # reload.
            self.rules = [r for r in self.rules if r.tool != tool_name or r.pattern is not None]
            self.rules.append(PermissionRule(tool=tool_name, level=level))
        except Exception as exc:
            logger.warning("Failed to persist permission decision for %s: %s", tool_name, exc)

    def _read_persistent_raw(self) -> dict[str, dict[str, bool]]:
        """Read the raw dict form of the permissions file, empty if missing."""
        path = self.persistent_path
        if not path.exists():
            return {}
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return {}
        try:
            with path.open("rb") as fh:
                return tomllib.load(fh)  # type: ignore[return-value]
        except Exception:
            return {}

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


def _render_permissions_toml(data: dict[str, dict[str, bool]]) -> str:
    """Render an ``allow``/``deny`` dict into a tiny TOML file.

    We don't depend on ``tomli-w`` — the schema here is simple enough that
    a hand-rolled writer is clearer than pulling in a dependency.
    """
    lines: list[str] = []
    for section in ("allow", "deny"):
        entries = data.get(section, {})
        if not entries:
            continue
        lines.append(f"[{section}]")
        for tool, flag in sorted(entries.items()):
            if flag:
                lines.append(f"{tool} = true")
        lines.append("")  # blank line between sections
    return "\n".join(lines).rstrip() + "\n"


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
