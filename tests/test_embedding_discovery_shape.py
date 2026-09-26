"""Regression tests for #240 — semantic discovery silently downgrading to keyword.

The bug was not that discovery failed. Discovery worked: dispatch returned five
hits with the best at 0.74, and JARVIS answered the question. The bug was that
the embedding results were discarded on the way back and an unranked keyword
match answered instead, while the log read "embedding returned nothing".

That is why these tests assert on the *mechanism*, not the outcome. Every test
that only checks "discovery found a server" passed throughout, because the
fallback kept succeeding — so a test of the outcome is exactly the test that
cannot catch this class of bug.
"""

import json

import pytest

from jarvis.dispatch.discovery import _as_results

HITS = [
    {"server_id": "com.example.mcp.weather", "server_name": "Weather", "score": 0.7398},
    {"server_id": "com.example.mcp.caldav", "server_name": "CalDAV", "score": 0.48},
]


class _Block:
    def __init__(self, text):
        self.text = text


class _CallToolResult:
    """The shape the MCP SDK hands back for dispatch's browse_servers.

    Deliberately has no `structuredContent` attribute: the SDK field is
    `structured_content`, and dispatch's non-standard top-level `results` key is
    dropped by the SDK because it is not part of the tool-result schema. Both of
    those are what the real object does, and both were load-bearing in #240.
    """

    def __init__(self, text, structured=None):
        self.content = [_Block(text)]
        if structured is not None:
            self.structured_content = structured


def _extract(result):
    from jarvis.dispatch.adapter import DispatchAdapter

    return DispatchAdapter._extract_content(None, result)


# --- the shape normaliser -------------------------------------------------


def test_bare_list_is_read_as_results():
    """The CLI path: `dmcp browse --vector --json` prints a bare array.

    Previously this reached `.get("results", [])` as a list and raised
    AttributeError, which a blanket except turned into "no matches".
    """
    assert _as_results(HITS, [])["results"] == HITS


def test_output_wrapped_list_is_read_as_results():
    """The MCP path: `_extract_content` wraps a non-dict payload as `output`."""
    assert _as_results({"output": HITS}, [])["results"] == HITS


def test_results_key_is_passed_through():
    assert _as_results({"results": HITS}, [])["results"] == HITS


def test_unrecognised_payload_yields_the_empty_shape():
    for payload in ({}, {"results": "not a list"}, None, 42, "text"):
        assert _as_results(payload, [])["results"] == []


def test_batch_empty_shape_is_per_vector():
    empty = [[], [], []]
    assert _as_results({}, empty)["results"] == empty


def test_transport_error_is_not_flattened_into_no_matches():
    """An error and an empty result must stay distinguishable.

    Conflating them is precisely how a broken primary path reads as "nothing
    matched" rather than as a fault.
    """
    out = _as_results({"error": "connection refused"}, [])
    assert out["results"] == []
    assert out["error"] == "connection refused"


# --- _extract_content -----------------------------------------------------


def test_structured_content_is_read_under_its_real_attribute_name():
    """The SDK field is snake_case.

    `hasattr(result, "structuredContent")` is always False on a real
    CallToolResult, so testing only that spelling made the branch dead code.
    """
    result = _CallToolResult(text="ignored", structured={"results": HITS})
    assert _extract(result) == {"results": HITS}


def test_a_json_array_body_survives_extraction_and_normalisation():
    """The end-to-end shape path that was broken.

    dispatch puts the array in content[0].text; extraction wraps it as `output`;
    the normaliser has to recover it. Asserting the hits come back — not merely
    that something did — is the point.
    """
    result = _CallToolResult(text=json.dumps(HITS))
    extracted = _extract(result)
    assert _as_results(extracted, [])["results"] == HITS


def test_a_json_object_body_is_still_returned_as_a_dict():
    """Unrelated tools return objects; extraction must not regress for them."""
    result = _CallToolResult(text=json.dumps({"output": "Synced: 33 vectors"}))
    assert _extract(result) == {"output": "Synced: 33 vectors"}


# --- the mode must reach the planner --------------------------------------


def test_keyword_results_are_labelled_as_unranked_for_the_model():
    """A downgrade has to be visible to the planner, not just in the log.

    Keyword matching scores every entry 0.0, so the ordering carries no signal;
    a model told to pick "the best fit" needs to know the list is not sorted by
    fit.
    """
    from jarvis.runtime.root_context import format_search_results

    rows = [dict(h, score=0.0) for h in HITS]
    keyword = format_search_results("weather", rows, "keyword")
    assert "NOT semantic similarity" in keyword
    assert "unranked" in keyword.lower()

    semantic = format_search_results("weather", HITS, "embedding")
    assert "NOT semantic similarity" not in semantic


@pytest.mark.parametrize("mode", ["embedding", "keyword"])
def test_search_results_always_name_the_servers_found(mode):
    from jarvis.runtime.root_context import format_search_results

    rendered = format_search_results("weather", HITS, mode)
    for hit in HITS:
        assert hit["server_id"] in rendered
