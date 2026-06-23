"""Fixture parser unit tests: provider JSON -> the unified result schema.

Each provider captures a representative response shape in ``tests/fixtures`` and
this suite asserts the JSON->Result mapping, including missing-field -> NULL and
the ``extra`` JSON. No network: we call each provider's private ``_parse`` on the
canned payload directly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vgi_search.providers.brave import BraveProvider
from vgi_search.providers.ddg import DdgProvider
from vgi_search.providers.exa import ExaProvider
from vgi_search.providers.searxng import SearxngProvider
from vgi_search.providers.serpapi import SerpApiProvider
from vgi_search.providers.serper import SerperProvider
from vgi_search.providers.tavily import TavilyProvider

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# --------------------------------------------------------------------------- #
# brave
# --------------------------------------------------------------------------- #


class TestBrave:
    def test_maps_to_unified(self) -> None:
        rows = BraveProvider(api_key="x")._parse(_load("brave"), base_rank=0)
        assert len(rows) == 2
        first = rows[0]
        assert first.title == "DuckDB Arrow Protocol"
        assert first.url == "https://duckdb.org/docs/arrow"
        assert first.snippet.startswith("How DuckDB")
        assert first.rank == 1
        assert first.source == "brave"
        assert first.published == datetime(2024, 11, 2, 10, 0, tzinfo=UTC)
        assert first.score is None  # Brave has no numeric score

    def test_base_rank_offsets_rank(self) -> None:
        rows = BraveProvider(api_key="x")._parse(_load("brave"), base_rank=10)
        assert [r.rank for r in rows] == [11, 12]

    def test_missing_fields_become_null(self) -> None:
        rows = BraveProvider(api_key="x")._parse(_load("brave"), base_rank=0)
        second = rows[1]
        assert second.published is None  # no page_age in the fixture
        assert second.score is None

    def test_extra_json_roundtrips(self) -> None:
        rows = BraveProvider(api_key="x")._parse(_load("brave"), base_rank=0)
        extra = json.loads(rows[0].extra_json())
        assert extra["language"] == "en"
        assert extra["profile_name"] == "DuckDB"
        assert extra["hostname"] == "duckdb.org"


# --------------------------------------------------------------------------- #
# tavily
# --------------------------------------------------------------------------- #


class TestTavily:
    def test_maps_to_unified_with_score(self) -> None:
        rows = TavilyProvider(api_key="x")._parse(_load("tavily"))
        assert len(rows) == 2
        assert rows[0].title == "DuckDB Labs"
        assert rows[0].snippet == "DuckDB Labs is the company behind DuckDB."
        assert rows[0].score == pytest.approx(0.97231)
        assert rows[0].published == datetime(2024, 3, 15, tzinfo=UTC)

    def test_missing_published_is_null(self) -> None:
        rows = TavilyProvider(api_key="x")._parse(_load("tavily"))
        assert rows[1].published is None

    def test_extra_carries_answer_and_raw_content(self) -> None:
        rows = TavilyProvider(api_key="x")._parse(_load("tavily"))
        extra = json.loads(rows[0].extra_json())
        assert "answer" in extra
        assert extra["raw_content"].startswith("DuckDB Labs full page")


# --------------------------------------------------------------------------- #
# exa
# --------------------------------------------------------------------------- #


class TestExa:
    def test_highlights_become_snippet(self) -> None:
        rows = ExaProvider(api_key="x")._parse(_load("exa"))
        assert rows[0].snippet == "neural search ranks by meaning ... embeddings over keywords"
        assert rows[0].score == pytest.approx(0.42)
        assert rows[0].published == datetime(2024, 6, 1, tzinfo=UTC)

    def test_falls_back_to_text_snippet(self) -> None:
        rows = ExaProvider(api_key="x")._parse(_load("exa"))
        assert rows[1].snippet == "Plain text only, no highlights here."
        assert rows[1].published is None

    def test_extra_has_author_and_highlights(self) -> None:
        rows = ExaProvider(api_key="x")._parse(_load("exa"))
        extra = json.loads(rows[0].extra_json())
        assert extra["author"] == "Exa Team"
        assert extra["highlights"]


# --------------------------------------------------------------------------- #
# ddg (Instant Answer)
# --------------------------------------------------------------------------- #


class TestDdg:
    def test_abstract_plus_related_topics(self) -> None:
        rows = DdgProvider()._parse(_load("ddg"))
        # 1 abstract + 1 flat related topic + 1 nested topic = 3 (the url-less one skipped)
        assert len(rows) == 3
        assert rows[0].title == "Python (programming language)"
        assert rows[0].snippet.startswith("Python is a high-level")
        assert json.loads(rows[0].extra_json())["result_kind"] == "abstract"

    def test_related_topic_row(self) -> None:
        rows = DdgProvider()._parse(_load("ddg"))
        topic = rows[1]
        assert topic.url == "https://duckduckgo.com/Guido_van_Rossum"
        assert topic.title == "Guido van Rossum"
        assert json.loads(topic.extra_json())["result_kind"] == "related_topic"

    def test_nested_topics_flattened(self) -> None:
        rows = DdgProvider()._parse(_load("ddg"))
        urls = [r.url for r in rows]
        assert "https://duckduckgo.com/CPython" in urls

    def test_empty_payload_is_empty(self) -> None:
        assert DdgProvider()._parse({}) == []


# --------------------------------------------------------------------------- #
# searxng
# --------------------------------------------------------------------------- #


class TestSearxng:
    def test_maps_results(self) -> None:
        rows = SearxngProvider(base_url="http://x")._parse(_load("searxng"))
        assert len(rows) == 2
        assert rows[0].score == pytest.approx(3.5)
        assert rows[0].published == datetime(2023, 1, 2, 15, 4, 5, tzinfo=UTC)
        extra = json.loads(rows[0].extra_json())
        assert extra["engine"] == "google"

    def test_missing_published_null(self) -> None:
        rows = SearxngProvider(base_url="http://x")._parse(_load("searxng"))
        assert rows[1].published is None


# --------------------------------------------------------------------------- #
# serpapi / serper (flagged)
# --------------------------------------------------------------------------- #


class TestSerpProviders:
    def test_serpapi_organic_results(self) -> None:
        rows = SerpApiProvider(api_key="x")._parse(_load("serpapi"), base_rank=0)
        assert len(rows) == 2
        assert rows[0].url == "https://duckdb.org"
        assert rows[0].rank == 1
        assert rows[0].source == "serpapi"
        assert json.loads(rows[0].extra_json())["displayed_link"] == "https://duckdb.org"

    def test_serper_organic(self) -> None:
        rows = SerperProvider(api_key="x")._parse(_load("serper"), base_rank=0)
        assert len(rows) == 2
        assert rows[0].url == "https://duckdb.org"
        assert rows[0].source == "serper"
        assert json.loads(rows[1].extra_json())["sitelinks"]


# --------------------------------------------------------------------------- #
# Result.extra_json edge cases
# --------------------------------------------------------------------------- #


def test_empty_extra_is_none() -> None:
    from vgi_search.result import Result

    assert Result(title="t").extra_json() is None
