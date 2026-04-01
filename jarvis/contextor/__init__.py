"""
Contextor subsystem for JARVIS.

Manages long-term memory with three layers:
1. JSONL file backend (source of truth, works immediately)
2. ChromaDB vector store (semantic search index, optional)
3. Ollama embeddings (generates vectors for semantic search)

The adapter supports two retrieval modes:
- Keyword search (always available — substring matching)
- Semantic search (when RAG_ENABLED=true — embedding similarity via ChromaDB)

RAG entry point: ``ContextorAdapter.retrieve_context(query)`` — call this
before each LLM invocation to inject relevant memories into the prompt.
"""

from .adapter import ContextorAdapter
from .embeddings import OllamaEmbeddings
from .vector_store import VectorStore

__all__ = ["ContextorAdapter", "OllamaEmbeddings", "VectorStore"]
