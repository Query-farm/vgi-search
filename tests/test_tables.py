"""Table-function tests driven through the real bind/init/process lifecycle.

``web_search`` is exercised against the mock server (unified columns, NULLs,
extra JSON) and -- the headline test -- the pagination scan state is round-tripped
through its Arrow serialization on every tick, proving the cursor survives batch
boundaries. ``search_providers`` is exercised standalone.
"""

from __future__ import annotations

import json

import pytest

from tests.harness import run_table_function
from tests.mock_server import MockServer
from vgi_search.schema_utils import TIMESTAMPTZ
from vgi_search.tables import CHUNK_ROWS, SearchProviders, WebSearch


@pytest.fixture()
def server():
    with MockServer() as srv:
        yield srv


def _brave_env(monkeypatch, base_url: str) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setenv("VGI_SEARCH_BRAVE_BASE_URL", base_url)
    monkeypatch.setenv("VGI_SEARCH_DEFAULT_PROVIDER", "brave")


def test_web_search_unified_columns(server, monkeypatch) -> None:
    _brave_env(monkeypatch, server.base)
    table = run_table_function(WebSearch, positional=("duckdb arrow",), named={"provider": "brave"})

    assert table.column_names == [
        "title",
        "url",
        "snippet",
        "rank",
        "source",
        "published",
        "score",
        "extra",
    ]
    assert table.schema.field("published").type == TIMESTAMPTZ
    assert table.num_rows == 2
    assert table.column("title").to_pylist()[0] == "DuckDB Arrow Protocol"
    assert table.column("rank").to_pylist() == [1, 2]
    assert table.column("source").to_pylist() == ["brave", "brave"]
    # Missing fields -> NULL.
    assert table.column("score").to_pylist() == [None, None]
    assert table.column("published").to_pylist()[1] is None
    # extra is JSON or NULL.
    extra0 = json.loads(table.column("extra").to_pylist()[0])
    assert extra0["profile_name"] == "DuckDB"


def test_web_search_default_provider(server, monkeypatch) -> None:
    _brave_env(monkeypatch, server.base)
    # No provider arg -> falls back to VGI_SEARCH_DEFAULT_PROVIDER (brave).
    table = run_table_function(WebSearch, positional=("duckdb",))
    assert table.column("source").to_pylist()[0] == "brave"


def test_scan_state_roundtrips_across_batch_boundary(server, monkeypatch) -> None:
    """The headline: a 10-result page streamed in 5-row chunks, with the scan
    state serialized/deserialized between every tick, still yields the full,
    correctly-ranked page exactly once.
    """
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    # Point brave at the 12-result paging route.
    monkeypatch.setenv("VGI_SEARCH_BRAVE_BASE_URL", f"{server.base}/paging")

    # count=10 -> page spans ceil(10/5) = 2+ emit chunks -> at least one batch
    # boundary that the serialized cursor must survive.
    table = run_table_function(
        WebSearch,
        positional=("anything",),
        named={"provider": "brave", "count": 10},
        serialize_state=True,
    )

    assert CHUNK_ROWS < 10  # precondition: the page really does span chunks
    assert table.num_rows == 10
    # Ranks are 1..10, contiguous and unique -> no chunk dropped or duplicated.
    assert table.column("rank").to_pylist() == list(range(1, 11))
    titles = table.column("url").to_pylist()
    assert len(set(titles)) == 10


def test_scan_state_offset_page(server, monkeypatch) -> None:
    """offset selects a later page; rank reflects the global position."""
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setenv("VGI_SEARCH_BRAVE_BASE_URL", f"{server.base}/paging")
    table = run_table_function(
        WebSearch,
        positional=("anything",),
        named={"provider": "brave", "count": 5, "page": 1},
        serialize_state=True,
    )
    # page=1, count=5 -> brave page index 1, base_rank = page*count = 5.
    assert table.column("rank").to_pylist() == [6, 7, 8, 9, 10]


def test_web_search_provider_error_is_clean(server, monkeypatch) -> None:
    """A keyless keyed provider surfaces a clean error, not a worker crash."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("VGI_SEARCH_BRAVE_BASE_URL", server.base)
    from vgi_search.providers.base import ProviderError

    with pytest.raises(ProviderError, match="requires an API key"):
        run_table_function(WebSearch, positional=("x",), named={"provider": "brave"})


def test_search_providers_lists_configured(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    table = run_table_function(SearchProviders)
    assert table.column_names == ["provider", "requires_key", "supports_answer", "configured"]
    rows = {r["provider"]: r for r in table.to_pylist()}
    assert rows["ddg"]["configured"] is True  # no key needed
    assert rows["ddg"]["supports_answer"] is True
    assert rows["tavily"]["configured"] is True  # key set above
    assert rows["brave"]["configured"] is False  # key unset
