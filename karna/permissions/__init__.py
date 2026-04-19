"""3-tier permission system for Karna/Nellie.

Exports the core types and manager for controlling tool execution
permissions: ALLOW (auto-approve), ASK (prompt user), DENY (block).

Ported from cc-src permission patterns with attribution to the
Anthropic Claude Code codebase.
"""

from karna.permissions.manager import (
    PROFILES,
    PermissionLevel,
    PermissionManager,
    PermissionRule,
)

__all__ = [
    "PermissionLevel",
    "PermissionManager",
    "PermissionRule",
    "PROFILES",
]
