"""3-tier permission system for Karna/Nellie.

Exports the core types and manager for controlling tool execution
permissions: ALLOW (auto-approve), ASK (prompt user), DENY (block).

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
