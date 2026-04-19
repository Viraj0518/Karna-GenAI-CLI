"""Notebook tool -- read, edit, add, execute, and create Jupyter notebooks.

Supports .ipynb files via ``nbformat`` when available, falling back to
raw JSON parsing otherwise.  Execution uses ``jupyter nbconvert`` or
``papermill`` if available.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from karna.tools.base import BaseTool

# --------------------------------------------------------------------------- #
#  nbformat helpers (optional dependency)
# --------------------------------------------------------------------------- #

try:
    import nbformat as _nbformat  # type: ignore[import-untyped]

    _HAS_NBFORMAT = True
except ModuleNotFoundError:
    _nbformat = None  # type: ignore[assignment]
    _HAS_NBFORMAT = False


def _read_nb(path: Path) -> dict:
    """Read a notebook file and return the parsed dict."""
    if _HAS_NBFORMAT:
        return _nbformat.read(str(path), as_version=4)  # type: ignore[union-attr]
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_nb(path: Path, nb: dict) -> None:
    """Write a notebook dict back to disk."""
    if _HAS_NBFORMAT:
        _nbformat.write(nb, str(path))  # type: ignore[union-attr]
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(nb, fh, indent=1, ensure_ascii=False)
            fh.write("\n")


def _new_nb() -> dict:
    """Return a minimal empty notebook dict (nbformat v4)."""
    if _HAS_NBFORMAT:
        return _nbformat.v4.new_notebook()  # type: ignore[union-attr]
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": [],
    }


def _new_cell(cell_type: str = "code", source: str = "") -> dict:
    """Return a new cell dict."""
    if _HAS_NBFORMAT:
        if cell_type == "code":
            return _nbformat.v4.new_code_cell(source)  # type: ignore[union-attr]
        return _nbformat.v4.new_markdown_cell(source)  # type: ignore[union-attr]
    base: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source,
    }
    if cell_type == "code":
        base["execution_count"] = None
        base["outputs"] = []
    return base


# --------------------------------------------------------------------------- #
#  Formatting helpers
# --------------------------------------------------------------------------- #


def _format_outputs(outputs: list[dict]) -> str:
    """Flatten cell outputs to a human-readable string."""
    parts: list[str] = []
    for out in outputs:
        otype = out.get("output_type", "")
        if otype == "stream":
            text = out.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            parts.append(text)
        elif otype in ("execute_result", "display_data"):
            data = out.get("data", {})
            text = data.get("text/plain", "")
            if isinstance(text, list):
                text = "".join(text)
            parts.append(text)
        elif otype == "error":
            tb = out.get("traceback", [])
            parts.append("\n".join(tb))
    return "".join(parts).rstrip("\n")


def _format_cell(idx: int, cell: dict) -> str:
    """Format a single cell for display."""
    ctype = cell.get("cell_type", "unknown")
    source = cell.get("source", "")
    if isinstance(source, list):
        source = "".join(source)
    header = f"cell[{idx}] ({ctype}):"
    lines = [header, source]

    if ctype == "code":
        outputs = cell.get("outputs", [])
        out_text = _format_outputs(outputs)
        if out_text:
            lines.append(f"--- output ---\n{out_text}")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  NotebookTool
# --------------------------------------------------------------------------- #


class NotebookTool(BaseTool):
    """Read, edit, and execute Jupyter notebook (.ipynb) cells."""

    name = "notebook"
    description = "Read, edit, and execute Jupyter notebook (.ipynb) cells."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "edit", "add", "execute", "create"],
                "description": "Action to perform on the notebook.",
            },
            "path": {
                "type": "string",
                "description": "Path to .ipynb file.",
            },
            "cell_index": {
                "type": "integer",
                "description": "Cell index (0-based).",
            },
            "content": {
                "type": "string",
                "description": "New cell content.",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "default": "code",
                "description": "Type of cell (code or markdown).",
            },
        },
        "required": ["action", "path"],
    }

    async def execute(self, **kwargs: Any) -> str:  # noqa: C901
        action: str = kwargs["action"]
        path_str: str = kwargs["path"]
        cell_index: int | None = kwargs.get("cell_index")
        content: str | None = kwargs.get("content")
        cell_type: str = kwargs.get("cell_type", "code")

        path = Path(os.path.expanduser(path_str)).resolve()

        try:
            if action == "create":
                return self._create(path)
            if action == "read":
                return self._read(path, cell_index)
            if action == "edit":
                return self._edit(path, cell_index, content)
            if action == "add":
                return self._add(path, cell_index, content, cell_type)
            if action == "execute":
                return self._execute(path, cell_index)
            return f"[error] Unknown action: {action}"
        except FileNotFoundError:
            return f"[error] Notebook not found: {path}"
        except json.JSONDecodeError as exc:
            return f"[error] Invalid notebook JSON: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[error] {exc}"

    # ------------------------------------------------------------------ #
    #  Action implementations
    # ------------------------------------------------------------------ #

    def _create(self, path: Path) -> str:
        if path.exists():
            return f"[error] File already exists: {path}"
        path.parent.mkdir(parents=True, exist_ok=True)
        nb = _new_nb()
        _write_nb(path, nb)
        return f"Notebook created: {path}"

    def _read(self, path: Path, cell_index: int | None) -> str:
        nb = _read_nb(path)
        cells = nb.get("cells", [])
        if not cells:
            return f"Notebook {path.name} has no cells."

        if cell_index is not None:
            if cell_index < 0 or cell_index >= len(cells):
                return f"[error] cell_index {cell_index} out of range (notebook has {len(cells)} cells)."
            return _format_cell(cell_index, cells[cell_index])

        parts = [f"Notebook: {path.name} ({len(cells)} cells)\n"]
        for i, cell in enumerate(cells):
            parts.append(_format_cell(i, cell))
        return "\n\n".join(parts)

    def _edit(self, path: Path, cell_index: int | None, content: str | None) -> str:
        if cell_index is None:
            return "[error] cell_index is required for edit."
        if content is None:
            return "[error] content is required for edit."

        nb = _read_nb(path)
        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"[error] cell_index {cell_index} out of range (notebook has {len(cells)} cells)."

        cells[cell_index]["source"] = content
        # Clear outputs on edit for code cells
        if cells[cell_index].get("cell_type") == "code":
            cells[cell_index]["outputs"] = []
            cells[cell_index]["execution_count"] = None
        _write_nb(path, nb)
        return f"Cell {cell_index} updated."

    def _add(
        self,
        path: Path,
        cell_index: int | None,
        content: str | None,
        cell_type: str,
    ) -> str:
        nb = _read_nb(path)
        cells = nb.get("cells", [])
        new = _new_cell(cell_type, content or "")

        if cell_index is None or cell_index >= len(cells):
            cells.append(new)
            idx = len(cells) - 1
        else:
            idx = max(0, cell_index)
            cells.insert(idx, new)

        nb["cells"] = cells
        _write_nb(path, nb)
        return f"Cell added at index {idx}."

    def _execute(self, path: Path, cell_index: int | None) -> str:
        """Execute notebook cell(s) and return captured output.

        Tries ``jupyter nbconvert --execute`` first, then ``papermill``.
        If neither is installed, falls back to a simple ``exec()`` for
        code cells (single cell only).
        """
        if cell_index is not None:
            return self._execute_single(path, cell_index)
        return self._execute_full(path)

    def _execute_full(self, path: Path) -> str:
        """Execute the entire notebook via nbconvert or papermill."""
        # Try jupyter nbconvert
        out_path = path.with_suffix(".out.ipynb")
        result = subprocess.run(  # noqa: S603
            [
                "jupyter",
                "nbconvert",
                "--to",
                "notebook",
                "--execute",
                "--output",
                str(out_path),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            nb = _read_nb(out_path)
            # Copy outputs back
            _write_nb(path, nb)
            try:
                out_path.unlink()
            except OSError:
                pass
            return self._read(path, None)

        # Try papermill
        result2 = subprocess.run(  # noqa: S603
            ["papermill", str(path), str(out_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result2.returncode == 0:
            nb = _read_nb(out_path)
            _write_nb(path, nb)
            try:
                out_path.unlink()
            except OSError:
                pass
            return self._read(path, None)

        return (
            f"[error] Could not execute notebook. "
            f"nbconvert stderr: {result.stderr.strip()}\n"
            f"papermill stderr: {result2.stderr.strip()}"
        )

    def _execute_single(self, path: Path, cell_index: int) -> str:
        """Execute a single cell with simple exec() fallback."""
        nb = _read_nb(path)
        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"[error] cell_index {cell_index} out of range (notebook has {len(cells)} cells)."
        cell = cells[cell_index]
        if cell.get("cell_type") != "code":
            return f"[error] Cell {cell_index} is not a code cell."

        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)

        # Capture stdout via exec
        import contextlib
        import io

        stdout_buf = io.StringIO()
        local_ns: dict[str, Any] = {}
        try:
            with contextlib.redirect_stdout(stdout_buf):
                exec(source, {"__builtins__": __builtins__}, local_ns)  # noqa: S102
            captured = stdout_buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            captured = f"Error: {exc}"

        # Store the output back
        cell["outputs"] = [
            {
                "output_type": "stream",
                "name": "stdout",
                "text": captured,
            }
        ]
        cell["execution_count"] = 1
        _write_nb(path, nb)

        result_text = _format_cell(cell_index, cell)
        return result_text
