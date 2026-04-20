"""RAG (Retrieval Augmented Generation) — local knowledge base for Nellie.

Indexes documents into a local vector store and retrieves relevant
context before each LLM call.  Supports two backends:

- **ChromaDB** — full-featured local vector database (requires ``chromadb``).
- **Fallback** — zero-dependency JSON + cosine similarity approach.

Install the optional dependency with ``pip install karna[rag]``.
"""

from karna.rag.store import KnowledgeStore

__all__ = ["KnowledgeStore"]
