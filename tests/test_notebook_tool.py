"""Tests for the notebook tool.

Critical behaviour under test: when neither ``jupyter nbconvert`` nor
``papermill`` is available, the tool must refuse to execute cells rather
than evaluating model-generated cell source in the host interpreter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from karna.tools import notebook as notebook_mod
from karna.tools.notebook import NotebookTool


def _write_simple_nb(path: Path, source: str = "print('hi')") -> None:
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {
                "cell_type": "code",
                "metadata": {},
                "source": source,
                "execution_count": None,
                "outputs": [],
            }
        ],
    }
    path.write_text(json.dumps(nb), encoding="utf-8")


class TestNotebookExecuteRefusesWithoutBackends:
    """When nbconvert/papermill are absent, cell execution must refuse."""

    @pytest.mark.asyncio
    async def test_single_cell_refuses_when_backends_missing(
        self, tmp_path, monkeypatch
    ):
        nb_path = tmp_path / "nb.ipynb"
        _write_simple_nb(nb_path, source="sentinel = 1")

        def missing_backend(*_args, **_kwargs):
            raise FileNotFoundError("no such tool")

        monkeypatch.setattr(notebook_mod.subprocess, "run", missing_backend)

        tool = NotebookTool()
        result = await tool.execute(
            action="execute", path=str(nb_path), cell_index=0
        )
        assert "[error]" in result
        assert "nbconvert" in result.lower()
        assert "papermill" in result.lower()

    @pytest.mark.asyncio
    async def test_full_notebook_refuses_when_backends_missing(
        self, tmp_path, monkeypatch
    ):
        nb_path = tmp_path / "nb.ipynb"
        _write_simple_nb(nb_path)

        def missing_backend(*_args, **_kwargs):
            raise FileNotFoundError("no such tool")

        monkeypatch.setattr(notebook_mod.subprocess, "run", missing_backend)

        tool = NotebookTool()
        result = await tool.execute(action="execute", path=str(nb_path))
        assert "[error]" in result
        assert "nbconvert" in result.lower() or "papermill" in result.lower()

    @pytest.mark.asyncio
    async def test_does_not_evaluate_cell_source_in_process(
        self, tmp_path, monkeypatch
    ):
        """Hostile-looking cell source must not alter host state, even
        when subprocess backends are unavailable. A regression to the
        prior in-process fallback would make this fail.
        """
        nb_path = tmp_path / "nb.ipynb"
        canary = tmp_path / "canary.txt"
        _write_simple_nb(
            nb_path,
            source=(
                "from pathlib import Path as _P\n"
                f"_P(r'{canary}').write_text('pwned')\n"
            ),
        )

        def missing_backend(*_args, **_kwargs):
            raise FileNotFoundError("no such tool")

        monkeypatch.setattr(notebook_mod.subprocess, "run", missing_backend)

        tool = NotebookTool()
        result = await tool.execute(
            action="execute", path=str(nb_path), cell_index=0
        )
        assert "[error]" in result
        assert not canary.exists(), "cell source was evaluated in-process"


class TestNotebookNonExecuteActions:
    """Non-execute actions should continue to work without any backend."""

    @pytest.mark.asyncio
    async def test_create_and_read_roundtrip(self, tmp_path):
        nb_path = tmp_path / "fresh.ipynb"
        tool = NotebookTool()
        created = await tool.execute(action="create", path=str(nb_path))
        assert "created" in created.lower()
        read = await tool.execute(action="read", path=str(nb_path))
        assert "no cells" in read.lower() or "0 cells" in read.lower()

    @pytest.mark.asyncio
    async def test_add_then_read_cell(self, tmp_path):
        nb_path = tmp_path / "fresh.ipynb"
        tool = NotebookTool()
        await tool.execute(action="create", path=str(nb_path))
        added = await tool.execute(
            action="add",
            path=str(nb_path),
            content="x = 1",
            cell_type="code",
        )
        assert "added" in added.lower()
        read = await tool.execute(
            action="read", path=str(nb_path), cell_index=0
        )
        assert "x = 1" in read
