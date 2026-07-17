"""
DispatchAdapter — MCP client that connects to the dispatch binary.

dispatch is a Rust-based concurrent task orchestrator. It exposes itself
as an MCP server via `dispatch serve`. This adapter connects to it over
stdio and provides methods to send task batches, kill tasks, and receive
signals.

The adapter also handles semantic tool discovery:
- Embeds sub-task intents via Ollama (when embedding search is enabled)
- Passes vectors to dispatch → dmcp for cosine similarity search
- Falls back to keyword search when vector search is unavailable or
  returns no results
- Auto-indexes non-approved servers after installation

All decision-making lives in JARVIS — dispatch and dmcp are tools that
do what they're told.
"""

import json
from typing import Any, Callable, Dict, List, Optional

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
from .dmcp_registry import get_server_manifest as registry_get_server_manifest
from .dmcp_registry import install_server as registry_install_server
from .dmcp_registry import list_server_tools as registry_list_server_tools
from .dmcp_registry import run_dmcp as registry_run_dmcp
from .dmcp_registry import run_server_setup as registry_run_server_setup
from .dmcp_registry import search_servers as registry_search_servers
from .dmcp_registry import uninstall_server as registry_uninstall_server
from .transport import call_tool as transport_call_tool
from .transport import connect as transport_connect
from .transport import disconnect as transport_disconnect
from .transport import get_signal_window as transport_get_signal_window
from .transport import (
    require_connection,
)

logger = get_logger(__name__)

# How long to wait for a blocking 'wait' call before giving up (seconds).
# Tasks can run for a long time; this is intentionally much larger than
# the regular DISPATCH_TIMEOUT.
_WAIT_TIMEOUT = getattr(Config, "DISPATCH_WAIT_TIMEOUT", 600.0)


