"""Tests for the RAG knowledge base system.

Covers:
- File indexing (create temp files, index, query)
- Chunking (verify overlap, size limits)
- Query relevance (indexed content ranks higher)
- Remove from index
- Stats
- Graceful fallback when chromadb not installed
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

from karna.rag.chunker import Chunk, chunk_file, chunk_text
from karna.rag.embedder import TFIDFEmbedder, get_embedder
from karna.rag.store import KnowledgeStore

# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


def _run(coro):
    """Run an async coroutine synchronously.

    Python 3.10+ deprecated ``asyncio.get_event_loop()`` when no loop is
    running — it raises ``RuntimeError`` on fresh pytest workers. Use
    ``asyncio.run()`` which creates + tears down a loop each call.
    """
    return asyncio.run(coro)


# ------------------------------------------------------------------ #
#  Chunking tests
# ------------------------------------------------------------------ #


class TestChunking:
    """Test the document chunker."""

    def test_empty_text_returns_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_short_text_single_chunk(self) -> None:
        text = "Hello, this is a short document."
        chunks = chunk_text(text, source_path="test.txt")
        assert len(chunks) == 1
        assert chunks[0].source_path == "test.txt"
        assert chunks[0].chunk_index == 0
        assert "Hello" in chunks[0].text

    def test_multiple_paragraphs_create_chunks(self) -> None:
        # Create text with several paragraphs that exceed the chunk limit.
        paragraphs = []
        for i in range(20):
            paragraphs.append(f"Paragraph {i}. " + "word " * 100)
        text = "\n\n".join(paragraphs)

        chunks = chunk_text(text, source_path="big.md", chunk_tokens=128, overlap_tokens=32)
        assert len(chunks) > 1, "Should produce multiple chunks"

    def test_chunk_overlap(self) -> None:
        """Adjacent chunks should share some overlapping content."""
        paragraphs = []
        for i in range(10):
            paragraphs.append(f"Section {i}: " + "content " * 60)
        text = "\n\n".join(paragraphs)

        chunks = chunk_text(text, chunk_tokens=128, overlap_tokens=32)
        if len(chunks) >= 2:
            # Check that the end of chunk N appears at the start of chunk N+1.
            first_end_words = set(chunks[0].text.split()[-10:])
            second_start_words = set(chunks[1].text.split()[:30])
            overlap = first_end_words & second_start_words
            assert len(overlap) > 0, "Chunks should have overlapping content"

    def test_chunk_metadata_has_line_ranges(self) -> None:
        text = "Line one.\n\nLine three.\n\nLine five."
        chunks = chunk_text(text, source_path="lines.txt")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_chunk_source_label(self) -> None:
        chunk = Chunk(text="hello", source_path="foo.py", start_line=1, end_line=5, chunk_index=0)
        assert chunk.source_label == "foo.py:1-5"


class TestChunkFile:
    """Test chunking from actual files."""

    def test_index_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("# Title\n\nSome content here.\n\n## Section\n\nMore content.")
        chunks = chunk_file(f)
        assert len(chunks) >= 1
        assert chunks[0].source_path == str(f)

    def test_skip_binary_file(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02" * 2000)
        chunks = chunk_file(f)
        assert chunks == []

    def test_skip_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        chunks = chunk_file(f)
        assert chunks == []


# ------------------------------------------------------------------ #
#  Embedder tests
# ------------------------------------------------------------------ #


class TestTFIDFEmbedder:
    """Test the TF-IDF fallback embedder."""

    def test_dimension(self) -> None:
        emb = TFIDFEmbedder(dim=128)
        assert emb.dimension == 128

    def test_embed_returns_correct_shape(self) -> None:
        emb = TFIDFEmbedder(dim=64)
        texts = ["hello world", "foo bar baz"]
        vectors = emb.embed(texts)
        assert len(vectors) == 2
        assert len(vectors[0]) == 64
        assert len(vectors[1]) == 64

    def test_embed_query(self) -> None:
        emb = TFIDFEmbedder(dim=64)
        emb.fit(["hello world", "foo bar"])
        vec = emb.embed_query("hello")
        assert len(vec) == 64
        assert any(v != 0.0 for v in vec)

    def test_similar_texts_have_higher_similarity(self) -> None:
        """Documents about the same topic should be more similar."""
        from karna.rag.store import _cosine_similarity

        emb = TFIDFEmbedder(dim=128)
        corpus = [
            "python programming language",
            "python code development",
            "cooking recipes for dinner",
        ]
        emb.fit(corpus)
        vecs = emb.embed(corpus)

        sim_related = _cosine_similarity(vecs[0], vecs[1])
        sim_unrelated = _cosine_similarity(vecs[0], vecs[2])
        assert sim_related > sim_unrelated, "Related texts should have higher similarity"


class TestGetEmbedder:
    """Test the embedder factory."""

    def test_fallback_to_tfidf(self) -> None:
        """When sentence-transformers is not installed, should fall back to TF-IDF."""
        with mock.patch.dict("sys.modules", {"sentence_transformers": None}):
            emb = get_embedder()
            assert isinstance(emb, TFIDFEmbedder)


# ------------------------------------------------------------------ #
#  KnowledgeStore tests
# ------------------------------------------------------------------ #


class TestKnowledgeStore:
    """Test the unified knowledge store."""

    def test_index_file(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        doc = tmp_path / "doc.md"
        doc.write_text("# Guide\n\nThis is a guide about configuration.\n\n## Setup\n\nRun the setup script.")

        store = KnowledgeStore(store_dir=store_dir)
        count = _run(store.index_file(doc))
        assert count >= 1

    def test_index_directory(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("Alpha content about deployment.")
        (docs_dir / "b.txt").write_text("Beta content about testing.")
        (docs_dir / "image.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

        store = KnowledgeStore(store_dir=store_dir)
        count = _run(store.index_directory(docs_dir))
        assert count >= 2  # at least one chunk per text file

    def test_query_returns_relevant_chunks(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "deploy.md").write_text(
            "# Deployment Guide\n\n"
            "Deploy using Docker containers. Run docker-compose up. "
            "Configure the environment variables in .env file."
        )
        (docs_dir / "cooking.md").write_text(
            "# Cooking Guide\n\n"
            "To make pasta, boil water first. Add salt and olive oil. "
            "Cook for 8 minutes until al dente."
        )

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_directory(docs_dir))

        results = _run(store.query("how to deploy with docker", top_k=2))
        assert len(results) >= 1
        # The deployment doc should rank higher than cooking.
        assert "deploy" in results[0].text.lower() or "docker" in results[0].text.lower()

    def test_remove_file(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        doc = tmp_path / "removeme.md"
        doc.write_text("Content to be removed from the index.")

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_file(doc))

        stats_before = store.stats()
        assert stats_before["total_files"] >= 1

        removed = _run(store.remove(doc))
        assert removed >= 1

        stats_after = store.stats()
        assert stats_after["total_files"] < stats_before["total_files"]

    def test_stats(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        doc = tmp_path / "stats_test.md"
        doc.write_text("Some content for testing stats.")

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_file(doc))

        stats = store.stats()
        assert stats["total_files"] >= 1
        assert stats["total_chunks"] >= 1
        assert stats["store_size_bytes"] > 0
        assert stats["store_dir"] == str(store_dir)
        assert stats["backend"] in ("json", "chromadb")

    def test_list_indexed(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        doc = tmp_path / "listed.txt"
        doc.write_text("Listed content.")

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_file(doc))

        indexed = store.list_indexed()
        assert len(indexed) >= 1
        assert any(str(doc) in f.path for f in indexed)

    def test_skip_already_indexed_file(self, tmp_path: Path) -> None:
        """Re-indexing an unchanged file should be a no-op."""
        store_dir = tmp_path / "store"
        doc = tmp_path / "cached.md"
        doc.write_text("Cached content.")

        store = KnowledgeStore(store_dir=store_dir)
        count1 = _run(store.index_file(doc))
        count2 = _run(store.index_file(doc))  # should return cached count
        assert count1 == count2

    def test_skip_non_text_files(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        store = KnowledgeStore(store_dir=store_dir)
        count = _run(store.index_file(img))
        assert count == 0

    def test_skip_large_files(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        big = tmp_path / "huge.txt"
        big.write_text("x" * (3 * 1024 * 1024))  # 3 MB

        store = KnowledgeStore(store_dir=store_dir)
        count = _run(store.index_file(big))
        assert count == 0


# ------------------------------------------------------------------ #
#  Fallback backend test
# ------------------------------------------------------------------ #


class TestFallbackBackend:
    """Test that the JSON fallback works when chromadb is not installed."""

    def test_json_backend_used_when_chromadb_unavailable(self, tmp_path: Path) -> None:
        with mock.patch.dict("sys.modules", {"chromadb": None}):
            store = KnowledgeStore(store_dir=tmp_path / "fallback")
            stats = store.stats()
            assert stats["backend"] == "json"

    def test_json_backend_persistence(self, tmp_path: Path) -> None:
        """Data should survive across store instances."""
        store_dir = tmp_path / "persist"
        doc = tmp_path / "persist_doc.txt"
        doc.write_text("Persistent content for testing.")

        with mock.patch.dict("sys.modules", {"chromadb": None}):
            store1 = KnowledgeStore(store_dir=store_dir)
            _run(store1.index_file(doc))
            assert store1.stats()["total_files"] == 1

            # Create a new store instance — should load from disk.
            store2 = KnowledgeStore(store_dir=store_dir)
            assert store2.stats()["total_files"] == 1
            results = _run(store2.query("persistent content"))
            assert len(results) >= 1


# ------------------------------------------------------------------ #
#  Context integration tests
# ------------------------------------------------------------------ #


class TestRAGContext:
    """Test the RAG context builder."""

    def test_build_rag_context_returns_none_for_empty_store(self, tmp_path: Path) -> None:
        from karna.rag.context import build_rag_context

        store = KnowledgeStore(store_dir=tmp_path / "empty_ctx")
        result = _run(build_rag_context("anything", store=store))
        assert result is None

    def test_build_rag_context_returns_formatted_text(self, tmp_path: Path) -> None:
        from karna.rag.context import build_rag_context

        store_dir = tmp_path / "ctx_store"
        doc = tmp_path / "ctx_doc.md"
        doc.write_text("# API Reference\n\nThe API endpoint is /v1/users. It accepts GET and POST requests.")

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_file(doc))

        result = _run(build_rag_context("API endpoint", store=store))
        assert result is not None
        assert "knowledge base" in result.lower()
        assert "Source:" in result

    def test_inject_rag_context_modifies_sections(self, tmp_path: Path) -> None:
        from karna.rag.context import inject_rag_context

        store_dir = tmp_path / "inject_store"
        doc = tmp_path / "inject_doc.md"
        doc.write_text("Configuration is done via config.toml in the project root.")

        store = KnowledgeStore(store_dir=store_dir)
        _run(store.index_file(doc))

        sections: list[tuple[str, str, int]] = []
        injected = _run(inject_rag_context("configuration", sections, store=store))
        assert injected is True
        assert len(sections) == 1
        assert sections[0][0] == "Knowledge Base"
