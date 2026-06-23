# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python",
#     "httpx>=0.27",
# ]
#
# [tool.uv.sources]
# vgi-python = { path = "../vgi-python" }
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

_SEARCH_CATALOG = Catalog(
    name="search",
    default_schema="main",
    schemas=[
        Schema(
            name="main",
            comment="Unified web search over pluggable providers for SQL / RAG",
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
