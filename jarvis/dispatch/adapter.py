"""
DispatchAdapter — MCP client that connects to the dispatch binary.

dispatch is a Rust-based concurrent task orchestrator. It exposes itself
as an MCP server via `dispatch serve`. This adapter connects to it over
stdio and provides methods to send task batches, kill tasks, and receive
signals.

The adapter also handles semantic tool discovery:
- Embeds sub-task intents via Ollama (when embedding model is available)
- Passes vectors to dispatch → dmcp for cosine similarity search
- Falls back to listing all visible servers when embeddings are unavailable
- Auto-indexes non-approved servers after installation

All decision-making lives in JARVIS — dispatch and dmcp are tools that
do what they're told.
"""

import json
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.logger import get_logger
from .discovery import auto_index_server as discovery_auto_index_server
from .discovery import browse_vector as discovery_browse_vector
from .discovery import browse_vectors_batch as discovery_browse_vectors_batch
from .discovery import embedding_spec as discovery_embedding_spec
from .discovery import ensure_embedding_model as discovery_ensure_embedding_model
from .discovery import index_server as discovery_index_server
from .discovery import normalize_count as discovery_normalize_count
from .discovery import server_count as discovery_server_count
from .discovery import sync_index as discovery_sync_index
from .dmcp_registry import install_server as registry_install_server
from .dmcp_registry import list_server_tools as registry_list_server_tools
from .dmcp_registry import list_visible_servers as registry_list_visible_servers
from .dmcp_registry import run_dmcp as registry_run_dmcp
from .tool_discovery import discover_tools as run_tool_discovery
from .tool_discovery import format_available_tools as render_available_tools
from .transport import call_tool as transport_call_tool
from .transport import connect as transport_connect
from .transport import disconnect as transport_disconnect
from .transport import get_signal_window as transport_get_signal_window
from .transport import (
    require_connection,
)

logger = get_logger(__name__)


