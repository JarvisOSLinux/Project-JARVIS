from typing import Dict, Any, List
from ..supermcp_client import SuperMCPWrapper
from .logger import get_logger

logger = get_logger(__name__)


class SuperMCPCommandParser:
    def __init__(self, supermcp_client: SuperMCPWrapper):
        self.supermcp = supermcp_client
    
    def execute_command_sequence(self, command_sequence: str) -> Dict[str, Any]:
        try:
            commands = [cmd.strip() for cmd in command_sequence.split(';') if cmd.strip()]
            results = []
            
            for command in commands:
                logger.info(f"Executing SuperMCP command: {command}")
                result = self._parse_and_execute_command(command)
                results.append(result)
            
            return {"success": True, "results": results}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _parse_and_execute_command(self, command: str) -> Dict[str, Any]:
        try:
            if command == "reload_servers()":
                return self.supermcp.reload_servers()
            elif command == "list_servers()":
                return self.supermcp.list_servers()
            elif command.startswith("inspect_server("):
                return self._handle_inspect_server(command)
            elif command.startswith("call_server_tool("):
                return self._handle_call_server_tool(command)
            else:
                return {"error": f"Unknown command: {command}"}
        except Exception as e:
            return {"error": f"Command execution failed: {e}"}
    
    def _handle_inspect_server(self, command: str) -> Dict[str, Any]:
        # Extract server name from inspect_server(server_name)
        server_name = command[16:-1]  # Remove "inspect_server(" and ")"
        return self.supermcp.inspect_server(server_name)
    
    def _handle_call_server_tool(self, command: str) -> Dict[str, Any]:
        try:
            # Extract the content between parentheses
            content = command[17:-1]  # Remove "call_server_tool(" and ")"
            
            # Parse arguments using simple state machine
            parts = self._parse_command_arguments(content)
            
            if len(parts) >= 3:
                server_name = parts[0]
                tool_name = parts[1]
                # For now, pass empty arguments - we can enhance this later
                return self.supermcp.call_server_tool(server_name, tool_name, {})
            else:
                return {"error": f"Invalid call_server_tool format: {command}"}
        except Exception as e:
            return {"error": f"Failed to parse call_server_tool: {e}"}
    
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