class DispatchAdapter:
    """Async MCP client for the dispatch binary."""

    def __init__(self):
        self.timeout = Config.DISPATCH_TIMEOUT
        self.session = None
        self._client = None
        self._connected = False
        # Sink for signals PUSHED by dispatch (notifications/message, #26).
        # Set by the runtime to EventMerger.enqueue_pushed_signal once the
        # merger exists; the MCP logging handler in transport reads it at call
        # time, so it is safe for it to be None during early connect.
        self._signal_sink: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_signal_sink(self, sink: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        """Register where pushed dispatch signals are delivered (#26)."""
        self._signal_sink = sink

    async def connect(self):
        """Connect to the dispatch binary via stdio MCP."""
        await transport_connect(self, logger)

    async def disconnect(self):
        """Disconnect from dispatch."""
        await transport_disconnect(self, logger)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_tasks(
        self,
        tasks: List[Dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a batch of tasks to dispatch for concurrent execution.

        Args:
            tasks: List of task dicts, each with:
                - server: MCP server name
                - tool: tool name on that server
                - params: dict of arguments
                - remind_after: (optional) seconds before a REMIND signal
            session_id: Optional opaque session identifier (e.g. goal ID).
                When provided, the dispatch binary scopes its signal window
                to PIDs belonging to this session.

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

        params: Dict[str, Any] = {"tasks": tasks}
        if session_id is not None:
            params["session_id"] = session_id

        return await transport_call_tool(
            self,
            logger,
            tool_name="dispatch",
            params=params,
            op_name="send_tasks",
            timeout_error="Dispatch timed out after {timeout}s",
            failure_prefix="Failed to dispatch tasks",
            extractor=self._extract_content,
        )

    async def wait_task(self, pids: List[int]) -> Dict[str, Any]:
        """
        Acknowledge a REMIND signal and block until the given PIDs complete.

        Calls the dispatch binary's 'wait' MCP tool, which records WAIT
        signals for each PID and then calls wait_for_event() again —
        blocking until EXIT or TIMEOUT fires, then returning the updated
        signal window.

        This is the correct mechanism to use after the LLM issues a
        {"action": "wait"} response: it keeps the sub-chain alive and
        delivers EXIT data directly, bypassing any EventMerger polling
        race condition.

        Args:
            pids: List of task PIDs to wait for.

        Returns:
            Dict / list containing the signal window with EXIT signal(s).
        """
        if not require_connection(self, logger, "wait_task"):
            return {"error": "Not connected to dispatch"}

        logger.info(
            f"Dispatch: Acknowledging REMIND — blocking until PIDs {pids} complete"
        )
        return await transport_call_tool(
            self,
            logger,
            tool_name="wait",
            params={"pids": pids},
            op_name="wait_task",
            timeout_error="wait_task timed out after {timeout}s",
            failure_prefix="Failed to wait for task completion",
            extractor=self._extract_content,
            timeout=_WAIT_TIMEOUT,
        )

    async def kill_tasks(self, pids: List[int]) -> Dict[str, Any]:
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
        return await transport_get_signal_window(self, logger)

    def _extract_content(self, result) -> Dict[str, Any]:
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
    # MCP server discovery via dmcp (on-demand, keyword-based)
    # ------------------------------------------------------------------

    async def _run_dmcp(self, *args: str) -> Optional[str]:
        """Run a dmcp command and return stdout, or None on failure."""
        return await registry_run_dmcp(logger, *args)

    async def search_servers(self, keywords: List[str]) -> Dict[str, Any]:
        return await registry_search_servers(logger, keywords)

    async def install_server(self, server_id: str) -> Dict[str, Any]:
        """Install an MCP server from registry via `dmcp install`."""
        return await registry_install_server(logger, server_id)

    async def uninstall_server(self, server_id: str) -> Dict[str, Any]:
        """Uninstall an MCP server via `dmcp uninstall`."""
        return await registry_uninstall_server(logger, server_id)

    async def list_server_tools(self, server_id: str) -> Dict[str, Any]:
        """List tools available on an installed MCP server."""
        return await registry_list_server_tools(logger, server_id)

    async def get_server_manifest(self, server_id: str) -> Dict[str, Any]:
        """Return the manifest for a server, reading locally with no network call.

        Checks installed path first, falls back to registry clone.
        """
        return await registry_get_server_manifest(logger, server_id)

    async def run_server_setup(self, server_id: str) -> Dict[str, Any]:
        """Run the setup script for an installed server via `dmcp setup <id>`."""
        return await registry_run_server_setup(logger, server_id)

    @staticmethod
    def _sanitize_config_key(key: str) -> str:
        """Normalise an LLM-supplied config key to an env-var name.

        The LLM may copy flag names from error messages (e.g. --brave-api-key).
        dmcp config set expects the bare env-var form (BRAVE_API_KEY).
        """
        key = key.lstrip("-")
        return key.replace("-", "_").upper()

    async def set_server_config(self, server_id: str, config: Dict[str, str]) -> None:
        """Persist config key-value pairs for a server via `dmcp config <id> set`.

        Each key is stored in the server's installed manifest config section and
        will be injected as an environment variable when the server is next run.
        """
        for raw_key, value in config.items():
            if not value:
                continue
            key = self._sanitize_config_key(raw_key)
            result = await self._run_dmcp("config", server_id, "set", key, value)
            if result is None:
                raise RuntimeError(
                    f"dmcp config set failed for '{key}' on '{server_id}' — check dmcp logs"
                )

    # ------------------------------------------------------------------
    # Semantic tool discovery (vector-based via dispatch → dmcp)
    # ------------------------------------------------------------------

    async def server_count(self) -> Dict[str, Any]:
        return await discovery_server_count(self, logger)

    @staticmethod
    def _normalize_count(content: Any) -> Dict[str, int]:
        return discovery_normalize_count(content)

    async def embedding_spec(self) -> Optional[Dict[str, Any]]:
        return await discovery_embedding_spec(self, logger)

    async def sync_index(self) -> Dict[str, Any]:
        return await discovery_sync_index(self, logger)

    async def browse_vector(
        self,
        vector: List[float],
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
        return await discovery_browse_vector(self, logger, vector, top_k, min_score)

    async def browse_vectors_batch(
        self,
        vectors: List[List[float]],
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
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
        return await discovery_index_server(
            self,
            logger,
            server_id,
            vectors,
            name,
            description,
        )

    async def select_discovery_mode(
        self,
        embeddings: Optional[Any] = None,
    ) -> str:
        """Return the active tool-discovery backend: ``"embedding"`` or ``"keyword"``."""
        if not Config.ALLOW_EMBEDDING_SEARCH or embeddings is None:
            return "keyword"

        count = await self.server_count()
        total = count.get("total", 0)

        if (
            Config.ENFORCE_EMBEDDING_SEARCH
            or total >= Config.EMBEDDING_SEARCH_THRESHOLD
        ):
            return "embedding"
        return "keyword"

    async def auto_index_server(
        self,
        server_id: str,
        embeddings: Optional[Any] = None,
    ) -> None:
        await discovery_auto_index_server(self, logger, server_id, embeddings)

    async def ensure_embedding_model(self, embeddings: Optional[Any] = None) -> None:
        await discovery_ensure_embedding_model(self, logger, embeddings)

    async def search_by_capability(
        self,
        capability: str,
        embeddings: Optional[Any] = None,
        top_k: int = 5,
        min_score: float = 0.25,
    ) -> Dict[str, Any]:
        """Semantic search for MCP servers/tools matching a capability description.

        Embeds `capability` and runs a vector similarity search against the full
        index (installed + registry). Falls back to keyword search on the
        capability words when embeddings are unavailable.
        """
        if embeddings is not None and Config.ALLOW_EMBEDDING_SEARCH:
            try:
                vector = embeddings.embed_single(capability)
                result = await self.browse_vector(
                    vector, top_k=top_k, min_score=min_score
                )
                entries = result.get("results", [])
                if entries:
                    logger.info(
                        f"Dispatch: search_by_capability '{capability}' → "
                        f"{len(entries)} hit(s) via embedding"
                    )
                    return {"results": entries, "mode": "embedding"}
                logger.info(
                    f"Dispatch: search_by_capability '{capability}' — "
                    "embedding returned nothing, falling back to keyword"
                )
            except Exception as e:
                logger.warning(
                    f"Dispatch: search_by_capability embedding failed: {e}, "
                    "falling back to keyword"
                )

        # Keyword fallback — split capability into words, strip short tokens
        words = [w for w in capability.lower().split() if len(w) > 2]
        if not words:
            return {"results": [], "mode": "keyword"}

        kw_result = await self.search_servers(words)
        servers = kw_result.get("servers", [])

        # Flatten servers into the same result shape as vector search
        entries: List[Dict[str, Any]] = []
        seen: set = set()
        for s in servers:
            sid = s.get("id", s.get("server_id", ""))
            if not sid or sid in seen:
                continue
            seen.add(sid)
            entries.append(
                {
                    "server_id": sid,
                    "server_name": s.get("name", sid),
                    "server_description": s.get("description", s.get("summary", "")),
                    "installed": bool(s.get("installed", False)),
                    "score": 0.0,
                }
            )

        logger.info(
            f"Dispatch: search_by_capability '{capability}' → "
            f"{len(entries)} hit(s) via keyword"
        )
        return {"results": entries, "mode": "keyword"}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
