"""
Contextor subsystem for JARVIS.

Thin stdio adapter to the Rust contextor binary.  The binary handles
storage (SQLite), vector indexing, and cosine similarity search.
JARVIS computes embeddings via OllamaEmbeddings and passes pre-computed
vectors to the binary.
"""

from .adapter import ContextorAdapter
from .embeddings import OllamaEmbeddings

__all__ = ["ContextorAdapter", "OllamaEmbeddings"]
