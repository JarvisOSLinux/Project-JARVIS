"""
Abstract base class for LLM providers.

Any new LLM backend must implement :class:`BaseLLMProvider`.
The rest of JARVIS only depends on this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, List


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages and return the response text."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider backend is reachable."""
