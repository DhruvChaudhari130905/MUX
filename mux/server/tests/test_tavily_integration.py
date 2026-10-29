"""Tests for Tavily search with per-room cache (mux/integrations/tavily.py)."""

import pytest

from mux.integrations.tavily import SearchResult, Source, TavilySearch, _shorten


# ---------------------------------------------------------------------------
# _shorten
# ---------------------------------------------------------------------------

def test_shorten_under_limit_unchanged():
    assert _shorten("hi", 300) == "hi"


def test_shorten_over_limit_truncates():
    long = "a" * 400
    out = _shorten(long, 300)
    assert len(out) == 300
    assert out.endswith("…")


def test_shorten_collapses_whitespace():
    assert _shorten("a   b\n\nc", 100) == "a b c"


# ---------------------------------------------------------------------------
# TavilySearch: per-room cache
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self):
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append(query)
        return {
            "answer": f"answer for {query}",
            "results": [
                {"title": "T", "url": "https://t.dev", "content": "content for " + query},
            ],
        }


@pytest.mark.asyncio
async def test_search_caches_per_room():
    client = FakeClient()
    ts = TavilySearch(client=client, max_results=2, snippet_chars=50)

    r1 = await ts.search("rsvp login spam")
    r2 = await ts.search("rsvp login spam")  # cached -> no second call
    assert r1.answer == "answer for rsvp login spam"
    assert r2.answer == r1.answer
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_search_different_queries_hit_api_twice():
    client = FakeClient()
    ts = TavilySearch(client=client)

    await ts.search("query one")
    await ts.search("query two")
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_search_normalizes_whitespace_key():
    client = FakeClient()
    ts = TavilySearch(client=client)
    await ts.search("  rsvp   login   spam  ")
    await ts.search("rsvp login spam")
    assert len(client.calls) == 1  # normalized to same key


@pytest.mark.asyncio
async def test_search_result_shapes():
    client = FakeClient()
    ts = TavilySearch(client=client, max_results=3, snippet_chars=10)
    r = await ts.search("q")
    assert r.query == "q"
    assert r.answer == "answer for q"
    assert len(r.sources) == 1
    assert r.sources[0].title == "T"
    assert r.sources[0].url == "https://t.dev"
    expected = "content for q"[: 10 - 1].rstrip() + "…"
    assert r.sources[0].snippet == expected


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

class FailingClient:
    async def search(self, query, **kwargs):
        raise RuntimeError("Tavily down")


@pytest.mark.asyncio
async def test_search_error_propagates():
    ts = TavilySearch(client=FailingClient())
    with pytest.raises(RuntimeError, match="Tavily down"):
        await ts.search("q")
