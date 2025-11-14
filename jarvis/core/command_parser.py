from typing import Dict, Any, List
import json
import re
from ..supermcp_client import SuperMCPWrapper
from .logger import get_logger

logger = get_logger(__name__)


class SuperMCPCommandParser:
    def __init__(self, supermcp_client: SuperMCPWrapper):
        self.supermcp = supermcp_client
    
    def execute_approved_command(self, server: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a command that has been approved by the user"""
        logger.info(f"CommandParser: Executing approved command {server}.{tool} with args: {arguments}")
        return self.supermcp.call_server_tool(server, tool, arguments)
    
    def execute_command_sequence(self, command_sequence: str) -> Dict[str, Any]:
        try:
            logger.info(f"CommandParser: Executing {command_sequence.count(';') + 1} command(s)")
            commands = [cmd.strip() for cmd in command_sequence.split(';') if cmd.strip()]
            results = []
            
            for i, command in enumerate(commands, 1):
                logger.debug(f"CommandParser: Command {i}/{len(commands)}: {command}")
                result = self._parse_and_execute_command(command)
                logger.debug(f"CommandParser: Result: {result}")
                results.append(result)
            
            logger.info(f"CommandParser: Completed {len(commands)} command(s)")
            return {"success": True, "results": results}
        except Exception as e:
            logger.error(f"CommandParser: Command sequence failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _parse_and_execute_command(self, command: str) -> Dict[str, Any]:
        try:
            logger.debug(f"CommandParser: Parsing command: {command}")
            if command == "reload_servers()":
                logger.debug("CommandParser: Matched reload_servers()")
                result = self.supermcp.reload_servers()
                logger.debug(f"CommandParser: reload_servers() returned: {result}")
                return result
            elif command == "list_servers()":
                logger.debug("CommandParser: Matched list_servers()")
                result = self.supermcp.list_servers()
                logger.debug(f"CommandParser: list_servers() returned: {result}")
                return result
            elif command.startswith("inspect_server("):
                logger.debug("CommandParser: Matched inspect_server(...)")
                return self._handle_inspect_server(command)
            elif command.startswith("call_server_tool("):
                logger.debug("CommandParser: Matched call_server_tool(...)")
                return self._handle_call_server_tool(command)
            else:
                error_msg = f"Unknown command: {command}"
                logger.warning(f"CommandParser: {error_msg}")
                return {"error": error_msg}
        except Exception as e:
            error_msg = f"Command execution failed: {e}"
            logger.error(f"CommandParser: {error_msg}", exc_info=True)
            return {"error": error_msg}
    
    def _handle_inspect_server(self, command: str) -> Dict[str, Any]:
        # Extract server name from inspect_server(server_name)
        server_name = command[15:-1]  # Remove "inspect_server(" and ")"
        logger.debug(f"CommandParser: Extracted server_name: '{server_name}'")
        result = self.supermcp.inspect_server(server_name)
        logger.debug(f"CommandParser: inspect_server returned: {result}")
        return result
    
    def _handle_call_server_tool(self, command: str) -> Dict[str, Any]:
        try:
            # Extract the content between parentheses
            content = command[17:-1]  # Remove "call_server_tool(" and ")"
            logger.debug(f"CommandParser: Extracted content: '{content}'")
            
            # Parse arguments using simple state machine
            parts = self._parse_command_arguments(content)
            logger.debug(f"CommandParser: Parsed parts: {parts}")
            
            if len(parts) >= 2:
                server_name = parts[0]
                tool_name = parts[1]
                
                # Parse arguments if provided
                arguments = {}
                if len(parts) >= 3:
                    args_str = parts[2]
                    logger.debug(f"CommandParser: Parsing arguments from: '{args_str}'")
                    arguments = self._parse_json_like_args(args_str)
                    logger.debug(f"CommandParser: Parsed arguments: {arguments}")
                
                logger.info(f"CommandParser: Calling {server_name}.{tool_name} with args: {arguments}")
                result = self.supermcp.call_server_tool(server_name, tool_name, arguments)
                logger.debug(f"CommandParser: call_server_tool returned: {result}")
                
                # Check if ShellMCP returned a "requires approval" response
                # ShellMCP returns a message like "This command requires approval. It has been queued with ID: ..."
                if server_name == "ShellMCP" and tool_name == "execute_command":
                    # Handle both dict and string results
                    if isinstance(result, dict):
                        result_str = str(result.get('result', ''))
                    else:
                        # Result is already a string (common for simple command outputs)
                        result_str = str(result)
                    
                    if 'requires approval' in result_str.lower() or 'queued with id' in result_str.lower():
                        command_to_execute = arguments.get("command", "")
                        logger.info(f"CommandParser: ShellMCP command requires approval: {command_to_execute}")
                        return {
                            "approval_required": True,
                            "server": server_name,
                            "tool": tool_name,
                            "arguments": arguments,
                            "command": command_to_execute,
                            "shellmcp_response": result_str
                        }
                
                return result
            else:
                error_msg = f"Invalid call_server_tool format: {command}"
                logger.warning(f"CommandParser: {error_msg}")
                return {"error": error_msg}
        except Exception as e:
            error_msg = f"Failed to parse call_server_tool: {e}"
            logger.error(f"CommandParser: {error_msg}", exc_info=True)
            return {"error": error_msg}
    
    def _parse_json_like_args(self, args_str: str) -> Dict[str, Any]:
        """Parse JSON-like argument string into a Python dict"""
        try:
            # Remove outer braces and whitespace
            args_str = args_str.strip()
            if args_str.startswith('{') and args_str.endswith('}'):
                args_str = args_str[1:-1]
            
            # Try to parse as a simple key-value format
            # Format: key1: value1, key2: value2
            result = {}
            current_key = ""
            current_value = ""
            in_value = False
            quote_char = None
            depth = 0
            
            i = 0
            while i < len(args_str):
                char = args_str[i]
                
                # Handle quotes
                if char in ('"', "'") and (i == 0 or args_str[i-1] != '\\'):
                    if quote_char is None:
                        quote_char = char
                        in_value = True
                    elif quote_char == char:
                        quote_char = None
                    else:
                        current_value += char
                    i += 1
                    continue
                
                # If we're in quotes, add everything to value
                if quote_char:
                    current_value += char
                    i += 1
                    continue
                
                # Handle colon (key-value separator)
                if char == ':' and not in_value and depth == 0:
                    current_key = current_key.strip()
                    in_value = True
                    i += 1
                    continue
                
                # Handle comma (argument separator)
                if char == ',' and depth == 0 and in_value:
                    current_value = current_value.strip()
                    if current_value:
                        result[current_key] = current_value
                    current_key = ""
                    current_value = ""
                    in_value = False
                    i += 1
                    continue
                
                # Handle nested braces
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                
                # Add to current key or value
                if in_value:
                    current_value += char
                else:
                    current_key += char
                
                i += 1
            
            # Don't forget the last key-value pair
            if current_key and in_value:
                current_value = current_value.strip()
                if current_value:
                    result[current_key] = current_value
            
            logger.debug(f"CommandParser: Parsed arguments: {result}")
            return result
            
        except Exception as e:
            logger.warning(f"CommandParser: Failed to parse arguments: {e}")
            logger.debug(f"CommandParser: Problematic args_str: '{args_str[:200]}'")
            return {}
    
    def _parse_command_arguments(self, content: str) -> List[str]:
        parts = []
        current_part = ""
        brace_count = 0
        
        for char in content:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
            elif char == ',' and brace_count == 0:
                parts.append(current_part.strip())
                current_part = ""
                continue
            current_part += char
        
        if current_part:
            parts.append(current_part.strip())
        
        return parts
