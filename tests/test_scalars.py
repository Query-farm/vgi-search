"""``web_answer`` scalar tests (driven through compute against the mock server)."""

from __future__ import annotations

import pyarrow as pa
import pytest

from tests.mock_server import MockServer
from vgi_search.scalars import WebAnswer


@pytest.fixture()
def server():
    with MockServer() as srv:
        yield srv


def test_ddg_answer(server, monkeypatch) -> None:
    monkeypatch.setenv("VGI_SEARCH_DDG_BASE_URL", server.base)
    out = WebAnswer.compute(pa.array(["python"]), "ddg")
    assert out.to_pylist()[0].startswith("Python is a high-level")


def test_tavily_answer(server, monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("VGI_SEARCH_TAVILY_BASE_URL", server.base)
    out = WebAnswer.compute(pa.array(["who maintains duckdb"]), "tavily")
    assert out.to_pylist()[0].startswith("DuckDB is maintained")


def test_provider_without_answer_returns_null(server, monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    monkeypatch.setenv("VGI_SEARCH_BRAVE_BASE_URL", server.base)
    out = WebAnswer.compute(pa.array(["x", "y"]), "brave")
    assert out.to_pylist() == [None, None]


def test_unknown_provider_returns_null() -> None:
    out = WebAnswer.compute(pa.array(["x"]), "nope")
    assert out.to_pylist() == [None]


def test_null_query_row_is_null(server, monkeypatch) -> None:
    monkeypatch.setenv("VGI_SEARCH_DDG_BASE_URL", server.base)
    out = WebAnswer.compute(pa.array(["python", None]), "ddg")
    assert out.to_pylist()[1] is None
