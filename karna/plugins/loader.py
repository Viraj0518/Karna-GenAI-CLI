"""Minimal plugin loader for Karna.

A plugin is a directory containing a ``plugin.toml`` manifest::

    [plugin]
    name    = "my-plugin"
    version = "0.1.0"
    entry   = "my_plugin_module:register"   # "dotted.path:callable"

The loader:

1. Discovers subdirectories of ``~/.karna/plugins/`` (override via
   ``PluginLoader(root=...)``).
2. Parses each ``plugin.toml`` into a :class:`Plugin`.
3. Imports the entry module (adding the plugin directory to ``sys.path``
   temporarily) and resolves the callable.
4. Invokes ``entry(ctx)`` where *ctx* is a :class:`KarnaContext` exposing
   ``add_tool``, ``add_skill``, ``add_hook``, ``add_command``.

The loader is intentionally side-effect-light. It does **not** sandbox
untrusted code -- that responsibility sits in ``karna/permissions/`` and
``karna/security/``.

TODO(karna-cli): wire ``PluginLoader`` into ``karna/cli.py`` at startup so
discovered plugins' tools/skills/hooks/commands are registered on the
live agent. Owner of ``cli.py`` must take this on; this module
intentionally avoids importing from ``karna.cli`` to prevent cycles.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_PLUGIN_ROOT = Path.home() / ".karna" / "plugins"


class PluginManifestError(ValueError):
    """Raised when a plugin's ``plugin.toml`` is missing required fields
    or specifies an entry point that cannot be resolved."""


@dataclass
class Plugin:
    """An on-disk plugin that has been discovered but may not yet be loaded."""

    name: str
    version: str
    entry: str  # "dotted.module:callable"
    path: Path  # directory containing plugin.toml

    # Populated by ``PluginLoader.load``:
    entry_callable: Callable[["KarnaContext"], None] | None = None


@dataclass
class KarnaContext:
    """Context object passed to a plugin's ``register(ctx)`` callable.

    Plugins call ``ctx.add_tool(...)`` etc. to extend Karna. Each method
    takes a callable or object with a known interface and appends it to
    the registry lists. The host (cli.py) reads those lists and wires
    the contributions into the live agent.
    """

    tools: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)
    hooks: list[Any] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)

    def add_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def add_skill(self, skill: Any) -> None:
        self.skills.append(skill)

    def add_hook(self, hook: Any) -> None:
        self.hooks.append(hook)

    def add_command(self, command: Any) -> None:
        self.commands.append(command)


class PluginLoader:
    """Discovers, loads, and activates plugins from a root directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_PLUGIN_ROOT

    # -- discovery -----------------------------------------------------

    def discover(self) -> list[Plugin]:
        """Return a list of plugins found under ``self.root``.

        Silently returns ``[]`` if the root does not exist (a fresh
        install has no plugins). Subdirectories without ``plugin.toml``
        are skipped.
        """
        if not self.root.exists():
            return []

        plugins: list[Plugin] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            manifest = child / "plugin.toml"
            if not manifest.exists():
                continue
            plugins.append(self._parse_manifest(child, manifest))
        return plugins

    # -- loading -------------------------------------------------------

    def load(self, plugin_dir: Path) -> Plugin:
        """Parse a plugin's manifest and resolve its entry callable."""
        manifest = plugin_dir / "plugin.toml"
        if not manifest.exists():
            raise PluginManifestError(f"no plugin.toml in {plugin_dir}")
        plugin = self._parse_manifest(plugin_dir, manifest)
        plugin.entry_callable = self._resolve_entry(plugin)
        return plugin

    # -- activation ----------------------------------------------------

    def activate(self, plugin: Plugin, karna_ctx: KarnaContext) -> None:
        """Invoke the plugin's ``register(ctx)`` callable."""
        if plugin.entry_callable is None:
            plugin.entry_callable = self._resolve_entry(plugin)
        plugin.entry_callable(karna_ctx)

    # -- internals -----------------------------------------------------

    def _parse_manifest(self, plugin_dir: Path, manifest: Path) -> Plugin:
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - re-wrapped below
            raise PluginManifestError(f"failed to parse {manifest}: {exc}") from exc

        section = data.get("plugin")
        if not isinstance(section, dict):
            raise PluginManifestError(f"{manifest} is missing [plugin] section")

        for required in ("name", "version", "entry"):
            if required not in section:
                raise PluginManifestError(f"{manifest} missing [plugin].{required}")

        return Plugin(
            name=str(section["name"]),
            version=str(section["version"]),
            entry=str(section["entry"]),
            path=plugin_dir,
        )

    def _resolve_entry(self, plugin: Plugin) -> Callable[[KarnaContext], None]:
        if ":" not in plugin.entry:
            raise PluginManifestError(
                f"plugin {plugin.name}: entry '{plugin.entry}' must be 'module:callable'"
            )
        module_path, _, attr = plugin.entry.partition(":")

        plugin_dir_str = str(plugin.path)
        added = False
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            added = True
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001
            raise PluginManifestError(
                f"plugin {plugin.name}: cannot import '{module_path}': {exc}"
            ) from exc
        finally:
            if added:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass

        if not hasattr(module, attr):
            raise PluginManifestError(
                f"plugin {plugin.name}: module '{module_path}' has no attribute '{attr}'"
            )
        callable_ = getattr(module, attr)
        if not callable(callable_):
            raise PluginManifestError(
                f"plugin {plugin.name}: '{plugin.entry}' is not callable"
            )
        return callable_
