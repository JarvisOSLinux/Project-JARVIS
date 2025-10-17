import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)

class SuperMCPClient:
    def __init__(self):
        # Use config path or fallback to default
        config_path = Config.SUPERMCP_SERVER_PATH
        if not Path(config_path).is_absolute():
            self.supermcp_path = Path(__file__).parent / config_path
        else:
            self.supermcp_path = Path(config_path)
        
        self.timeout = Config.SUPERMCP_TIMEOUT
        self.session: Optional[ClientSession] = None
        self._client = None
        
    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.disconnect()
        
    async def connect(self):
        """Connect to SuperMCP server"""
        try:
            logger.info(f"SuperMCP: Connecting to server at {self.supermcp_path}...")
            
            # Check if the SuperMCP.py file exists
            if not self.supermcp_path.exists():
                raise FileNotFoundError(f"SuperMCP server not found at: {self.supermcp_path}")
            
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(self.supermcp_path)]
            )
            logger.debug(f"SuperMCP: Starting server process: {sys.executable} {self.supermcp_path}")
            
            self._client = stdio_client(params)
            logger.debug("SuperMCP: Entering stdio client context...")
            
            # Add timeout to the connection process
            read, write = await asyncio.wait_for(
                self._client.__aenter__(),
                timeout=10.0  # 10 second timeout for initial connection
            )
            logger.debug("SuperMCP: Stdio client connected, creating session...")
            
            self.session = ClientSession(read, write)
            logger.debug("SuperMCP: Session created, initializing...")
            
            await asyncio.wait_for(
                self.session.initialize(),
                timeout=10.0  # 10 second timeout for session initialization
            )
            logger.info("SuperMCP: Connected successfully!")
        except asyncio.TimeoutError as e:
            error_msg = "SuperMCP: Connection timed out - server may be hanging during startup"
            logger.error(error_msg)
            raise TimeoutError(error_msg) from e
        except Exception as e:
            logger.error(f"SuperMCP: Connection failed: {e}", exc_info=True)
            raise
            
    async def disconnect(self):
        """Disconnect from SuperMCP server"""
        try:
            if self.session:
                await self.session.close()
            if self._client:
                await self._client.__aexit__(None, None, None)
            logger.info("SuperMCP: Disconnected successfully!")
        except Exception as e:
            logger.error(f"SuperMCP: Disconnect error: {e}")
            
    async def reload_servers(self) -> Dict[str, Any]:
        """Reload available MCP servers"""
        try:
            logger.info("SuperMCP: Calling reload_servers...")
            result = await self.session.call_tool("reload_servers", {})
            logger.debug(f"SuperMCP: reload_servers raw result: {result}")
            extracted = self._extract_content(result)
            logger.info(f"SuperMCP: reload_servers completed: {extracted}")
            return extracted
        except Exception as e:
            error_msg = f"Failed to reload servers: {e}"
            logger.error(f"SuperMCP: {error_msg}", exc_info=True)
            return {"error": error_msg}
            
    async def list_servers(self) -> List[Dict[str, Any]]:
        """List all available MCP servers"""
        try:
            logger.info("SuperMCP: Calling list_servers...")
            result = await self.session.call_tool("list_servers", {})
            logger.debug(f"SuperMCP: list_servers raw result: {result}")
            extracted = self._extract_content(result)
            logger.info(f"SuperMCP: list_servers completed, found {len(extracted) if isinstance(extracted, list) else 'N/A'} servers")
            return extracted
        except Exception as e:
            error_msg = f"Failed to list servers: {e}"
            logger.error(f"SuperMCP: {error_msg}", exc_info=True)
            return [{"error": error_msg}]
            
    async def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Inspect a specific MCP server's capabilities"""
        try:
            logger.info(f"SuperMCP: Calling inspect_server for '{server_name}'...")
            result = await self.session.call_tool("inspect_server", {"name": server_name})
            logger.debug(f"SuperMCP: inspect_server raw result: {result}")
            extracted = self._extract_content(result)
            logger.info(f"SuperMCP: inspect_server completed for '{server_name}'")
            return extracted
        except Exception as e:
            error_msg = f"Failed to inspect server {server_name}: {e}"
            logger.error(f"SuperMCP: {error_msg}", exc_info=True)
            return {"error": error_msg}
            
    async def call_server_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool from a specific MCP server"""
        try:
            logger.info(f"SuperMCP: Calling tool '{tool_name}' on server '{server_name}' with args: {arguments}")
            result = await self.session.call_tool("call_server_tool", {
                "name": server_name,
                "tool_name": tool_name,
                "arguments": arguments or {}
            })
            logger.debug(f"SuperMCP: call_server_tool raw result: {result}")
            extracted = self._extract_content(result)
            logger.info(f"SuperMCP: call_server_tool completed for '{server_name}.{tool_name}'")
            return extracted
        except Exception as e:
            error_msg = f"Failed to call {server_name}.{tool_name}: {e}"
            logger.error(f"SuperMCP: {error_msg}", exc_info=True)
            return {"error": error_msg}
            
    def _extract_content(self, result) -> Any:
        """Extract content from MCP result"""
        if hasattr(result, 'structuredContent') and result.structuredContent is not None:
            return result.structuredContent
        elif hasattr(result, 'content') and result.content:
            # Concatenate text blocks
            texts = []
            for block in result.content:
                if hasattr(block, 'text') and block.text:
                    texts.append(block.text)
            return "\n".join(texts) if texts else str(result)
        else:
            return str(result)

# Synchronous wrapper for easier integration with existing JARVIS code
class SuperMCPWrapper:
    def __init__(self):
        self.timeout = Config.SUPERMCP_TIMEOUT
        config_path = Config.SUPERMCP_SERVER_PATH
        if not Path(config_path).is_absolute():
            self.supermcp_path = Path(__file__).parent / config_path
        else:
            self.supermcp_path = Path(config_path)
        
    def reload_servers(self) -> Dict[str, Any]:
        """Synchronous wrapper for reload_servers"""
        return asyncio.run(self._run_operation_with_connection("reload_servers", {}))
        
    def list_servers(self) -> List[Dict[str, Any]]:
        """Synchronous wrapper for list_servers"""
        return asyncio.run(self._run_operation_with_connection("list_servers", {}))
        
    def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Synchronous wrapper for inspect_server"""
        return asyncio.run(self._run_operation_with_connection("inspect_server", {"name": server_name}))
        
    def call_server_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Synchronous wrapper for call_server_tool"""
        return asyncio.run(self._run_operation_with_connection("call_server_tool", {
            "name": server_name,
            "tool_name": tool_name,
            "arguments": arguments or {}
        }))
    
    async def _run_operation_with_connection(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Run a single operation with its own connection lifecycle"""
        logger.info(f"SuperMCPWrapper: Starting operation '{tool_name}'")
        
        try:
            # Create connection parameters
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(self.supermcp_path)]
            )
            
            logger.debug(f"SuperMCPWrapper: Connecting and initializing for '{tool_name}'...")
            
            # Use the same pattern as our successful test
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=10.0)
                    logger.debug(f"SuperMCPWrapper: Calling tool '{tool_name}'...")
                    
                    # Call the tool
                    result = await asyncio.wait_for(
                        session.call_tool(tool_name, arguments),
                        timeout=self.timeout
                    )
                    
                    logger.info(f"SuperMCPWrapper: Operation '{tool_name}' completed successfully")
                    
                    # Extract content
                    return self._extract_content(result)
                    
        except asyncio.TimeoutError:
            error_msg = f"SuperMCP operation '{tool_name}' timed out after {self.timeout} seconds"
            logger.error(f"SuperMCPWrapper: {error_msg}")
            return {"error": error_msg}
        except Exception as e:
            error_msg = f"SuperMCP operation '{tool_name}' failed: {e}"
            logger.error(f"SuperMCPWrapper: {error_msg}", exc_info=True)
            return {"error": error_msg}
    
    def _extract_content(self, result) -> Any:
        """Extract content from MCP result"""
        if hasattr(result, 'structuredContent') and result.structuredContent is not None:
            return result.structuredContent
        elif hasattr(result, 'content') and result.content:
            # Concatenate text blocks
            texts = []
            for block in result.content:
                if hasattr(block, 'text') and block.text:
                    texts.append(block.text)
            return "\n".join(texts) if texts else str(result)
        else:
            return str(result)
