"""Embedding generation for the RAG knowledge base.

Supports two backends (tried in order):

1. **sentence-transformers** — high-quality local embeddings via
   ``sentence_transformers.SentenceTransformer`` when the package is
   installed (``pip install sentence-transformers``).
2. **TF-IDF / cosine similarity** — zero-dependency fallback using
   a simple term-frequency approach built on Python's stdlib.

The public API is the :func:`get_embedder` factory which returns the
best available backend.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import sys
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _silence_hf_output():
    """Silence HuggingFace's stdout/stderr during model loads.

    sentence-transformers + transformers emit a "LOAD REPORT" table
    plus UNEXPECTED-keys warnings on first use of MiniLM. Those writes
    hit stderr directly (bypassing Python logging) and corrupt the
    prompt_toolkit TUI output pane when they fire mid-session. This
    context redirects both streams to /dev/null for the duration of
    the load and also nudges the relevant loggers down to ERROR.
    """
    saved_levels = {}
    for name in ("transformers", "sentence_transformers", "tokenizers", "huggingface_hub"):
        lg = logging.getLogger(name)
        saved_levels[name] = lg.level
        lg.setLevel(logging.ERROR)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    devnull = open(os.devnull, "w")
    real_stdout, real_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = devnull
        sys.stderr = devnull
        yield
    finally:
        sys.stdout = real_stdout
        sys.stderr = real_stderr
        devnull.close()
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


class BaseEmbedder(ABC):
    """Abstract base for embedding backends."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a list of embedding vectors (one per input text)."""
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Return the embedding for a single query text."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


# ------------------------------------------------------------------ #
#  TF-IDF fallback (zero external deps)
# ------------------------------------------------------------------ #

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + extract alphanumeric tokens."""
    return _WORD_RE.findall(text.lower())


class TFIDFEmbedder(BaseEmbedder):
    """Simple TF-IDF bag-of-words embedder.

    This is the zero-dependency fallback.  It builds a vocabulary from
    the corpus and produces sparse-like fixed-dimension vectors via
    hashing into a fixed number of buckets.

    The quality is significantly lower than neural embeddings but it
    works out of the box without any pip installs.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim
        # IDF weights learned from the corpus (populated lazily).
        self._idf: dict[str, float] = {}
        self._corpus_size = 0

    @property
    def dimension(self) -> int:
        return self._dim

    def _hash_token(self, token: str) -> int:
        """Deterministic hash into a bucket index."""
        h = 0
        for ch in token:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return h % self._dim

    def _tf_vector(self, text: str) -> list[float]:
        """Build a TF-weighted vector for *text*."""
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self._dim

        vec = [0.0] * self._dim
        tf: dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1

        for tok, count in tf.items():
            bucket = self._hash_token(tok)
            weight = (1 + math.log(count)) * self._idf.get(tok, 1.0)
            vec[bucket] += weight

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def fit(self, texts: list[str]) -> None:
        """Learn IDF weights from the corpus."""
        self._corpus_size = len(texts)
        doc_freq: dict[str, int] = {}
        for text in texts:
            seen: set[str] = set()
            for tok in _tokenize(text):
                if tok not in seen:
                    doc_freq[tok] = doc_freq.get(tok, 0) + 1
                    seen.add(tok)

        for tok, df in doc_freq.items():
            self._idf[tok] = math.log((1 + self._corpus_size) / (1 + df)) + 1

    def embed(self, texts: list[str]) -> list[list[float]]:
        # If IDF is empty, fit on these texts first.
        if not self._idf:
            self.fit(texts)
        return [self._tf_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._tf_vector(text)


# ------------------------------------------------------------------ #
#  sentence-transformers backend
# ------------------------------------------------------------------ #


class SentenceTransformerEmbedder(BaseEmbedder):
    """Wrapper around ``sentence_transformers.SentenceTransformer``.

    Uses ``all-MiniLM-L6-v2`` by default — a 384-dim model that is
    small (~80 MB) and fast.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        with _silence_hf_output():
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            self._model = SentenceTransformer(model_name)
            self._dim = self._model.get_sentence_embedding_dimension()  # type: ignore[assignment]

    @property
    def dimension(self) -> int:
        return self._dim  # type: ignore[return-value]

    def embed(self, texts: list[str]) -> list[list[float]]:
        with _silence_hf_output():
            embeddings = self._model.encode(texts, show_progress_bar=False)
        return [e.tolist() for e in embeddings]

    def embed_query(self, text: str) -> list[float]:
        with _silence_hf_output():
            return self._model.encode(text, show_progress_bar=False).tolist()


# ------------------------------------------------------------------ #
#  Factory
# ------------------------------------------------------------------ #


def get_embedder() -> BaseEmbedder:
    """Return the best available embedder backend.

    Tries (in order):
    1. sentence-transformers
    2. TF-IDF fallback
    """
    try:
        embedder = SentenceTransformerEmbedder()
        logger.info("RAG: using sentence-transformers embedder (%d-dim)", embedder.dimension)
        return embedder
    except ImportError:
        logger.info("RAG: sentence-transformers not installed, using TF-IDF fallback")
    except Exception as exc:
        logger.warning("RAG: sentence-transformers failed (%s), using TF-IDF fallback", exc)

    return TFIDFEmbedder()
