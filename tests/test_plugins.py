"""Tests for the minimal plugin loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from karna.plugins import KarnaContext, PluginLoader, PluginManifestError


def _write_plugin(
    root: Path,
    name: str,
    *,
    module_body: str,
    entry: str = "my_plugin:register",
    version: str = "0.1.0",
) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent(
            f"""
            [plugin]
            name = "{name}"
            version = "{version}"
            entry = "{entry}"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    # Entry module name is the part before the colon.
    module_name = entry.split(":", 1)[0]
    (plugin_dir / f"{module_name}.py").write_text(module_body, encoding="utf-8")
    return plugin_dir


def test_discover_empty_root(tmp_path: Path) -> None:
    loader = PluginLoader(root=tmp_path / "does-not-exist")
    assert loader.discover() == []


def test_discover_and_activate(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "hello_plugin",
        entry="hello_mod:register",
        module_body=textwrap.dedent(
            """
            def register(ctx):
                ctx.add_tool("hello-tool")
                ctx.add_skill("hello-skill")
                ctx.add_hook("hello-hook")
                ctx.add_command("hello-cmd")
            """
        ),
    )

    loader = PluginLoader(root=tmp_path)
    plugins = loader.discover()
    assert len(plugins) == 1
    assert plugins[0].name == "hello_plugin"
    assert plugins[0].version == "0.1.0"

    ctx = KarnaContext()
    loaded = loader.load(plugins[0].path)
    loader.activate(loaded, ctx)

    assert ctx.tools == ["hello-tool"]
    assert ctx.skills == ["hello-skill"]
    assert ctx.hooks == ["hello-hook"]
    assert ctx.commands == ["hello-cmd"]


def test_missing_manifest_section(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text('title = "oops"\n', encoding="utf-8")

    loader = PluginLoader(root=tmp_path)
    with pytest.raises(PluginManifestError):
        loader.load(plugin_dir)


def test_bad_entry_format(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "badentry",
        module_body="def register(ctx):\n    pass\n",
        entry="badentry_no_colon",
    )
    loader = PluginLoader(root=tmp_path)
    (plugin,) = loader.discover()
    with pytest.raises(PluginManifestError):
        loader.activate(plugin, KarnaContext())


def test_entry_target_not_callable(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "not_callable",
        entry="not_callable_mod:register",
        module_body="register = 42\n",
    )
    loader = PluginLoader(root=tmp_path)
    (plugin,) = loader.discover()
    with pytest.raises(PluginManifestError):
        loader.activate(plugin, KarnaContext())
