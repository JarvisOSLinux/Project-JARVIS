from .config import Config
from .core.logger import get_logger
from .llm_providers import LLMProviderFactory
import json

logger = get_logger(__name__)

class LLM:
    """Main LLM interface that works with any configured provider."""
    
    def __init__(self, system, release, version, machine, shell):
        """
        Initialize LLM with configured provider.
        
        Args:
            system: Operating system name
            release: OS release version
            version: OS version
            machine: Machine architecture
            shell: Shell name
        """
        # Create provider based on configuration
        self.provider = LLMProviderFactory.create_provider()
        logger.info(f"Using LLM provider: {Config.LLM_PROVIDER} with model: {self.provider.model}")
        
        # Initialize chat history with system prompt
        self.default_chat = [
            {
                'role': 'system',
                'content': Config.LLM_RULE.format(
                    system=system,
                    release=release,
                    version=version,
                    machine=machine,
                    shell=shell
                ),
            }
        ]
        self.chat_history = list.copy(self.default_chat)

        logger.info("LLM: Initiating Preload...")
        # Start preload to warm up the provider
        try:
            self.provider.chat(self.chat_history)
            logger.info("LLM: Initiation Complete!")
        except Exception as e:
            logger.warning(f"LLM preload failed (this may be expected): {e}")
            logger.info("LLM: Continuing despite preload failure...")
    
    def ask(self, prompt):
        """
        Ask the LLM a question and get JSON response.
        
        Args:
            prompt: User's question/prompt
            
        Returns:
            Parsed JSON response as dictionary
        """
        # Add user message to history
        self.chat_history.append({
            'role': 'user',
            'content': prompt
        })

        # Get response from provider
        response_text = self.provider.chat(self.chat_history)

        logger.debug(f"LLM Responded:'\n{response_text}\n----------")

        # Try to parse as JSON
        try:
            return json.loads(response_text)
        
        except json.decoder.JSONDecodeError:
            logger.warning("LLM response was not valid JSON, retrying with error message...")
            return self.ask(Config.LLM_WRONG_JSON_FORMAT_MESSAGE)
        
    def reset_history(self):
        """Reset chat history to default system prompt."""
        self.chat_history = list.copy(self.default_chat)
