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

import asyncio
import json
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.logger import get_logger
from .dmcp_registry import install_server as registry_install_server
from .dmcp_registry import list_server_tools as registry_list_server_tools
from .dmcp_registry import run_dmcp as registry_run_dmcp
from .dmcp_registry import search_servers as registry_search_servers
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
    # MCP server discovery via dmcp (on-demand, keyword-based)
    # ------------------------------------------------------------------

    async def _run_dmcp(self, *args: str) -> Optional[str]:
        """Run a dmcp command and return stdout, or None on failure."""
        return await registry_run_dmcp(logger, *args)

    async def search_servers(self, keywords: List[str]) -> Dict[str, Any]:
        """
        Search for MCP servers by keywords via `dmcp browse`.

        Returns installed matches first, then not-installed ones from registries.
        """
        return await registry_search_servers(logger, keywords)

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
        if not self._connected:
            # Fall back to direct dmcp call if dispatch isn't connected
            raw = await self._run_dmcp("count", "--json")
            if raw is None:
                return {"total": 0, "local": 0, "registry": 0}
            try:
                parsed = json.loads(raw)
                return self._normalize_count(parsed)
            except json.JSONDecodeError:
                # Plain number output
                try:
                    return {"total": int(raw.strip()), "local": 0, "registry": 0}
                except ValueError:
                    return {"total": 0, "local": 0, "registry": 0}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("server_count", {}),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            normalized = self._normalize_count(content)
            logger.info(f"Dispatch: Server count: {normalized}")
            return normalized
        except Exception as e:
            logger.warning(f"Dispatch: server_count failed: {e}")
            return {"total": 0, "local": 0, "registry": 0}

    @staticmethod
    def _normalize_count(content: Any) -> Dict[str, int]:
        """Coerce a server-count response into {total, local, registry}."""
        default = {"total": 0, "local": 0, "registry": 0}
        if isinstance(content, int):
            return {"total": content, "local": 0, "registry": 0}
        if not isinstance(content, dict):
            return default

        if "total" in content:
            return {
                "total": int(content.get("total", 0) or 0),
                "local": int(content.get("local", 0) or 0),
                "registry": int(content.get("registry", 0) or 0),
            }

        # Bare number wrapped by _extract_content as {"output": N}
        val = content.get("output")
        if isinstance(val, int):
            return {"total": val, "local": 0, "registry": 0}
        if isinstance(val, str):
            try:
                return {"total": int(val.strip()), "local": 0, "registry": 0}
            except ValueError:
                return default
        return default

    async def embedding_spec(self) -> Optional[Dict[str, Any]]:
        """
        Get the registry's embedding model spec from dmcp.

        Returns: {"model": "nomic-embed-text", "version": "v1.5", "dimensions": 768}
        or None if not available.
        """
        if not self._connected:
            raw = await self._run_dmcp("embedding-spec")
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("embedding_spec", {}),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            if "error" in content:
                logger.warning(f"Dispatch: embedding_spec: {content['error']}")
                return None
            logger.info(f"Dispatch: Embedding spec: {content}")
            return content
        except Exception as e:
            logger.warning(f"Dispatch: embedding_spec failed: {e}")
            return None

    async def sync_index(self) -> Dict[str, Any]:
        """Trigger dmcp sync-index to refresh the vector index from registries."""
        if not self._connected:
            raw = await self._run_dmcp("sync-index")
            if raw is None:
                return {"error": "dmcp sync-index failed"}
            return {"output": raw.strip()}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("sync_index", {}),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            logger.info(f"Dispatch: sync_index result: {content}")
            return content
        except Exception as e:
            logger.warning(f"Dispatch: sync_index failed: {e}")
            return {"error": f"sync_index failed: {e}"}

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
        if not self._connected:
            vector_json = json.dumps(vector)
            raw = await self._run_dmcp(
                "browse",
                "--vector",
                vector_json,
                "--top-k",
                str(top_k),
                "--min-score",
                str(min_score),
                "--json",
            )
            if raw is None:
                return {"results": []}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"results": []}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "browse_vector",
                    {
                        "vector": vector,
                        "top_k": top_k,
                        "min_score": min_score,
                    },
                ),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            return content
        except Exception as e:
            logger.warning(f"Dispatch: browse_vector failed: {e}")
            return {"results": []}

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
        if not self._connected:
            vectors_json = json.dumps(vectors)
            raw = await self._run_dmcp(
                "browse",
                "--vectors",
                vectors_json,
                "--top-k",
                str(top_k),
                "--min-score",
                str(min_score),
                "--json",
            )
            if raw is None:
                return {"results": [[] for _ in vectors]}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"results": [[] for _ in vectors]}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "browse_vectors",
                    {
                        "vectors": vectors,
                        "top_k": top_k,
                        "min_score": min_score,
                    },
                ),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            return content
        except Exception as e:
            logger.warning(f"Dispatch: browse_vectors_batch failed: {e}")
            return {"results": [[] for _ in vectors]}

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
        if not self._connected:
            args = ["index-server", server_id, "--vectors", json.dumps(vectors)]
            if name:
                args.extend(["--name", name])
            if description:
                args.extend(["--description", description])
            raw = await self._run_dmcp(*args)
            if raw is None:
                return {"error": f"Failed to index server '{server_id}'"}
            return {"indexed": server_id, "output": raw.strip()}

        try:
            params: Dict[str, Any] = {
                "server_id": server_id,
                "vectors": json.dumps(vectors),
            }
            if name:
                params["name"] = name
            if description:
                params["description"] = description

            result = await asyncio.wait_for(
                self.session.call_tool("index_server", params),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            logger.info(f"Dispatch: Indexed server '{server_id}': {content}")
            return content
        except Exception as e:
            logger.warning(f"Dispatch: index_server failed: {e}")
            return {"error": f"Failed to index server: {e}"}

    async def select_discovery_mode(
        self,
        embeddings: Optional[Any] = None,
    ) -> str:
        """
        Return the active tool-discovery backend: ``"embedding"`` or ``"keyword"``.

        Chosen from config + runtime state:
          - embedding:  ALLOW_EMBEDDING_SEARCH, embeddings available,
                        and (visible_servers >= threshold OR enforce flag)
          - keyword:    everything else

        The LLM never sees these names — this only drives which dispatch
        system prompt JARVIS installs and which search path runs.
        """
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

    async def discover_tools(
        self,
        tasks: List[Dict[str, Any]],
        embeddings: Optional[Any] = None,
    ) -> str:
        """
        Tool discovery — the main entry point after a ``plan`` action.

        Runs whichever backend ``select_discovery_mode`` picked (embedding or
        keyword) and returns a formatted ``MATCHED_TOOLS`` / ``CANDIDATE_SERVERS``
        block for prompt injection. Empty string if nothing matched.

        Args:
            tasks: List of dicts with "intent" (required) and optional
                   "keywords", "top_k", "min_score".
            embeddings: OllamaEmbeddings instance for local embedding.
        """
        mode = await self.select_discovery_mode(embeddings)
        logger.info(f"Dispatch: discover_tools — {len(tasks)} sub-task(s), mode={mode}")

        all_results: List[Dict[str, Any]] = []

        if mode == "embedding":
            intents = [t.get("intent", "") for t in tasks]
            try:
                vectors = embeddings.embed_batch(intents)
                top_k = max(t.get("top_k", 5) for t in tasks)
                min_score = min(t.get("min_score", 0.3) for t in tasks)

                batch_result = await self.browse_vectors_batch(
                    vectors=vectors,
                    top_k=top_k,
                    min_score=min_score,
                )

                result_sets = batch_result.get("results", [])
                if isinstance(result_sets, list):
                    for i, result_set in enumerate(result_sets):
                        if isinstance(result_set, list) and result_set:
                            for r in result_set:
                                r["_source_task"] = intents[i]
                            all_results.extend(result_set)
                        else:
                            # No vector match for this task — fall back to
                            # keyword search so the user still gets candidates.
                            all_results.extend(await self._keyword_fallback(tasks[i]))

            except Exception as e:
                logger.warning(
                    f"Dispatch: Embedding discovery failed, falling back to keywords: {e}"
                )
                for task in tasks:
                    all_results.extend(await self._keyword_fallback(task))
        else:
            for task in tasks:
                all_results.extend(await self._keyword_fallback(task))

        if not all_results:
            logger.info("Dispatch: discover_tools found no matching tools")
            return ""

        return self._format_available_tools(all_results)

    async def _keyword_fallback(self, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Keyword search fallback for a single sub-task.

        For installed servers, expand each server into per-tool rows with
        real ``tool_name`` / ``description`` / ``params`` so the LLM can
        dispatch directly without guessing. Non-installed matches stay
        server-level — the LLM must install them before they become
        dispatchable.
        """
        keywords = task.get("keywords", [])
        if not keywords:
            # Extract basic keywords from intent
            intent = task.get("intent", "")
            keywords = [w for w in intent.lower().split() if len(w) > 3]

        if not keywords:
            return []

        result = await self.search_servers(keywords)
        servers = result.get("servers", [])

        source_task = task.get("intent", "")
        tool_results: List[Dict[str, Any]] = []

        for server in servers:
            server_id = server.get("id", server.get("server_id", ""))
            if not server_id:
                continue

            installed = bool(server.get("installed", False))
            server_name = server.get("name", server_id)
            server_desc = server.get("description", "")

            if installed:
                # Expand installed servers to per-tool rows
                tools_result = await self.list_server_tools(server_id)
                tools = (
                    tools_result.get("tools", [])
                    if isinstance(tools_result, dict)
                    else []
                )

                if tools:
                    for tool in tools:
                        if not isinstance(tool, dict):
                            continue
                        tool_name = tool.get("name", "")
                        if not tool_name:
                            continue
                        tool_results.append(
                            {
                                "server_id": server_id,
                                "server_name": server_name,
                                "tool_name": tool_name,
                                "description": tool.get("description", ""),
                                "params": tool.get(
                                    "inputSchema", tool.get("params", {})
                                ),
                                "installed": True,
                                "score": 0,  # keyword matches don't have scores
                                "_source": "keyword",
                                "_source_task": source_task,
                            }
                        )
                    continue

                # Installed but no tool info — fall through to server-level row
                logger.debug(
                    f"Dispatch: installed server '{server_id}' exposed no tools "
                    "via list_server_tools; emitting server-level row"
                )

            tool_results.append(
                {
                    "server_id": server_id,
                    "server_name": server_name,
                    "description": server_desc,
                    "installed": installed,
                    "score": 0,
                    "_source": "keyword",
                    "_source_task": source_task,
                }
            )

        return tool_results

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
        matched: List[Dict[str, Any]] = []
        candidates: List[Dict[str, Any]] = []

        seen_tools: set = set()
        seen_servers: set = set()

        for r in results:
            server_id = r.get("server_id", "")
            if not server_id:
                continue
            tool_name = r.get("tool_name", "")
            installed = bool(r.get("installed", True))

            is_dispatchable = bool(tool_name) and installed
            if is_dispatchable:
                key = (server_id, tool_name)
                if key in seen_tools:
                    continue
                seen_tools.add(key)
                matched.append(r)
            else:
                if server_id in seen_servers:
                    continue
                seen_servers.add(server_id)
                candidates.append(r)

        sections: List[str] = []

        if matched:
            lines = ["MATCHED_TOOLS:"]
            for r in matched:
                server_id = r.get("server_id", "unknown")
                tool_name = r.get("tool_name", "")
                line = f"  {server_id}/{tool_name}"
                score = r.get("score", 0)
                if score:
                    line += f" (relevance: {score:.0%})"
                description = r.get("description", "")
                if description:
                    line += f"\n    {description}"
                params = r.get("params", r.get("parameter_schema", ""))
                if params:
                    params_str = (
                        json.dumps(params) if isinstance(params, dict) else str(params)
                    )
                    line += f"\n    params: {params_str}"
                lines.append(line)
            sections.append("\n".join(lines))

        if candidates:
            lines = ["CANDIDATE_SERVERS (not installed — use install + list_tools):"]
            for r in candidates:
                server_id = r.get("server_id", "unknown")
                line = f"  {server_id}"
                description = r.get("description", "")
                if description:
                    line += f"\n    {description}"
                lines.append(line)
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

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
        if embeddings is None:
            logger.debug(
                f"Dispatch: Skipping auto-index for '{server_id}' (no embeddings)"
            )
            return

        # Get tool descriptions from the installed server
        tools_result = await self.list_server_tools(server_id)
        tools = tools_result.get("tools", [])
        if not tools:
            logger.debug(f"Dispatch: No tools found for '{server_id}', skipping index")
            return

        try:
            # Build texts to embed
            server_desc = f"{server_id}"
            tool_texts = {}
            for tool in tools:
                name = tool.get("name", "")
                desc = tool.get("description", "")
                params = tool.get("params", tool.get("inputSchema", {}))
                text = f"{server_id} | {name} | {desc} | params: {json.dumps(params)}"
                tool_texts[name] = text

            # Embed server description
            server_vector = embeddings.embed_single(server_desc)

            # Embed each tool
            tool_vectors = {}
            for name, text in tool_texts.items():
                tool_vectors[name] = embeddings.embed_single(text)

            # Store in dmcp's index
            vectors = {
                "server": server_vector,
                "tools": tool_vectors,
            }
            result = await self.index_server(server_id, vectors)
            logger.info(
                f"Dispatch: Auto-indexed '{server_id}' ({len(tool_vectors)} tools): {result}"
            )

        except Exception as e:
            # Non-fatal — server is installed, just not vector-indexed
            logger.warning(
                f"Dispatch: Auto-index failed for '{server_id}' (non-fatal): {e}"
            )

    async def ensure_embedding_model(self, embeddings: Optional[Any] = None) -> None:
        """
        On startup, check the registry's embedding spec and ensure
        the correct model is available locally.
        """
        if embeddings is None:
            return

        spec = await self.embedding_spec()
        if spec is None:
            logger.info(
                "Dispatch: No embedding spec from registry (index may not be synced)"
            )
            return

        registry_model = spec.get("model", "")
        if registry_model and registry_model != embeddings.model:
            logger.warning(
                f"Dispatch: Registry embedding model '{registry_model}' differs from "
                f"local model '{embeddings.model}'. Updating local model."
            )
            embeddings.model = registry_model
            embeddings.ensure_model()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
