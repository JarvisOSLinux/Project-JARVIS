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
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(self.supermcp_path)]
            )
            self._client = stdio_client(params)
            read, write = await self._client.__aenter__()
            self.session = ClientSession(read, write)
            await self.session.initialize()
            logger.info("SuperMCP: Connected successfully!")
        except Exception as e:
            logger.error(f"SuperMCP: Connection failed: {e}")
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
            result = await self.session.call_tool("reload_servers", {})
            return self._extract_content(result)
        except Exception as e:
            return {"error": f"Failed to reload servers: {e}"}
            
    async def list_servers(self) -> List[Dict[str, Any]]:
        """List all available MCP servers"""
        try:
            result = await self.session.call_tool("list_servers", {})
            return self._extract_content(result)
        except Exception as e:
            return [{"error": f"Failed to list servers: {e}"}]
            
    async def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Inspect a specific MCP server's capabilities"""
        try:
            result = await self.session.call_tool("inspect_server", {"name": server_name})
            return self._extract_content(result)
        except Exception as e:
            return {"error": f"Failed to inspect server {server_name}: {e}"}
            
    async def call_server_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool from a specific MCP server"""
        try:
            result = await self.session.call_tool("call_server_tool", {
                "name": server_name,
                "tool_name": tool_name,
                "arguments": arguments or {}
            })
            return self._extract_content(result)
        except Exception as e:
            return {"error": f"Failed to call {server_name}.{tool_name}: {e}"}
            
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
        self.client = SuperMCPClient()
        
    def reload_servers(self) -> Dict[str, Any]:
        """Synchronous wrapper for reload_servers"""
        return asyncio.run(self._run_async(self.client.reload_servers()))
        
    def list_servers(self) -> List[Dict[str, Any]]:
        """Synchronous wrapper for list_servers"""
        return asyncio.run(self._run_async(self.client.list_servers()))
        
    def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Synchronous wrapper for inspect_server"""
        return asyncio.run(self._run_async(self.client.inspect_server(server_name)))
        
    def call_server_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Synchronous wrapper for call_server_tool"""
        return asyncio.run(self._run_async(self.client.call_server_tool(server_name, tool_name, arguments)))
        
    async def _run_async(self, coro):
        """Helper to run async operations"""
        async with self.client:
            try:
                return await asyncio.wait_for(coro, timeout=self.client.timeout)
            except asyncio.TimeoutError:
                return {"error": f"SuperMCP operation timed out after {self.client.timeout} seconds"}
