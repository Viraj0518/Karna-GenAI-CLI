"""Plugin system for Karna.

Provides a minimal loader that discovers plugin directories under
``~/.karna/plugins/``, reads their ``plugin.toml`` manifest, imports the
declared entry point, and calls its ``register(karna_ctx)`` function.

The loader is deliberately small: it does not sandbox, wrap in MCP, or
enforce permissions. Those concerns belong to ``karna/permissions/`` and
``karna/security/``. Plugin authors simply get a ``KarnaContext`` with
hooks to attach tools, skills, hooks, and commands.

Usage (intended to be wired into ``karna/cli.py`` at startup):

    from karna.plugins import PluginLoader, KarnaContext

    ctx = KarnaContext(...)
    loader = PluginLoader()
    for plugin in loader.discover():
        loader.activate(plugin, ctx)
"""

from karna.plugins.loader import KarnaContext, Plugin, PluginLoader, PluginManifestError

__all__ = ["KarnaContext", "Plugin", "PluginLoader", "PluginManifestError"]
