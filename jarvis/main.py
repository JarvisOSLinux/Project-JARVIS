from .config import Config
from .core import ComponentFactory
from .core.logger import get_logger
from json import dumps
from typing import Dict, Any

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
        logger.info(f"JARVIS: Processing: '{prompt}'")
        response = self.llm.ask(prompt)
        
        iteration = 0
        MAX_ITERATIONS = 5  # Reduced from 10 - if LLM needs more than 5 tries, something is wrong
        last_command = None
        repeat_count = 0
        
        while response['user_request'] != "Conversation" and iteration < MAX_ITERATIONS:
            iteration += 1
            logger.debug(f"JARVIS: Iteration {iteration}, type: '{response['user_request']}'")
            
            if response['user_request'] == "SuperMCP":
                # Handle SuperMCP commands
                current_command = response['output']
                logger.info(f"JARVIS: [{iteration}] {current_command}")
                
                # Detect if LLM is repeating the same command
                if current_command == last_command:
                    repeat_count += 1
                    logger.warning(f"JARVIS: LLM repeated same command (count: {repeat_count})")
                    if repeat_count >= 2:
                        logger.error("JARVIS: LLM stuck repeating - breaking loop")
                        response = {
                            "user_request": "Conversation",
                            "output": "I apologize, but I'm having difficulty processing this request. The system kept trying the same approach. Please try rephrasing your request."
                        }
                        break
                else:
                    repeat_count = 0
                    last_command = current_command
                
                supermcp_output = self.command_parser.execute_command_sequence(response['output'])
                logger.debug(f"JARVIS: SuperMCP output:\n{dumps(supermcp_output, indent=2)}\n----------")
                
                # Convert to human-readable feedback for the LLM
                feedback_text = self._format_feedback_for_llm(supermcp_output)
                logger.info(f"JARVIS: Feedback ({len(feedback_text)} chars): {feedback_text[:150]}...")
                logger.debug(f"JARVIS: Full feedback:\n{feedback_text}\n---")
                response = self.llm.ask(feedback_text)
            else:
                logger.warning(f"JARVIS: Unknown user_request type: '{response['user_request']}'")
                break
        
        if iteration >= MAX_ITERATIONS:
            logger.warning(f"JARVIS: Hit maximum iterations ({MAX_ITERATIONS}), forcing conversation response")
            response = {
                "user_request": "Conversation",
                "output": "I've tried multiple approaches but encountered repeated issues. Could you please rephrase your request or try a simpler command?"
            }
        
        logger.info(f"JARVIS: Completed in {iteration} iteration(s)")

        # Reset history only if configured to do so
        if Config.RESET_HISTORY_AFTER_RESPONSE:
            logger.debug("JARVIS: Resetting LLM history")
            self.llm.reset_history()
        
        # Handle output using output manager
        logger.info(f"JARVIS: Final response: {response['output']}")
        self.output_manager.handle_response(response)
        
        return response
    
    def _format_feedback_for_llm(self, output: Dict[str, Any]) -> str:
        """Convert SuperMCP output to clear, readable text for the LLM"""
        if not isinstance(output, dict) or not output.get('success'):
            return f"Error: {output.get('error', 'Unknown error occurred')}"
        
        feedback_lines = []
        results = output.get('results', [])
        
        logger.debug(f"JARVIS: Formatting {len(results)} results for LLM")
        logger.debug(f"JARVIS: Results structure: {dumps(results, indent=2)[:500]}")
        
        for i, result in enumerate(results, 1):
            logger.debug(f"JARVIS: Processing result {i}: {list(result.keys()) if isinstance(result, dict) else type(result)}")
            
            if not isinstance(result, dict):
                feedback_lines.append(f"Result {i}: {result}")
                continue
            
            # Handle errors FIRST
            if 'error' in result:
                feedback_lines.append(f"Error in command {i}: {result['error']}")
                continue
            
            # Handle reload_servers success
            if result.get('ok') is True and 'count' in result:
                feedback_lines.append(f"[OK] Reloaded {result['count']} MCP servers")
                continue
            
            # Handle file listing results (check this BEFORE generic result handling)
            if 'result' in result:
                inner_result = result['result']
                if isinstance(inner_result, dict) and 'files' in inner_result and 'directories' in inner_result:
                    files = inner_result.get('files', [])
                    dirs = inner_result.get('directories', [])
                    path = inner_result.get('path', '.')
                    
                    feedback_lines.append(f"[SUCCESS] Listed directory '{path}':")
                    file_names = ', '.join([f['name'] for f in files[:10]])
                    feedback_lines.append(f"  Files ({len(files)}): {file_names}")
                    if len(files) > 10:
                        feedback_lines.append(f"    ... and {len(files) - 10} more")
                    dir_names = ', '.join([d['name'] for d in dirs[:10]]) if dirs else "none"
                    feedback_lines.append(f"  Directories ({len(dirs)}): {dir_names}")
                    if len(dirs) > 10:
                        feedback_lines.append(f"    ... and {len(dirs) - 10} more")
                    feedback_lines.append(f"\nTASK COMPLETE! Return a Conversation response summarizing these results to the user.")
                    continue
                    
                # Handle list_servers success (list of dicts with 'name')
                if isinstance(inner_result, list) and inner_result and 'name' in inner_result[0]:
                    server_names = [s['name'] for s in inner_result]
                    feedback_lines.append(f"[OK] Available servers: {', '.join(server_names)}")
                    continue
                
                # Generic successful result
                feedback_lines.append(f"[OK] Command executed successfully")
                feedback_lines.append(f"  Output: {str(inner_result)[:300]}")
                feedback_lines.append(f"TASK COMPLETE! Return a Conversation response with this output.")
                continue
            
            # Handle inspect_server success  
            if 'name' in result and 'tools' in result:
                server_name = result['name']
                tools = result.get('tools', [])
                feedback_lines.append(f"[OK] Inspected {server_name}:")
                feedback_lines.append(f"  Available tools: {', '.join(tools)}")
                continue
            
            # Default: show the result as-is (shouldn't reach here often)
            feedback_lines.append(f"Result {i}: {dumps(result)[:300]}")
        
        return "\n".join(feedback_lines)

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
