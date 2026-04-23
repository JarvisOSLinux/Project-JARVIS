"""Dispatch semantic discovery and index-management helpers."""

from __future__ import annotations

import asyncio
import json
from logging import Logger
from typing import Any, Dict, List, Optional


def normalize_count(content: Any) -> Dict[str, int]:
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


async def server_count(adapter: Any, logger: Logger) -> Dict[str, Any]:
    """Get number of visible MCP servers from dispatch or dmcp."""
    if not adapter._connected:
        raw = await adapter._run_dmcp("count", "--json")
        if raw is None:
            return {"total": 0, "local": 0, "registry": 0}
        try:
            parsed = json.loads(raw)
            return normalize_count(parsed)
        except json.JSONDecodeError:
            try:
                return {"total": int(raw.strip()), "local": 0, "registry": 0}
            except ValueError:
                return {"total": 0, "local": 0, "registry": 0}

    try:
        result = await asyncio.wait_for(
            adapter.session.call_tool("server_count", {}),
            timeout=adapter.timeout,
        )
        content = adapter._extract_content(result)
        normalized = normalize_count(content)
        logger.info(f"Dispatch: Server count: {normalized}")
        return normalized
    except Exception as e:
        logger.warning(f"Dispatch: server_count failed: {e}")
        return {"total": 0, "local": 0, "registry": 0}


async def embedding_spec(adapter: Any, logger: Logger) -> Optional[Dict[str, Any]]:
    """Get the registry embedding model spec from dmcp/dispatch."""
    if not adapter._connected:
        raw = await adapter._run_dmcp("embedding-spec")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    try:
        result = await asyncio.wait_for(
            adapter.session.call_tool("embedding_spec", {}),
            timeout=adapter.timeout,
        )
        content = adapter._extract_content(result)
        if "error" in content:
            logger.warning(f"Dispatch: embedding_spec: {content['error']}")
            return None
        logger.info(f"Dispatch: Embedding spec: {content}")
        return content
    except Exception as e:
        logger.warning(f"Dispatch: embedding_spec failed: {e}")
        return None


async def sync_index(adapter: Any, logger: Logger) -> Dict[str, Any]:
    """Trigger dmcp sync-index to refresh vector index from registries."""
    if not adapter._connected:
        raw = await adapter._run_dmcp("sync-index")
        if raw is None:
            return {"error": "dmcp sync-index failed"}
        return {"output": raw.strip()}

    try:
        result = await asyncio.wait_for(
            adapter.session.call_tool("sync_index", {}),
            timeout=adapter.timeout,
        )
        content = adapter._extract_content(result)
        logger.info(f"Dispatch: sync_index result: {content}")
        return content
    except Exception as e:
        logger.warning(f"Dispatch: sync_index failed: {e}")
        return {"error": f"sync_index failed: {e}"}


async def browse_vector(
    adapter: Any,
    logger: Logger,
    vector: List[float],
    top_k: int = 5,
    min_score: float = 0.3,
) -> Dict[str, Any]:
    """Semantic search for servers/tools by one vector."""
    if not adapter._connected:
        vector_json = json.dumps(vector)
        raw = await adapter._run_dmcp(
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
            adapter.session.call_tool(
                "browse_vector",
                {
                    "vector": vector,
                    "top_k": top_k,
                    "min_score": min_score,
                },
            ),
            timeout=adapter.timeout,
        )
        return adapter._extract_content(result)
    except Exception as e:
        logger.warning(f"Dispatch: browse_vector failed: {e}")
        return {"results": []}


async def browse_vectors_batch(
    adapter: Any,
    logger: Logger,
    vectors: List[List[float]],
    top_k: int = 5,
    min_score: float = 0.3,
) -> Dict[str, Any]:
    """Semantic search for servers/tools by a batch of vectors."""
    if not adapter._connected:
        vectors_json = json.dumps(vectors)
        raw = await adapter._run_dmcp(
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
            adapter.session.call_tool(
                "browse_vectors",
                {
                    "vectors": vectors,
                    "top_k": top_k,
                    "min_score": min_score,
                },
            ),
            timeout=adapter.timeout,
        )
        return adapter._extract_content(result)
    except Exception as e:
        logger.warning(f"Dispatch: browse_vectors_batch failed: {e}")
        return {"results": [[] for _ in vectors]}


async def index_server(
    adapter: Any,
    logger: Logger,
    server_id: str,
    vectors: Dict[str, Any],
    name: str = "",
    description: str = "",
) -> Dict[str, Any]:
    """Index a non-approved server's vectors for semantic search."""
    if not adapter._connected:
        args = ["index-server", server_id, "--vectors", json.dumps(vectors)]
        if name:
            args.extend(["--name", name])
        if description:
            args.extend(["--description", description])
        raw = await adapter._run_dmcp(*args)
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
            adapter.session.call_tool("index_server", params),
            timeout=adapter.timeout,
        )
        content = adapter._extract_content(result)
        logger.info(f"Dispatch: Indexed server '{server_id}': {content}")
        return content
    except Exception as e:
        logger.warning(f"Dispatch: index_server failed: {e}")
        return {"error": f"Failed to index server: {e}"}


async def auto_index_server(
    adapter: Any,
    logger: Logger,
    server_id: str,
    embeddings: Optional[Any] = None,
) -> None:
    """Auto-index a non-approved server after installation."""
    if embeddings is None:
        logger.debug(f"Dispatch: Skipping auto-index for '{server_id}' (no embeddings)")
        return

    tools_result = await adapter.list_server_tools(server_id)
    tools = tools_result.get("tools", [])
    if not tools:
        logger.debug(f"Dispatch: No tools found for '{server_id}', skipping index")
        return

    try:
        server_desc = f"{server_id}"
        tool_texts = {}
        for tool in tools:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            params = tool.get("params", tool.get("inputSchema", {}))
            text = f"{server_id} | {name} | {desc} | params: {json.dumps(params)}"
            tool_texts[name] = text

        server_vector = embeddings.embed_single(server_desc)
        tool_vectors = {
            name: embeddings.embed_single(text) for name, text in tool_texts.items()
        }

        vectors = {
            "server": server_vector,
            "tools": tool_vectors,
        }
        result = await adapter.index_server(server_id, vectors)
        logger.info(
            f"Dispatch: Auto-indexed '{server_id}' ({len(tool_vectors)} tools): {result}"
        )
    except Exception as e:
        logger.warning(
            f"Dispatch: Auto-index failed for '{server_id}' (non-fatal): {e}"
        )


async def ensure_embedding_model(
    adapter: Any,
    logger: Logger,
    embeddings: Optional[Any] = None,
) -> None:
    """Ensure local embedding model matches registry spec."""
    if embeddings is None:
        return

    spec = await adapter.embedding_spec()
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
