"""
LLM Provider implementations for JARVIS.

Supports multiple LLM backends:
- Ollama (local)
- OpenAI-compatible APIs (OpenAI, Claude, OpenRouter, custom servers)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import json
import httpx
import ollama
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    def __init__(self, model: str):
        """
        Initialize LLM provider.
        
        Args:
            model: Model name/identifier
        """
        self.model = model
    
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        Send chat messages and get response.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            
        Returns:
            Response text from the LLM
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the provider is available and accessible.
        
        Returns:
            True if provider is available, False otherwise
        """
        pass


class OllamaProvider(BaseLLMProvider):
    """Provider for local Ollama instances."""
    
    def __init__(self, model: str, base_url: Optional[str] = None, auto_pull: bool = False):
        """
        Initialize Ollama provider.
        
        Args:
            model: Ollama model name
            base_url: Optional Ollama base URL (default: http://localhost:11434)
            auto_pull: If True, automatically pull missing models without prompting
        """
        super().__init__(model)
        self.base_url = base_url or "http://localhost:11434"
        self.auto_pull = auto_pull
        
        # Configure Ollama client if custom URL is provided
        if base_url and base_url != "http://localhost:11434":
            import os
            os.environ["OLLAMA_HOST"] = base_url
    
    def _model_exists(self) -> bool:
        """
        Check if the specified model exists locally.
        
        Returns:
            True if model exists, False otherwise
        """
        try:
            models_response = ollama.list()
            models = models_response.get("models", [])
            model_names = [m.get("name", "") for m in models if m.get("name")]
            
            logger.debug(f"Looking for model '{self.model}' in available models: {model_names}")
            
            # Check for exact match first
            if self.model in model_names:
                logger.debug(f"Found exact match for model '{self.model}'")
                return True
            
            # Check if model name matches as prefix (e.g., "llama3" matches "llama3:8b")
            # or if we have a tag, check if base name matches
            model_base = self.model.split(":")[0]
            for name in model_names:
                if not name:
                    continue
                    
                # Handle both "model" and "model:tag" formats
                name_base = name.split(":")[0]
                
                # Exact base match
                if name_base == model_base:
                    logger.debug(f"Found base match: '{name}' matches '{self.model}'")
                    return True
                
                # Check if name starts with our model (handles cases like "qwen3-embedding:4b" matching "qwen3-embedding")
                if name.startswith(self.model):
                    logger.debug(f"Found prefix match: '{name}' starts with '{self.model}'")
                    return True
                
                # Also check reverse: if our model starts with the name (handles "qwen3-embedding:4b" matching "qwen3-embedding")
                if self.model.startswith(name_base):
                    logger.debug(f"Found reverse prefix match: '{self.model}' starts with '{name_base}'")
                    return True
            
            logger.debug(f"Model '{self.model}' not found in available models")
            return False
        except Exception as e:
            logger.debug(f"Error checking for model: {e}", exc_info=True)
            return False
    
    def _pull_model(self) -> bool:
        """
        Pull the model from Ollama.
        
        Returns:
            True if pull succeeded, False otherwise
        """
        try:
            logger.info(f"Pulling model '{self.model}' from Ollama...")
            logger.info("This may take a while depending on model size...")
            
            # ollama.pull() returns a generator that streams progress
            for chunk in ollama.pull(self.model, stream=True):
                if "status" in chunk:
                    # Show progress without spamming logs
                    if "downloading" in chunk["status"].lower() or "pulling" in chunk["status"].lower():
                        completed = chunk.get("completed") or 0
                        total = chunk.get("total") or 0
                        progress = (completed / total * 100) if total and total > 0 else 0
                        print(f"\rPulling {self.model}: {chunk['status']} ({progress:.1f}%)", end="", flush=True)
            
            print()  # New line after progress
            logger.info(f"✓ Successfully pulled model '{self.model}'")
            return True
        except Exception as e:
            logger.error(f"Failed to pull model '{self.model}': {e}")
            return False
    
    def ensure_model(self) -> bool:
        """
        Ensure the model exists, pulling it if necessary.
        
        This will:
        - Check if model exists
        - If not, prompt user (unless auto_pull is True) or pull automatically
        - Return True if model is available, False otherwise
        
        Returns:
            True if model is available, False otherwise
        """
        if self._model_exists():
            logger.debug(f"Model '{self.model}' is already available")
            return True
        
        logger.warning(f"Model '{self.model}' is not installed locally")
        
        if self.auto_pull:
            logger.info("Auto-pull enabled, pulling model automatically...")
            return self._pull_model()
        
        # Prompt user if in interactive mode
        import sys
        if sys.stdin.isatty():  # Check if running in interactive terminal
            response = input(f"\nModel '{self.model}' is not installed. Would you like to pull it now? (y/n): ").strip().lower()
            if response in ['y', 'yes']:
                return self._pull_model()
            else:
                logger.error("Model not available and user declined to pull it")
                return False
        else:
            # Non-interactive mode (e.g., scripts, CI/CD)
            logger.error(f"Model '{self.model}' is not installed and auto-pull is disabled.")
            logger.error("To enable auto-pull, set LLM_AUTO_PULL=true in your .env file")
            return False
    
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages to Ollama."""
        try:
            response = ollama.chat(
                model=self.model,
                messages=messages
            )
            return response["message"]["content"]
        except Exception as e:
            # If model not found error, try to pull it
            if "model" in str(e).lower() and ("not found" in str(e).lower() or "does not exist" in str(e).lower()):
                logger.warning("Model not found during chat, attempting to ensure model is available...")
                if self.ensure_model():
                    # Retry chat after pulling
                    try:
                        response = ollama.chat(
                            model=self.model,
                            messages=messages
                        )
                        return response["message"]["content"]
                    except Exception as retry_error:
                        logger.error(f"Ollama chat error after model pull: {retry_error}")
                        raise
                else:
                    raise RuntimeError(f"Model '{self.model}' is not available and could not be pulled")
            
            logger.error(f"Ollama chat error: {e}")
            raise
    
    def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            # Try to list models to verify Ollama is accessible
            ollama.list()
            return True
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False


class APILLMProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible API endpoints."""
    
    def __init__(
        self,
        model: str,
        api_url: str,
        api_key: str,
        headers: Optional[Dict[str, str]] = None
    ):
        """
        Initialize API provider.
        
        Args:
            model: Model identifier for the API
            api_url: Base URL for the API endpoint
            api_key: API key for authentication
            headers: Optional additional headers (default: Authorization header)
        """
        super().__init__(model)
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        
        # Default headers
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        # Add custom headers if provided
        if headers:
            self.headers.update(headers)
    
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages to API endpoint."""
        # Convert messages format if needed (most APIs use the same format)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False  # Non-streaming for now
        }
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.api_url}/v1/chat/completions",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                
                result = response.json()
                
                # Extract content from response
                # OpenAI format: choices[0].message.content
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"]
                else:
                    raise ValueError(f"Unexpected API response format: {result}")
                    
        except httpx.HTTPError as e:
            logger.error(f"API HTTP error: {e}")
            raise
        except Exception as e:
            logger.error(f"API chat error: {e}")
            raise
    
    def is_available(self) -> bool:
        """Check if API endpoint is available."""
        try:
            # Try to list models or make a simple request
            # Some APIs might not have /v1/models, so we'll catch and try the endpoint directly
            with httpx.Client(timeout=5.0) as client:
                # Try /v1/models first (OpenAI format)
                try:
                    response = client.get(
                        f"{self.api_url}/v1/models",
                        headers=self.headers
                    )
                    if response.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                
                # If that fails, the endpoint might still work for chat
                # We'll return True optimistically - actual availability will be checked during chat
                # This prevents false negatives for APIs with different endpoint structures
                return True
        except Exception as e:
            logger.debug(f"API availability check failed: {e}")
            # Return True anyway - the actual request will fail if truly unavailable
            # This prevents blocking users from using custom endpoints
            return True


class LLMProviderFactory:
    """Factory for creating LLM providers based on configuration."""
    
    @staticmethod
    def create_provider() -> BaseLLMProvider:
        """
        Create LLM provider based on configuration.
        
        Returns:
            Appropriate LLM provider instance
            
        Raises:
            ValueError: If provider type is invalid or required config is missing
        """
        provider_type = Config.LLM_PROVIDER.lower()
        model = Config.LLM_MODEL
        
        if not model:
            raise ValueError("LLM_MODEL must be set in configuration")
        
        if provider_type == "ollama":
            base_url = Config.LLM_OLLAMA_URL
            auto_pull = getattr(Config, 'LLM_AUTO_PULL', False)
            provider = OllamaProvider(model=model, base_url=base_url, auto_pull=auto_pull)
            
            if not provider.is_available():
                logger.warning("Ollama provider configured but not available. "
                             "Make sure Ollama is running.")
                return provider  # Return anyway, error will be caught when trying to use it
            
            # Ensure model is available (will prompt user or auto-pull if configured)
            logger.info(f"Checking if model '{model}' is available...")
            if not provider.ensure_model():
                logger.error(f"Model '{model}' is not available and could not be pulled.")
                raise RuntimeError(f"Model '{model}' is not available. Please install it manually or enable auto-pull.")
            
            return provider
        
        elif provider_type == "api":
            api_url = Config.LLM_API_URL
            api_key = Config.LLM_API_KEY
            
            if not api_url:
                raise ValueError("LLM_API_URL must be set when using API provider")
            if not api_key:
                raise ValueError("LLM_API_KEY must be set when using API provider")
            
            # Parse custom headers if provided
            headers = None
            if Config.LLM_API_HEADERS:
                try:
                    headers = json.loads(Config.LLM_API_HEADERS)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid LLM_API_HEADERS JSON, ignoring: {Config.LLM_API_HEADERS}")
            
            provider = APILLMProvider(
                model=model,
                api_url=api_url,
                api_key=api_key,
                headers=headers
            )
            
            if not provider.is_available():
                logger.warning("API provider configured but endpoint not available. "
                             "Check LLM_API_URL and LLM_API_KEY.")
            
            return provider
        
        else:
            raise ValueError(
                f"Invalid LLM_PROVIDER: {provider_type}. "
                f"Must be 'ollama' or 'api'"
            )
