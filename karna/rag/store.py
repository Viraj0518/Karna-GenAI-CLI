"""Local vector store for the RAG knowledge base.

Provides a unified ``KnowledgeStore`` that uses ChromaDB when available
and falls back to a simple JSON + cosine similarity approach.

Storage layout::

    ~/.karna/knowledge/
        chroma/          # ChromaDB persistent directory (if using chroma)
        index.json       # fallback index (if using JSON backend)
        meta.json        # file-level metadata (path, mtime, chunk count)

The store is fully local — no network calls, no telemetry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

from karna.rag.chunker import Chunk, chunk_file
from karna.rag.embedder import BaseEmbedder, TFIDFEmbedder, get_embedder

logger = logging.getLogger(__name__)

_DEFAULT_STORE_DIR = Path.home() / ".karna" / "knowledge"

# File extensions that are considered indexable text.
_TEXT_EXTENSIONS: set[str] = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".r",
    ".R",
    ".jl",
    ".lua",
    ".vim",
    ".el",
    ".org",
    ".tex",
    ".csv",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".dockerfile",
    ".makefile",
    "",  # extensionless files (Makefile, Dockerfile, etc.)
}

# Directories to skip during recursive indexing.
_SKIP_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    ".karna",
}


# ------------------------------------------------------------------ #
#  Data types
# ------------------------------------------------------------------ #


class IndexedFile:
    """Metadata for a file that has been indexed."""

    __slots__ = ("path", "mtime", "chunk_count", "indexed_at")

    def __init__(self, path: str, mtime: float, chunk_count: int, indexed_at: float) -> None:
        self.path = path
        self.mtime = mtime
        self.chunk_count = chunk_count
        self.indexed_at = indexed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mtime": self.mtime,
            "chunk_count": self.chunk_count,
            "indexed_at": self.indexed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IndexedFile:
        return cls(
            path=d["path"],
            mtime=d.get("mtime", 0.0),
            chunk_count=d.get("chunk_count", 0),
            indexed_at=d.get("indexed_at", 0.0),
        )


# ------------------------------------------------------------------ #
#  Cosine similarity helper
# ------------------------------------------------------------------ #


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ------------------------------------------------------------------ #
#  JSON fallback backend
# ------------------------------------------------------------------ #


class _JSONBackend:
    """Zero-dependency vector store backed by a JSON file."""

    def __init__(self, store_dir: Path, embedder: BaseEmbedder) -> None:
        self._store_dir = store_dir
        self._embedder = embedder
        self._index_path = store_dir / "index.json"
        self._entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
            except (json.JSONDecodeError, OSError):
                self._entries = []

            # Re-fit the TF-IDF embedder on existing corpus
            if isinstance(self._embedder, TFIDFEmbedder) and self._entries:
                texts = [e["text"] for e in self._entries]
                self._embedder.fit(texts)

    def _save(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        data = {"entries": self._entries}
        self._index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def add(self, chunks: list[Chunk]) -> None:
        """Add chunks to the index."""
        if not chunks:
            return

        texts = [c.text for c in chunks]

        # Re-fit TF-IDF if using that backend (needs full corpus).
        if isinstance(self._embedder, TFIDFEmbedder):
            all_texts = [e["text"] for e in self._entries] + texts
            self._embedder.fit(all_texts)
            # Re-embed existing entries with updated IDF.
            for entry in self._entries:
                entry["embedding"] = self._embedder.embed_query(entry["text"])

        embeddings = self._embedder.embed(texts)
        for chunk, embedding in zip(chunks, embeddings):
            self._entries.append(
                {
                    "id": hashlib.sha256(f"{chunk.source_path}:{chunk.chunk_index}".encode()).hexdigest()[:16],
                    "text": chunk.text,
                    "embedding": embedding,
                    "source_path": chunk.source_path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "chunk_index": chunk.chunk_index,
                }
            )
        self._save()

    def query(self, text: str, top_k: int = 5) -> list[Chunk]:
        """Query the index and return the top-k most relevant chunks."""
        if not self._entries:
            return []

        query_embedding = self._embedder.embed_query(text)
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in self._entries:
            sim = _cosine_similarity(query_embedding, entry["embedding"])
            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[Chunk] = []
        for _score, entry in scored[:top_k]:
            results.append(
                Chunk(
                    text=entry["text"],
                    source_path=entry["source_path"],
                    start_line=entry["start_line"],
                    end_line=entry["end_line"],
                    chunk_index=entry["chunk_index"],
                )
            )
        return results

    def remove(self, source_path: str) -> int:
        """Remove all entries for *source_path*. Returns count removed."""
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["source_path"] != source_path]
        removed = before - len(self._entries)
        if removed:
            self._save()
        return removed

    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries = []
        self._save()


# ------------------------------------------------------------------ #
#  ChromaDB backend
# ------------------------------------------------------------------ #


class _ChromaBackend:
    """Vector store backed by ChromaDB."""

    def __init__(self, store_dir: Path, embedder: BaseEmbedder) -> None:
        import chromadb  # type: ignore[import-untyped]

        self._embedder = embedder
        chroma_dir = store_dir / "chroma"
        chroma_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name="karna_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return

        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)
        ids = [hashlib.sha256(f"{c.source_path}:{c.chunk_index}".encode()).hexdigest()[:16] for c in chunks]
        metadatas = [
            {
                "source_path": c.source_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ]

        self._collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(self, text: str, top_k: int = 5) -> list[Chunk]:
        if self._collection.count() == 0:
            return []

        query_embedding = self._embedder.embed_query(text)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._collection.count()),
        )

        chunks: list[Chunk] = []
        if results and results["documents"] and results["metadatas"]:
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                chunks.append(
                    Chunk(
                        text=doc,
                        source_path=meta.get("source_path", "<unknown>"),
                        start_line=meta.get("start_line", 0),
                        end_line=meta.get("end_line", 0),
                        chunk_index=meta.get("chunk_index", 0),
                    )
                )
        return chunks

    def remove(self, source_path: str) -> int:
        """Remove all entries for a given source path."""
        try:
            existing = self._collection.get(
                where={"source_path": source_path},
            )
        except Exception:
            return 0

        if existing and existing["ids"]:
            self._collection.delete(ids=existing["ids"])
            return len(existing["ids"])
        return 0

    def count(self) -> int:
        return self._collection.count()

    def clear(self) -> None:
        # Delete and recreate the collection.
        self._client.delete_collection("karna_knowledge")
        self._collection = self._client.get_or_create_collection(
            name="karna_knowledge",
            metadata={"hnsw:space": "cosine"},
        )


# ------------------------------------------------------------------ #
#  Unified KnowledgeStore
# ------------------------------------------------------------------ #


class KnowledgeStore:
    """Local vector store backed by ChromaDB or JSON fallback.

    Usage::

        store = KnowledgeStore()
        await store.index_file(Path("docs/guide.md"))
        chunks = await store.query("how to configure X")
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self._store_dir = store_dir or _DEFAULT_STORE_DIR
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._store_dir / "meta.json"
        self._indexed_files: dict[str, IndexedFile] = {}
        self._load_meta()

        self._embedder = get_embedder()
        self._backend: _JSONBackend | _ChromaBackend = self._init_backend()

    def _init_backend(self) -> _JSONBackend | _ChromaBackend:
        """Try ChromaDB first, fall back to JSON."""
        try:
            backend = _ChromaBackend(self._store_dir, self._embedder)
            logger.info("RAG: using ChromaDB backend")
            return backend
        except ImportError:
            logger.info("RAG: chromadb not installed, using JSON fallback")
        except Exception as exc:
            logger.warning("RAG: ChromaDB init failed (%s), using JSON fallback", exc)

        return _JSONBackend(self._store_dir, self._embedder)

    # ------------------------------------------------------------------ #
    #  Metadata persistence
    # ------------------------------------------------------------------ #

    def _load_meta(self) -> None:
        if self._meta_path.exists():
            try:
                data = json.loads(self._meta_path.read_text(encoding="utf-8"))
                for entry in data.get("files", []):
                    ifile = IndexedFile.from_dict(entry)
                    self._indexed_files[ifile.path] = ifile
            except (json.JSONDecodeError, OSError):
                self._indexed_files = {}

    def _save_meta(self) -> None:
        data = {"files": [f.to_dict() for f in self._indexed_files.values()]}
        self._meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def index_file(self, path: Path) -> int:
        """Index a single file. Returns the number of chunks created."""
        path = path.resolve()
        if not path.is_file():
            return 0

        # Skip files with non-text extensions.
        suffix = path.suffix.lower()
        # Check extensionless files by name.
        if suffix not in _TEXT_EXTENSIONS and path.name.lower() not in {
            "makefile",
            "dockerfile",
            "vagrantfile",
            "gemfile",
            "rakefile",
            "procfile",
        }:
            return 0

        # Skip very large files (> 2 MB).
        try:
            size = path.stat().st_size
            if size > 2 * 1024 * 1024:
                logger.debug("RAG: skipping large file %s (%d bytes)", path, size)
                return 0
        except OSError:
            return 0

        str_path = str(path)
        mtime = path.stat().st_mtime

        # Skip if already indexed and not modified.
        existing = self._indexed_files.get(str_path)
        if existing and existing.mtime >= mtime:
            return existing.chunk_count

        # Remove old chunks for this file.
        if existing:
            self._backend.remove(str_path)

        chunks = chunk_file(path)
        if not chunks:
            # File is now empty/unreadable — purge stale metadata.
            if str_path in self._indexed_files:
                del self._indexed_files[str_path]
                self._save_meta()
            return 0

        self._backend.add(chunks)

        self._indexed_files[str_path] = IndexedFile(
            path=str_path,
            mtime=mtime,
            chunk_count=len(chunks),
            indexed_at=time.time(),
        )
        self._save_meta()
        return len(chunks)

    async def index_directory(self, path: Path, glob: str = "**/*") -> int:
        """Index all text files in a directory. Returns total chunk count."""
        path = path.resolve()
        if not path.is_dir():
            return 0

        total_chunks = 0
        for file_path in sorted(path.glob(glob)):
            if not file_path.is_file():
                continue

            # Skip hidden and ignored directories.
            parts = file_path.relative_to(path).parts
            if any(part in _SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
                continue

            # Skip hidden files.
            if file_path.name.startswith(".") and file_path.suffix not in {".env"}:
                continue

            count = await self.index_file(file_path)
            total_chunks += count

        return total_chunks

    async def query(self, text: str, top_k: int = 5) -> list[Chunk]:
        """Query the knowledge base. Returns the top-k most relevant chunks."""
        return self._backend.query(text, top_k=top_k)

    async def remove(self, path: Path) -> int:
        """Remove a file or directory from the index. Returns chunks removed."""
        path = path.resolve()
        str_path = str(path)

        total_removed = 0

        if path.is_dir():
            # Remove all files under this directory.
            to_remove = [p for p in self._indexed_files if p.startswith(str_path + os.sep)]
            for p in to_remove:
                removed = self._backend.remove(p)
                total_removed += removed
                del self._indexed_files[p]
        else:
            removed = self._backend.remove(str_path)
            total_removed += removed
            self._indexed_files.pop(str_path, None)

        if total_removed:
            self._save_meta()
        return total_removed

    def list_indexed(self) -> list[IndexedFile]:
        """Return metadata for all indexed files."""
        return sorted(self._indexed_files.values(), key=lambda f: f.path)

    def stats(self) -> dict[str, Any]:
        """Return summary statistics about the knowledge base."""
        files = list(self._indexed_files.values())
        total_chunks = sum(f.chunk_count for f in files)

        # Estimate storage size.
        store_size = 0
        for child in self._store_dir.rglob("*"):
            if child.is_file():
                try:
                    store_size += child.stat().st_size
                except OSError:
                    pass

        return {
            "total_files": len(files),
            "total_chunks": total_chunks,
            "store_size_bytes": store_size,
            "store_dir": str(self._store_dir),
            "backend": "chromadb" if isinstance(self._backend, _ChromaBackend) else "json",
        }
