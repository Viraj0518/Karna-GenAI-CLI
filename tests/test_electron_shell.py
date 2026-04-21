"""Structural tests for the Electron desktop shell (Goose-parity #19).

These don't launch Electron (requires Node.js + electron binary, too
heavy for the CI matrix). Instead they verify the shell's files exist,
the package.json is valid JSON with the expected fields, and main.js
references the correct CLI surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ELECTRON_DIR = Path(__file__).resolve().parent.parent / "electron"


@pytest.fixture(scope="module")
def pkg_json() -> dict:
    return json.loads((ELECTRON_DIR / "package.json").read_text(encoding="utf-8"))


def test_electron_dir_exists():
    assert ELECTRON_DIR.is_dir(), f"missing {ELECTRON_DIR}"


def test_required_files_present():
    for name in ("package.json", "main.js", "preload.js", "README.md"):
        assert (ELECTRON_DIR / name).is_file(), f"missing electron/{name}"


def test_package_json_is_valid(pkg_json):
    assert pkg_json["name"] == "nellie-desktop"
    assert pkg_json["main"] == "main.js"
    assert "start" in pkg_json["scripts"]
    assert "build" in pkg_json["scripts"]


def test_package_json_has_electron_dep(pkg_json):
    assert "electron" in pkg_json["devDependencies"]
    assert "electron-builder" in pkg_json["devDependencies"]


def test_package_json_build_config_covers_three_os(pkg_json):
    build = pkg_json["build"]
    for os_key in ("mac", "win", "linux"):
        assert os_key in build, f"missing build.{os_key} target"
        assert "target" in build[os_key]


def test_main_js_spawns_nellie_web():
    src = (ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    # Child-process spawn path
    assert "spawn" in src
    # Invokes `nellie web` with host + port
    assert re.search(r"['\"]web['\"]", src), "main.js should spawn `nellie web`"
    assert "--host" in src
    assert "--port" in src


def test_main_js_waits_for_health():
    """We poll /health before loading the URL — ensures renderer never hits
    a half-started server."""
    src = (ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    assert "/health" in src
    assert "waitForPort" in src


def test_main_js_graceful_shutdown():
    """SIGTERM on non-Windows, taskkill on Windows — both must be present."""
    src = (ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    assert "SIGTERM" in src
    assert "taskkill" in src
    assert "before-quit" in src


def test_main_js_single_instance_lock():
    src = (ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    assert "requestSingleInstanceLock" in src


def test_preload_exposes_desktop_flag():
    src = (ELECTRON_DIR / "preload.js").read_text(encoding="utf-8")
    assert "contextBridge" in src
    assert "isDesktop" in src
    assert "contextIsolation" not in src or "true" in src.lower()


def test_main_js_hardens_renderer():
    """nodeIntegration off, contextIsolation on, sandbox on — standard
    Electron security posture."""
    src = (ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    assert re.search(r"nodeIntegration\s*:\s*false", src)
    assert re.search(r"contextIsolation\s*:\s*true", src)
    assert re.search(r"sandbox\s*:\s*true", src)
