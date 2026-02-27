"""
LLM package for JARVIS.

Provides the abstract provider interface, concrete provider
implementations (Ollama, OpenAI-compatible API), a provider
factory, and the LLM chat class.
"""

from .base import BaseLLMProvider
from .chat import LLM
from .providers import create_provider

__all__ = [
    "BaseLLMProvider",
    "LLM",
    "create_provider",
]
