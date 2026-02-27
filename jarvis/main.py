from .config import Config
from .core import ComponentFactory
from .core.logger import get_logger
from json import dumps
from typing import Dict, Any
import re

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

    def _handle_voice_command(self, text: str) -> dict:
        """
        Handle voice command from voice manager

        Args:
            text: Transcribed voice command

        Returns:
            LLM response dictionary
        """
        response = self.ask(prompt=text)
        logger.info(f"Response: {response['output']}")
        return response

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
                
                # Check if any command requires approval
                results = supermcp_output.get('results', [])
                approval_required = False
                approval_info = None
                
                for result in results:
                    if isinstance(result, dict) and result.get('approval_required'):
                        approval_required = True
                        approval_info = result
                        break
                
                if approval_required:
                    # Request user approval
                    command = approval_info.get('command', '')
                    shellmcp_response = approval_info.get('shellmcp_response', '')
                    
                    # Extract command ID from ShellMCP response if present
                    # Format: "It has been queued with ID: <uuid>"
                    command_id_match = re.search(r'ID:\s*([a-f0-9-]+)', shellmcp_response, re.IGNORECASE)
                    command_id = command_id_match.group(1) if command_id_match else None
                    
                    # Display security level from ShellMCP's whitelist check
                    security_level = "requires_approval"  # ShellMCP determined this
                    
                    approved = self.request_approval(command, security_level)
                    
                    if approved:
                        # Use ShellMCP's approve_command tool if we have a command ID
                        if command_id:
                            logger.info(f"JARVIS: Command approved, using ShellMCP approve_command with ID: {command_id}")
                            execution_result = self.command_parser.supermcp.call_server_tool(
                                "ShellMCP",
                                "approve_command",
                                {"commandId": command_id}
                            )
                        else:
                            # Fallback: execute directly (shouldn't happen if ShellMCP is working correctly)
                            logger.warning(f"JARVIS: No command ID found, executing command directly")
                            execution_result = self.command_parser.execute_approved_command(
                                approval_info['server'],
                                approval_info['tool'],
                                approval_info['arguments']
                            )
                        
                        # Create feedback for the LLM
                        if execution_result.get('error'):
                            feedback_text = f"Error executing command: {execution_result['error']}"
                        else:
                            result_str = str(execution_result.get('result', execution_result))
                            feedback_text = f"[SUCCESS] Command executed: {command}\nOutput: {result_str[:500]}"
                            feedback_text += "\n\nTASK COMPLETE! Return a Conversation response summarizing the results."
                    else:
                        # Use ShellMCP's deny_command tool if we have a command ID
                        if command_id:
                            logger.info(f"JARVIS: Command denied, using ShellMCP deny_command with ID: {command_id}")
                            self.command_parser.supermcp.call_server_tool(
                                "ShellMCP",
                                "deny_command",
                                {"commandId": command_id, "reason": "User denied the command"}
                            )
                        
                        # Command denied
                        logger.info(f"JARVIS: Command denied by user: {command}")
                        feedback_text = f"[DENIED] Command was not approved: {command}\n\nTASK COMPLETE! Return a Conversation response informing the user that the command was denied."
                    
                    response = self.llm.ask(feedback_text)
                else:
                    # Normal flow: convert to human-readable feedback for the LLM
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
    
    def request_approval(self, command: str, security_level: str = "read_only") -> bool:
        """
        Request user approval for a shell command
        
        Args:
            command: The command to execute
            security_level: Security level of the command
            
        Returns:
            True if approved, False if denied
        """
        # Display the command and ask for approval
        approval_prompt = f"⚠️  Command approval required:\nCommand: {command}\nSecurity Level: {security_level}\n\nWould you like to allow this command? (Yes/No): "
        
        logger.info(f"JARVIS: Requesting approval for command: {command}")
        self.output_manager._output_text(approval_prompt)
        
        # Get user input based on mode
        if self.text_mode:
            # Text mode: use input()
            user_response = input().strip()
        else:
            # Voice mode: use voice input
            if not self.voice_manager:
                logger.warning("Voice manager not available for approval request, falling back to text input")
                user_response = input().strip()
            else:
                # Use voice manager's listen_once for a single utterance
                logger.info("Listening for your approval response...")
                user_response = self.voice_manager.listen_once(timeout=15.0) or ""
                if user_response:
                    logger.info(f"Approval response received: {user_response}")
        
        if not user_response:
            logger.warning("JARVIS: No response received, denying command")
            return False
        
        # Parse yes/no response - use simple keyword matching first (fast and reliable)
        # Then optionally use LLM for ambiguous cases
        user_lower = user_response.lower().strip()
        
        # Clear approval keywords
        approval_keywords = ['yes', 'y', 'allow', 'approve', 'sure', 'ok', 'okay', 'go', 'proceed', 'execute', 'run']
        denial_keywords = ['no', 'n', 'deny', 'reject', 'cancel', 'stop', 'abort', 'don\'t', 'dont']
        
        # Check for clear approval
        if any(word in user_lower for word in approval_keywords):
            # Make sure it's not a denial (e.g., "no, don't approve")
            if not any(word in user_lower for word in denial_keywords):
                logger.info(f"JARVIS: Approval parsed as: True (keyword match)")
                return True
        
        # Check for clear denial
        if any(word in user_lower for word in denial_keywords):
            logger.info(f"JARVIS: Approval parsed as: False (keyword match)")
            return False
        
        # Ambiguous response - use LLM for parsing
        logger.debug(f"JARVIS: Ambiguous response, using LLM to parse: '{user_response}'")
        parse_prompt = f"User was asked: 'Would you like to allow this command? (Yes/No)'\nUser responded: '{user_response}'\n\nParse this as a yes/no answer. Respond with ONLY a JSON object in this exact format: {{\"user_request\": \"Conversation\", \"output\": \"approved\"}} for yes, or {{\"user_request\": \"Conversation\", \"output\": \"denied\"}} for no."
        
        try:
            # Create a temporary LLM instance for parsing (don't pollute main history)
            from .llm import LLM
            from .core.system_info import SystemInfo
            
            system_info = SystemInfo.get_system_info()
            temp_llm = LLM(
                system=system_info['system'],
                release=system_info['release'],
                version=system_info['version'],
                machine=system_info['machine'],
                shell=system_info['shell']
            )
            
            # Reset history to avoid context pollution
            temp_llm.reset_history()
            
            response = temp_llm.ask(parse_prompt)
            output = response.get('output', '').lower()
            
            # Parse LLM response
            approved = 'approved' in output or 'yes' in output or 'allow' in output
            denied = 'denied' in output or 'no' in output or 'reject' in output
            
            if approved and not denied:
                logger.info(f"JARVIS: Approval parsed as: True (LLM)")
                return True
            elif denied:
                logger.info(f"JARVIS: Approval parsed as: False (LLM)")
                return False
            else:
                # Still ambiguous, default to denial for safety
                logger.warning(f"JARVIS: Could not parse approval response, defaulting to denial")
                return False
            
        except Exception as e:
            logger.error(f"JARVIS: Failed to parse approval response with LLM: {e}")
            # Default to denial for safety
            logger.warning(f"JARVIS: Defaulting to denial due to parsing error")
            return False
    
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
                # Handle string results (common for simple command outputs)
                feedback_lines.append(f"[SUCCESS] Command executed successfully")
                feedback_lines.append(f"  Output: {str(result)}")
                feedback_lines.append(f"TASK COMPLETE! Return a Conversation response with this output.")
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
