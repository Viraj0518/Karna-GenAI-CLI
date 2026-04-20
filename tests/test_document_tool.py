"""Tests for the document extraction tool."""

from __future__ import annotations

import csv
import os
import tempfile

import pytest

from karna.tools.document import (
    _MAX_CHARS,
    DocumentTool,
    _format_table,
    _parse_page_range,
    _truncate,
)

# ======================================================================= #
#  DocumentTool basic tests
# ======================================================================= #


class TestDocumentTool:
    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """Should return error for missing files."""
        tool = DocumentTool()
        result = await tool.execute(file_path="/nonexistent/document.pdf")
        assert "[error]" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_unsupported_format(self):
        """Should return error for unsupported file extensions."""
        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"some data")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "[error]" in result
            assert "Unsupported" in result
            assert ".xyz" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_no_path(self):
        """Should return error when no path is given."""
        tool = DocumentTool()
        result = await tool.execute()
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_csv_extraction(self):
        """Should extract CSV content as a markdown table."""
        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Age", "City"])
            writer.writerow(["Alice", "30", "New York"])
            writer.writerow(["Bob", "25", "London"])
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "CSV" in result
            assert "Name" in result
            assert "Alice" in result
            assert "Bob" in result
            # Should be formatted as a markdown table
            assert "|" in result
            assert "---" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_csv_empty(self):
        """Should handle empty CSV files."""
        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "empty" in result.lower()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_not_a_file(self):
        """Should return error for directories."""
        tool = DocumentTool()
        result = await tool.execute(file_path="/tmp")
        assert "[error]" in result
        assert "Not a file" in result

    def test_tool_format(self):
        """Should have valid OpenAI and Anthropic tool definitions."""
        tool = DocumentTool()

        oai = tool.to_openai_tool()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "document"
        assert "file_path" in oai["function"]["parameters"]["properties"]

        anth = tool.to_anthropic_tool()
        assert anth["name"] == "document"
        assert "file_path" in anth["input_schema"]["properties"]

    def test_tool_attributes(self):
        """Should have correct tool attributes."""
        tool = DocumentTool()
        assert tool.name == "document"
        assert tool.sequential is False
        assert "file_path" in tool.parameters["properties"]
        assert "pages" in tool.parameters["properties"]


# ======================================================================= #
#  Truncation tests
# ======================================================================= #


class TestTruncation:
    def test_short_text_not_truncated(self):
        """Should not truncate text under the limit."""
        text = "short text"
        assert _truncate(text) == text

    def test_long_text_truncated(self):
        """Should truncate text over the limit."""
        text = "x" * (_MAX_CHARS + 1000)
        result = _truncate(text)
        assert len(result) < len(text)
        assert "truncated" in result.lower()
        assert str(len(text)) in result

    def test_exact_limit_not_truncated(self):
        """Should not truncate text at exactly the limit."""
        text = "x" * _MAX_CHARS
        assert _truncate(text) == text

    @pytest.mark.asyncio
    async def test_large_csv_truncated(self):
        """Should truncate very large CSV files."""
        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Col1", "Col2", "Col3"])
            # Write enough rows to exceed 50K chars
            for i in range(5000):
                writer.writerow([f"value_{i}_aaaa", f"data_{i}_bbbb", f"info_{i}_cccc"])
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert len(result) <= _MAX_CHARS + 200  # allow for truncation message
            assert "truncated" in result.lower()
        finally:
            os.unlink(path)


# ======================================================================= #
#  Page range parsing tests
# ======================================================================= #


class TestPageRange:
    def test_single_page(self):
        assert _parse_page_range("3", 10) == [2]

    def test_page_range(self):
        assert _parse_page_range("1-5", 10) == [0, 1, 2, 3, 4]

    def test_out_of_range_single(self):
        assert _parse_page_range("20", 10) == []

    def test_range_clamped_to_total(self):
        result = _parse_page_range("8-15", 10)
        assert result == [7, 8, 9]

    def test_page_one(self):
        assert _parse_page_range("1", 5) == [0]


# ======================================================================= #
#  Table formatting tests
# ======================================================================= #


