"""RAG context integration — inject retrieved chunks into the system prompt.

Before each agent loop call, the user's message is used to query the
knowledge base.  The top-k relevant chunks are formatted as a context
section and injected into the system prompt with source attribution.

This module provides two public helpers:

- :func:`build_rag_context` — query the store and format results as a
  prompt-ready string.
- :func:`inject_rag_context` — convenience wrapper that appends the RAG
  section to the system prompt's context sections list.
"""

from __future__ import annotations

import logging

from karna.rag.chunker import Chunk
from karna.rag.store import KnowledgeStore

logger = logging.getLogger(__name__)

# Singleton store instance — lazily initialised on first use.
_store: KnowledgeStore | None = None


def _get_store() -> KnowledgeStore:
    """Return (or create) the global KnowledgeStore singleton."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = KnowledgeStore()
    return _store


def reset_store(store: KnowledgeStore | None = None) -> None:
    """Replace the global store (used in tests)."""
    global _store  # noqa: PLW0603
    _store = store


def _format_chunk(chunk: Chunk, index: int) -> str:
    """Format a single chunk for prompt injection."""
    source = chunk.source_label
    return f"[{index + 1}] Source: {source}\n{chunk.text}"


async def build_rag_context(
    query: str,
    top_k: int = 5,
    store: KnowledgeStore | None = None,
) -> str | None:
    """Query the knowledge base and return formatted context.

    Returns ``None`` if the store is empty or no relevant chunks are found.

    Parameters
    ----------
    query : str
        The user's message to search for.
    top_k : int
        Maximum number of chunks to retrieve.
    store : KnowledgeStore, optional
        Store instance to use.  Falls back to the global singleton.

    Returns
    -------
    str or None
        Formatted context string with source attribution, or None.
    """
    ks = store or _get_store()

    try:
        chunks = await ks.query(query, top_k=top_k)
    except Exception as exc:
        logger.warning("RAG query failed: %s", exc)
        return None

    if not chunks:
        return None

    parts = [_format_chunk(c, i) for i, c in enumerate(chunks)]
    header = "The following context was retrieved from your local knowledge base:\n"
    return header + "\n\n".join(parts)


async def inject_rag_context(
    query: str,
    context_sections: list[tuple[str, str, int]],
    *,
    top_k: int = 5,
    priority: int = 4,
    store: KnowledgeStore | None = None,
) -> bool:
    """Query the knowledge base and append results to *context_sections*.

    Modifies *context_sections* in place.  Returns ``True`` if context
    was injected, ``False`` otherwise.

    Parameters
    ----------
    query : str
        The user's message to search for.
    context_sections : list
        The mutable list of ``(label, content, priority)`` tuples used
        by the system prompt builder.
    top_k : int
        Maximum number of chunks to retrieve.
    priority : int
        Priority for the RAG section (lower = kept first during trimming).
    store : KnowledgeStore, optional
        Store instance to use.  Falls back to the global singleton.
    """
    context = await build_rag_context(query, top_k=top_k, store=store)
    if context is None:
        return False

    context_sections.append(("Knowledge Base", context, priority))
    logger.info("RAG: injected %d-char context section at priority %d", len(context), priority)
    return True
