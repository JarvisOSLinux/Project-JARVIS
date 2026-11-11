"""The registry's embedding model against JARVIS's own (#291 review).

The registry names its vectors' model as Ollama did ("nomic-embed-text");
JARVIS runs the GGUF of the same model. That must count as a match, with no
warning on every start, and only a real difference may switch the model.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jarvis.dispatch import discovery

LOCAL = "nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0"


def _adapter(model):
    async def embedding_spec():
        return {"model": model, "dimensions": 768}

    return SimpleNamespace(embedding_spec=embedding_spec)


@pytest.mark.asyncio
async def test_same_model_under_its_registry_name_changes_nothing(caplog):
    embeddings = SimpleNamespace(model=LOCAL, use_model=MagicMock())
    with caplog.at_level(logging.WARNING):
        await discovery.ensure_embedding_model(
            _adapter("nomic-embed-text"), logging.getLogger("t"), embeddings
        )
    embeddings.use_model.assert_not_called()
    assert embeddings.model == LOCAL
    assert not caplog.records


@pytest.mark.asyncio
async def test_a_known_different_model_is_switched_to(caplog):
    embeddings = SimpleNamespace(
        model="BAAI/bge-small-en-v1.5-gguf", use_model=MagicMock()
    )
    await discovery.ensure_embedding_model(
        _adapter("nomic-embed-text"), logging.getLogger("t"), embeddings
    )
    embeddings.use_model.assert_called_once_with(LOCAL)


@pytest.mark.asyncio
async def test_an_unknown_registry_model_warns_and_keeps_the_local_one(caplog):
    embeddings = SimpleNamespace(model=LOCAL, use_model=MagicMock())
    with caplog.at_level(logging.WARNING):
        await discovery.ensure_embedding_model(
            _adapter("mxbai-embed-large"), logging.getLogger("t"), embeddings
        )
    embeddings.use_model.assert_not_called()
    assert "rank poorly" in caplog.text
