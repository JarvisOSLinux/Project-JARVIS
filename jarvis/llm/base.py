"""
Abstract base class for LLM providers.

Any new LLM backend must implement :class:`BaseLLMProvider`.
The rest of JARVIS only depends on this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Iterator, List


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model: str):
        self.model = model
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages and return the response text."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider backend is reachable."""

    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Yield the response incrementally, as it is generated.

        Purely additive: providers that don't override this fall back to
        yielding the full ``chat()`` result as a single chunk, so existing
        behavior for any caller that doesn't opt into streaming is
        unchanged.

        Note: nothing in the current ROOT/DISPATCH action-parsing path
        consumes this yet — that path expects a complete, parseable JSON
        object from the LLM, which requires incremental JSON parsing to
        stream safely. Wiring this into `jarvis ask`/the TUI is tracked as
        separate follow-up work, not implied by this method existing.
        """
        yield self.chat(messages)

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embedding vectors for a batch of texts.

        Not every provider/model supports embeddings — the default
        raises so callers get a clear, immediate error rather than a
        confusing failure further down the stack.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support embeddings")
