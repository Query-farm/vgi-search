# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.5",
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

import json

from vgi import Worker
from vgi.catalog import Catalog, Schema, Table

from vgi_search.scalars import SCALAR_FUNCTIONS
from vgi_search.tables import PROVIDERS_TABLE_TAGS, TABLE_FUNCTIONS, SearchProviders

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
    "Web-search functions over a pluggable provider surface.\n\n"
    "- `web_search(query, provider := ..., count := ..., page := ...)` -- table function returning "
    "ranked results in a unified schema (title, url, snippet, rank, source, published, score, "
    "extra JSON).\n"
    "- `web_answer(query, provider)` -- scalar returning a synthesized one-line answer, or NULL.\n"
    "- `search_providers()` -- table function listing the providers and which are configured.\n\n"
    "Backed by Brave, Tavily, Exa, SearXNG, DuckDuckGo, and the opt-in SerpApi/Serper scrapers. "
    "Provider keys come from the VGI secret provider, never from SQL; `ddg` works for free. Use "
    "these for RAG / retrieval that needs live web results."
)

_SCHEMA_DESCRIPTION_MD = (
    "# search.main\n\n"
    "Web-search functions over a **pluggable provider surface**, for RAG / retrieval.\n\n"
    "- `web_search` -- ranked results as a table (unified schema across providers).\n"
    "- `web_answer` -- a synthesized one-line answer (scalar), or NULL.\n"
    "- `search_providers` -- provider discovery (names, capabilities, configured state).\n\n"
    "Providers: Brave, Tavily, Exa, SearXNG, DuckDuckGo (free), plus opt-in SerpApi/Serper. "
    "Keys are supplied via the VGI secret provider, never inline in SQL."
)

_SCHEMA_EXAMPLE_QUERIES = (
    "SELECT * FROM search.main.search_providers() ORDER BY provider;\n"
    "SELECT title, url, rank FROM "
    "search.main.web_search('python programming language', provider := 'ddg', count := 5) "
    "ORDER BY rank;\n"
    "SELECT title, url, snippet FROM "
    "search.main.web_search('vector database', provider := 'brave', count := 10);\n"
    "SELECT search.main.web_answer('python programming language', 'ddg') AS answer;"
)

_SEARCH_CATALOG = Catalog(
    name="search",
    default_schema="main",
    comment="Unified web search over pluggable providers for SQL / RAG.",
    source_url="https://github.com/Query-farm/vgi-search",
    tags={
        "vgi.title": "Unified Web Search",
        "vgi.keywords": json.dumps(
            [
                "web search",
                "search",
                "retrieval",
                "rag",
                "serp",
                "results",
                "brave",
                "tavily",
                "exa",
                "searxng",
                "duckduckgo",
                "ddg",
                "serpapi",
                "serper",
                "web answer",
                "providers",
            ]
        ),
        "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
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
                "vgi.title": "Web Search — main",
                "vgi.keywords": json.dumps(
                    [
                        "web search",
                        "web_search",
                        "web_answer",
                        "search_providers",
                        "retrieval",
                        "rag",
                        "serp",
                        "results",
                        "providers",
                        "brave",
                        "tavily",
                        "exa",
                        "searxng",
                        "duckduckgo",
                        "ddg",
                    ]
                ),
                # VGI123 classifying tags (BARE keys: domain/category/topic) for faceting.
                "domain": "information-retrieval",
                "category": "web-search",
                "topic": "search-providers",
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
            },
            functions=[*SCALAR_FUNCTIONS, *TABLE_FUNCTIONS],
            # `search_providers()` is parameterless and always returns the same
            # provider directory, so also expose it as a regular table backed by
            # the same generator (VGI311): `SELECT * FROM search.main.search_providers`.
            tables=[
                Table(
                    name="search_providers",
                    function=SearchProviders,
                    comment="Directory of available search providers and whether each is configured",
                    # Every provider row is fully populated, and the provider name
                    # uniquely identifies a row (VGI806/VGI807 constraints).
                    primary_key=(("provider",),),
                    not_null=("provider", "requires_key", "supports_answer", "configured"),
                    tags=PROVIDERS_TABLE_TAGS,
                ),
            ],
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
