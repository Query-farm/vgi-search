# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.4",
#     "httpx>=0.27",
# ]
# ///
"""VGI worker exposing unified web search to DuckDB/SQL.

Assembles the functions in ``vgi_search`` into a single ``search`` catalog and
runs the worker over stdio (a DuckDB subprocess) or HTTP (via serve.py).

Search is an **egress connector**: queries leave the engine for a third-party
search API. It is commodity access -- a thin wrapper whose value lives in the
upstream subscription, not the worker -- so it is built as AI-stack glue, framed
honestly (see README.md). The durable value is the pluggable-provider surface.

Usage:
    uv run search_worker.py              # serve over stdio (DuckDB subprocess)
    python serve.py --port 8000          # serve over HTTP

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'search' (TYPE vgi, LOCATION 'uv run search_worker.py');

    SELECT title, url, snippet
      FROM search.web_search('duckdb arrow protocol', provider := 'brave', count := 10);
    SELECT search.web_answer('who maintains duckdb', 'tavily');
    SELECT * FROM search.search_providers();

Provider API keys are supplied via the VGI **secret provider** (one secret type
per keyed provider: brave/tavily/exa/serpapi/serper); for local dev / CI they may
fall back to the ``<PROVIDER>_API_KEY`` env vars. Keys are NEVER passed in SQL.
"""

from __future__ import annotations

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_search.scalars import SCALAR_FUNCTIONS
from vgi_search.tables import TABLE_FUNCTIONS

_CATALOG_DESCRIPTION_LLM = (
    "Run web searches from SQL through one pluggable provider surface (Brave, Tavily, Exa, "
    "SearXNG, DuckDuckGo, and the opt-in SerpApi/Serper SERP scrapers). Use it to retrieve live "
    "web results for RAG/retrieval: web_search(query, provider := ..., count := ..., page := ...) "
    "returns a unified row shape (title, url, snippet, rank, source, published, score, extra JSON) "
    "with provider-page pagination; web_answer(query, provider) returns a single synthesized "
    "one-line answer (Tavily or free DuckDuckGo Instant Answer) or NULL; search_providers() lists "
    "providers and which are configured. Provider API keys come from the VGI secret provider, "
    "never from SQL. This is an egress connector -- queries leave the engine for a third-party "
    "search API -- so results depend on the upstream subscription."
)

_CATALOG_DESCRIPTION_MD = (
    "# search\n\n"
    "Unified **web search** for DuckDB/SQL behind one pluggable provider surface, for RAG / "
    "retrieval.\n\n"
    "- **Table function** `web_search(query, provider := ..., count := ..., page := ...)` -- one "
    "search against the chosen provider, streamed as the unified result schema with page-based "
    "pagination.\n"
    "- **Table function** `search_providers()` -- list providers and whether each has a "
    "key/base_url configured.\n"
    "- **Scalar** `web_answer(query, provider)` -- a synthesized one-line answer (Tavily or free "
    "DuckDuckGo Instant Answer), or NULL when unavailable.\n\n"
    "Providers: Brave, Tavily, Exa, SearXNG, DuckDuckGo (free), plus opt-in SerpApi/Serper. "
    "Keys are supplied via the VGI secret provider, never inline in SQL."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Web-search functions: web_search (table function returning ranked results), web_answer "
    "(scalar synthesized answer), and search_providers (table function listing configured "
    "providers). Backed by pluggable providers (Brave, Tavily, Exa, SearXNG, DuckDuckGo, "
    "opt-in SerpApi/Serper)."
)

_SCHEMA_DESCRIPTION_MD = (
    "Web-search functions over a pluggable provider surface: `web_search` (ranked results), "
    "`web_answer` (synthesized answer), `search_providers` (provider discovery)."
)

_SEARCH_CATALOG = Catalog(
    name="search",
    default_schema="main",
    comment="Unified web search over pluggable providers for SQL / RAG.",
    source_url="https://github.com/Query-farm/vgi-search",
    tags={
        "vgi.description_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.description_md": _CATALOG_DESCRIPTION_MD,
        "vgi.author": "Query.Farm",
        "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-search/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-search/blob/main/README.md",
    },
    schemas=[
        Schema(
            name="main",
            comment="Unified web search over pluggable providers for SQL / RAG",
            tags={
                "vgi.description_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.description_md": _SCHEMA_DESCRIPTION_MD,
            },
            functions=[*SCALAR_FUNCTIONS, *TABLE_FUNCTIONS],
        ),
    ],
)


class SearchWorker(Worker):
    """Worker process hosting the ``search`` catalog."""

    catalog = _SEARCH_CATALOG


def main() -> None:
    """Run the search worker process (stdio or, via flags, HTTP)."""
    SearchWorker.main()


if __name__ == "__main__":
    main()
