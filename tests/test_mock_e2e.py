"""Mock-server E2E: real HTTP round-trip per provider against canned responses.

Each provider's ``base_url`` is pointed at a local :class:`MockServer` serving
fixture JSON, so ``Provider.search(...)`` exercises the full httpx path
(request building, status handling, JSON parsing) deterministically -- no keys,
no cost, no real network. Also covers the bounded-retry and clean-error paths.
"""

from __future__ import annotations

import pytest

from tests.mock_server import MockServer
from vgi_search.providers import build_provider
from vgi_search.providers.base import ProviderError
from vgi_search.providers.brave import BraveProvider


@pytest.fixture()
def server():
    with MockServer() as srv:
        yield srv


def test_brave_search_e2e(server) -> None:
    p = build_provider("brave", api_key="k", base_url=server.base)
    rows = p.search("duckdb arrow", count=10, offset=0, opts={})
    assert [r.title for r in rows] == ["DuckDB Arrow Protocol", "Arrow flight and DuckDB"]
    assert rows[0].rank == 1 and rows[0].source == "brave"


def test_tavily_search_e2e(server) -> None:
    p = build_provider("tavily", api_key="k", base_url=server.base)
    rows = p.search("who maintains duckdb", count=10, offset=0, opts={})
    assert rows[0].url == "https://duckdblabs.com"
    assert rows[0].score is not None


def test_tavily_answer_e2e(server) -> None:
    p = build_provider("tavily", api_key="k", base_url=server.base)
    answer = p.answer("who maintains duckdb", opts={})
    assert answer.startswith("DuckDB is maintained")


def test_exa_search_e2e(server) -> None:
    # exa posts to {base_url}/search; route it to the exa fixture path.
    p = build_provider("exa", api_key="k", base_url=f"{server.base}/exa")
    rows = p.search("neural search", count=10, offset=0, opts={})
    assert rows[0].source == "exa"
    assert rows[0].snippet.startswith("neural search ranks")


def test_searxng_search_e2e(server) -> None:
    p = build_provider("searxng", base_url=server.base)
    rows = p.search("duckdb", count=10, offset=0, opts={})
    assert rows[0].url == "https://duckdb.org"
    assert rows[0].source == "searxng"


def test_ddg_search_e2e(server) -> None:
    p = build_provider("ddg", base_url=server.base)
    rows = p.search("python", count=10, offset=0, opts={})
    assert rows[0].title == "Python (programming language)"


def test_ddg_answer_e2e(server) -> None:
    p = build_provider("ddg", base_url=server.base)
    assert p.answer("python", opts={}).startswith("Python is a high-level")


def test_searxng_requires_base_url() -> None:
    p = build_provider("searxng")  # no base_url
    with pytest.raises(ProviderError, match="requires a base_url"):
        p.search("x", count=10, offset=0, opts={})


def test_missing_key_raises_clean(server) -> None:
    p = build_provider("brave", base_url=server.base)  # no key, env unset
    with pytest.raises(ProviderError, match="requires an API key"):
        p.search("x", count=10, offset=0, opts={})


def test_clean_error_on_5xx(server) -> None:
    p = BraveProvider(api_key="k", base_url=f"{server.base}/boom")
    with pytest.raises(ProviderError, match="HTTP 500"):
        # /boom returns 500 (non-retry-exhausting since boom path isn't in retry
        # set handling here it returns immediately as is_error)
        p.request_with_retry("GET", f"{server.base}/boom")


def test_bounded_retry_then_success() -> None:
    # 2 x 503 then 200: the bounded retry (3 attempts) should recover.
    with MockServer(flaky=2) as srv:
        p = BraveProvider(api_key="k", base_url=srv.base)
        resp = p.request_with_retry("GET", f"{srv.base}/flaky")
        assert resp.status_code == 200


def test_retry_exhausted_raises() -> None:
    # 5 x 503 exhausts the 3 attempts -> clean ProviderError.
    with MockServer(flaky=5) as srv:
        p = BraveProvider(api_key="k", base_url=srv.base)
        with pytest.raises(ProviderError):
            p.request_with_retry("GET", f"{srv.base}/flaky")