class DispatchAdapter:
    """Async MCP client for the dispatch binary."""

    def __init__(self):
        self.timeout = Config.DISPATCH_TIMEOUT
        self.session = None
        self._client = None
        self._connected = False

    async def connect(self):
        """Connect to the dispatch binary via stdio MCP."""
        await transport_connect(self, logger)

    async def disconnect(self):
        """Disconnect from dispatch."""
        await transport_disconnect(self, logger)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_tasks(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send a batch of tasks to dispatch for concurrent execution.

        Args:
            tasks: List of task dicts, each with:
                - server: MCP server name
                - tool: tool name on that server
                - params: dict of arguments
                - remind_after: (optional) seconds before a REMIND signal

        Returns:
            Dict with assigned PIDs and status.
        """
        if not require_connection(self, logger, "send_tasks"):
            return {"error": "Not connected to dispatch"}

        logger.info(f"Dispatch: Sending {len(tasks)} task(s)")
        for i, task in enumerate(tasks):
            logger.info(
                f"Dispatch:   task[{i}]: server={task.get('server')}, tool={task.get('tool')}, params={task.get('params')}"
            )

        return await transport_call_tool(
            self,
            logger,
            tool_name="dispatch",
            params={"tasks": tasks},
            op_name="send_tasks",
            timeout_error="Dispatch timed out after {timeout}s",
            failure_prefix="Failed to dispatch tasks",
            extractor=self._extract_content,
        )

    async def kill_tasks(self, pids: List[int]) -> Dict[str, Any]:
        """
        Kill running tasks by PID.

        Args:
            pids: List of task PIDs to terminate.

        Returns:
            Dict with kill confirmation.
        """
        if not require_connection(self, logger, "kill_tasks"):
            return {"error": "Not connected to dispatch"}

        logger.info(f"Dispatch: Killing PIDs {pids}")
        return await transport_call_tool(
            self,
            logger,
            tool_name="kill",
            params={"pids": pids},
            op_name="kill_tasks",
            timeout_error="Kill timed out after {timeout}s",
            failure_prefix="Failed to kill tasks",
            extractor=self._extract_content,
        )

    async def set_timer(
        self, label: str, duration: int, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Set a one-shot timer in dispatch.

        Args:
            label: Human-readable label for the signal window.
            duration: Seconds until REMIND fires.
            metadata: Opaque key-value data passed through in signals.

        Returns:
            Dict with assigned PID and status, or error.
        """
        if not require_connection(self, logger, "set_timer"):
            return {"error": "Not connected to dispatch"}

        logger.info(
            f"Dispatch: Setting timer label='{label}', duration={duration}s, metadata={metadata}"
        )
        params: Dict[str, Any] = {"label": label, "duration": duration}
        if metadata is not None:
            params["metadata"] = metadata

        return await transport_call_tool(
            self,
            logger,
            tool_name="timer",
            params=params,
            op_name="set_timer",
            timeout_error="Timer call timed out after {timeout}s",
            failure_prefix="Failed to set timer",
            extractor=self._extract_content,
        )

    async def get_signal_window(self) -> List[Dict[str, Any]]:
        """
        Get the current signal window from dispatch.

        Returns:
            List of signal dicts (up to 20), each with:
                - pid: task PID
                - type: INIT | EXIT | REMIND | WAIT | KILL
                - timestamp: ISO string
                - data: (optional) output or error payload
        """
        return await transport_get_signal_window(self, logger)

    def _extract_content(self, result) -> Dict[str, Any]:
        """
        Extract content from an MCP CallToolResult.

        Always returns a dict. Text content that isn't valid JSON is wrapped
        as {"output": "<text>"} so callers can safely use .get().
        """
        if (
            hasattr(result, "structuredContent")
            and result.structuredContent is not None
        ):
            return result.structuredContent

        if hasattr(result, "content") and result.content:
            texts = []
            for block in result.content:
                if hasattr(block, "text") and block.text:
                    texts.append(block.text)
            combined = "\n".join(texts) if texts else ""

            try:
                parsed = json.loads(combined)
                if isinstance(parsed, dict):
                    return parsed
                return {"output": parsed}
            except (json.JSONDecodeError, ValueError):
                return {"output": combined}

        return {"output": str(result)}

    # ------------------------------------------------------------------
    # MCP server registry operations via dmcp
    # ------------------------------------------------------------------

    async def _run_dmcp(self, *args: str) -> Optional[str]:
        """Run a dmcp command and return stdout, or None on failure."""
        return await registry_run_dmcp(logger, *args)

    async def list_visible_servers(self) -> Dict[str, Any]:
        """Return all visible MCP servers via `dmcp browse` with no keyword filter."""
        return await registry_list_visible_servers(logger)

    async def install_server(self, server_id: str) -> Dict[str, Any]:
        """Install an MCP server from registry via `dmcp install`."""
        return await registry_install_server(logger, server_id)

    async def list_server_tools(self, server_id: str) -> Dict[str, Any]:
        """List tools available on an installed MCP server."""
        return await registry_list_server_tools(logger, server_id)

    # ------------------------------------------------------------------
    # Semantic tool discovery (vector-based via dispatch → dmcp)
    # ------------------------------------------------------------------

    async def server_count(self) -> Dict[str, Any]:
        """
        Get the number of visible MCP servers from dmcp.

        Returns a dict normalized to ``{"total", "local", "registry"}``.
        Handles three response shapes from the dispatch binary / dmcp:
            - full dict       : {"total": N, "local": L, "registry": R}
            - bare number     : wrapped as {"output": N}
            - plain text "N"  : fallback from --json parse failure
        """
        return await discovery_server_count(self, logger)

    @staticmethod
    def _normalize_count(content: Any) -> Dict[str, int]:
        """Coerce a server-count response into {total, local, registry}."""
        return discovery_normalize_count(content)

    async def embedding_spec(self) -> Optional[Dict[str, Any]]:
        """
        Get the registry's embedding model spec from dmcp.

        Returns: {"model": "nomic-embed-text", "version": "v1.5", "dimensions": 768}
        or None if not available.
        """
        return await discovery_embedding_spec(self, logger)

    async def sync_index(self) -> Dict[str, Any]:
        """Trigger dmcp sync-index to refresh the vector index from registries."""
        return await discovery_sync_index(self, logger)

    async def browse_vector(
        self,
        vector: List[float],
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Semantic search: find MCP servers/tools by vector similarity.

        Passes the vector to dispatch → dmcp browse --vector for cosine
        similarity search against the local vector index.
        """
        return await discovery_browse_vector(self, logger, vector, top_k, min_score)

    async def browse_vectors_batch(
        self,
        vectors: List[List[float]],
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Batch semantic search: search multiple vectors in one call.

        Returns grouped results — one result set per input vector.
        """
        return await discovery_browse_vectors_batch(
            self, logger, vectors, top_k, min_score
        )

    async def index_server(
        self,
        server_id: str,
        vectors: Dict[str, Any],
        name: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Index a non-approved server's vectors for semantic search.

        Called after installing a server that doesn't have pre-computed
        vectors in the registry. JARVIS embeds the tool descriptions
        locally via Ollama and passes the vectors here for storage.

        Args:
            server_id: The MCP server ID.
            vectors: {"server": [...], "tools": {"tool_name": [...]}}
            name: Optional server name.
            description: Optional server description.
        """
        return await discovery_index_server(
            self,
            logger,
            server_id,
            vectors,
            name,
            description,
        )

    async def discover_tools(
        self,
        tasks: List[Dict[str, Any]],
        embeddings: Optional[Any] = None,
    ) -> str:
        """
        Tool discovery — the main entry point after a ``plan`` action.

        Always uses embedding search. Falls back to listing all visible
        servers when embeddings are unavailable or fail.

        Args:
            tasks: List of dicts with "intent" (required) and optional
                   "top_k", "min_score".
            embeddings: OllamaEmbeddings instance for local embedding.
        """
        return await run_tool_discovery(self, logger, tasks, embeddings)

    @staticmethod
    def _format_available_tools(results: List[Dict[str, Any]]) -> str:
        """
        Render discovery results into the two blocks the LLM prompt names:

          MATCHED_TOOLS       — installed + has tool_name; dispatch-ready
          CANDIDATE_SERVERS   — not installed, or no tool_name yet; needs
                                install + list_tools before dispatch

        Deduplicated by (server_id, tool_name) for tool rows and by
        server_id for server rows.
        """
        return render_available_tools(results)

    async def auto_index_server(
        self,
        server_id: str,
        embeddings: Optional[Any] = None,
    ) -> None:
        """
        Auto-index a non-approved server after installation.

        Reads the server's tool descriptions, embeds them locally via
        Ollama, and stores the vectors in dmcp's local index.
        Does nothing if embeddings are unavailable.
        """
        await discovery_auto_index_server(self, logger, server_id, embeddings)

    async def ensure_embedding_model(self, embeddings: Optional[Any] = None) -> None:
        """
        On startup, check the registry's embedding spec and ensure
        the correct model is available locally.
        """
        await discovery_ensure_embedding_model(self, logger, embeddings)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
