from .config import Config
from .core import ComponentFactory
from .core.logger import get_logger
from json import dumps

logger = get_logger(__name__)

class Jarvis:
    def __init__(self, text_mode=False):
        """
        Initialize JARVIS AI Assistant
        
        Args:
            text_mode: If True, skip voice input components (STT, Voice Activation)
                      for CLI text-only mode
        """
        self.text_mode = text_mode
        
        # Create all components using factory
        self.components = ComponentFactory.create_all_components(
            text_mode=text_mode,
            on_voice_command=self._handle_voice_command
        )
        
        # Extract components for easy access
        self.llm = self.components['llm']
        self.command_parser = self.components['command_parser']
        self.output_manager = self.components['output_manager']
        
        # Voice manager only exists in voice mode
        self.voice_manager = self.components.get('voice_manager')

    def _handle_voice_command(self, text: str) -> None:
        """
        Handle voice command from voice manager
        
        Args:
            text: Transcribed voice command
        """
        response = self.ask(prompt=text)
        logger.info(f"Response: {response['output']}")

    def ask(self, prompt):
        """
        Process a user prompt and return response
        
        Args:
            prompt: User input text
            
        Returns:
            LLM response dictionary
        """
        response = self.llm.ask(prompt)
        while response['user_request'] != "Conversation":
            if response['user_request'] == "SuperMCP":
                # Handle SuperMCP commands
                supermcp_output = self.command_parser.execute_command_sequence(response['output'])
                logger.debug(f"Output from SuperMCP:\n{supermcp_output}\n----------")
                response = self.llm.ask(dumps(supermcp_output))

        # Reset history only if configured to do so
        if Config.RESET_HISTORY_AFTER_RESPONSE:
            self.llm.reset_history()
        
        # Handle output using output manager
        self.output_manager.handle_response(response)
        
        return response

    def listen_with_activation(self):
        """Listen with voice activation (wake word detection)."""
        if not self.voice_manager:
            logger.error("Voice manager not available in text mode")
            return
        
        self.voice_manager.start_voice_activation_mode()

    def listen(self):
        """Legacy continuous listening mode (without wake word detection)."""
        if not self.voice_manager:
            logger.error("Voice manager not available in text mode")
            return
            
        self.voice_manager.start_continuous_listening_mode()

def main():
    """Main entry point for JARVIS - delegates to CLI handler"""
    from .cli import main as cli_main
    cli_main()

if __name__ == "__main__":
    main()