class TestFormatTable:
    def test_basic_table(self):
        rows = [["A", "B"], ["1", "2"]]
        result = _format_table(rows)
        assert "| A | B |" in result
        assert "| --- | --- |" in result
        assert "| 1 | 2 |" in result

    def test_empty_table(self):
        assert "(empty table)" in _format_table([])

    def test_uneven_rows(self):
        rows = [["A", "B", "C"], ["1"]]
        result = _format_table(rows)
        # Should pad shorter rows
        assert result.count("|") > 0


# ======================================================================= #
#  PDF extraction tests (requires pdfplumber)
# ======================================================================= #


class TestPDFExtraction:
    @pytest.mark.asyncio
    async def test_pdf_missing_dep(self, monkeypatch):
        """Should show install hint when pdfplumber is not available."""
        import karna.tools.document as doc_mod

        original = doc_mod._HAS_PDFPLUMBER
        monkeypatch.setattr(doc_mod, "_HAS_PDFPLUMBER", False)

        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 dummy")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "pdfplumber" in result.lower()
            assert "pip install karna[docs]" in result
        finally:
            os.unlink(path)
            monkeypatch.setattr(doc_mod, "_HAS_PDFPLUMBER", original)

    @pytest.mark.asyncio
    async def test_pdf_page_range_param(self, monkeypatch):
        """Page range parameter should be forwarded to PDF extractor."""
        import karna.tools.document as doc_mod

        calls: list[tuple] = []

        def mock_extract(path, pages):
            calls.append((path, pages))
            return "mock content"

        monkeypatch.setattr(doc_mod, "_extract_pdf", mock_extract)

        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 dummy")
            f.flush()
            path = f.name

        try:
            await tool.execute(file_path=path, pages="1-5")
            assert len(calls) == 1
            assert calls[0][1] == "1-5"
        finally:
            os.unlink(path)


# ======================================================================= #
#  Docx extraction tests (requires python-docx)
# ======================================================================= #


class TestDocxExtraction:
    @pytest.mark.asyncio
    async def test_docx_missing_dep(self, monkeypatch):
        """Should show install hint when python-docx is not available."""
        import karna.tools.document as doc_mod

        original = doc_mod._HAS_DOCX
        monkeypatch.setattr(doc_mod, "_HAS_DOCX", False)

        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(b"PK dummy")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "python-docx" in result.lower()
            assert "pip install karna[docs]" in result
        finally:
            os.unlink(path)
            monkeypatch.setattr(doc_mod, "_HAS_DOCX", original)


# ======================================================================= #
#  Pptx extraction tests (requires python-pptx)
# ======================================================================= #


class TestPptxExtraction:
    @pytest.mark.asyncio
    async def test_pptx_missing_dep(self, monkeypatch):
        """Should show install hint when python-pptx is not available."""
        import karna.tools.document as doc_mod

        original = doc_mod._HAS_PPTX
        monkeypatch.setattr(doc_mod, "_HAS_PPTX", False)

        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            f.write(b"PK dummy")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "python-pptx" in result.lower()
            assert "pip install karna[docs]" in result
        finally:
            os.unlink(path)
            monkeypatch.setattr(doc_mod, "_HAS_PPTX", original)


# ======================================================================= #
#  Xlsx extraction tests (requires openpyxl)
# ======================================================================= #


class TestXlsxExtraction:
    @pytest.mark.asyncio
    async def test_xlsx_missing_dep(self, monkeypatch):
        """Should show install hint when openpyxl is not available."""
        import karna.tools.document as doc_mod

        original = doc_mod._HAS_OPENPYXL
        monkeypatch.setattr(doc_mod, "_HAS_OPENPYXL", False)

        tool = DocumentTool()

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(b"PK dummy")
            f.flush()
            path = f.name

        try:
            result = await tool.execute(file_path=path)
            assert "openpyxl" in result.lower()
            assert "pip install karna[docs]" in result
        finally:
            os.unlink(path)
            monkeypatch.setattr(doc_mod, "_HAS_OPENPYXL", original)


# ======================================================================= #
#  Tool registry integration
# ======================================================================= #


class TestToolRegistry:
    def test_document_tool_registered(self):
        """Document tool should be available in the tool registry."""
        from karna.tools import get_tool

        tool = get_tool("document")
        assert tool.name == "document"
        assert isinstance(tool, DocumentTool)
