"""Tavily provider -- LLM/RAG-optimized search with an optional synthesized answer.

Tavily is built for AI retrieval: a POST JSON API that returns scored results
plus, optionally, an ``answer`` string (which powers ``web_answer``). Docs:
https://docs.tavily.com/

Auth: API key in the JSON body (``api_key``). Tavily has no offset/page param,
so paging is emulated client-side by over-fetching and slicing (documented as
the trivial, non-defensible kind of scan state).
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers._dates import parse_published
from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result

TAVILY_MAX_RESULTS = 20


class TavilyProvider(BaseProvider):
    """Query Tavily's search endpoint; map ``results`` to unified rows."""

    name = "tavily"
    requires_key = True
    supports_answer = True
    default_base_url = "https://api.tavily.com"

    def _body(self, query: str, *, want: int, include_answer: bool, opts: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "api_key": self.require_key(),
            "query": query,
            "max_results": min(max(want, 1), TAVILY_MAX_RESULTS),
            "include_answer": include_answer,
            "search_depth": opts.get("search_depth", "basic"),
        }
        if "topic" in opts:
            body["topic"] = opts["topic"]
        return body

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        # Tavily has no native offset; over-fetch and slice for client-side paging.
        want = min(offset + count, TAVILY_MAX_RESULTS)
        resp = self.request_with_retry(
            "POST",
            f"{self.base_url}/search",
            json_body=self._body(query, want=want, include_answer=False, opts=opts),
            headers={"Content-Type": "application/json"},
        )
        rows = self._parse(resp.json())
        page = rows[offset : offset + count]
        for i, r in enumerate(page):
            r.rank = offset + i + 1
        return page

    def answer(self, query: str, *, opts: dict[str, Any]) -> str | None:
        resp = self.request_with_retry(
            "POST",
            f"{self.base_url}/search",
            json_body=self._body(query, want=5, include_answer=True, opts=opts),
            headers={"Content-Type": "application/json"},
        )
        answer = resp.json().get("answer")
        return answer or None

    def _parse(self, payload: dict[str, Any]) -> list[Result]:
        results: list[Result] = []
        for item in payload.get("results") or []:
            results.append(
                Result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=item.get("content"),
                    rank=None,  # assigned after slicing
                    source=self.name,
                    published=parse_published(item.get("published_date")),
                    score=_as_float(item.get("score")),
                    extra=_extra(item, payload),
                )
            )
        return results


def _as_float(value: object) -> float | None:
    try:
        return None if value is None else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extra(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if item.get("raw_content"):
        extra["raw_content"] = item["raw_content"]
    if payload.get("answer"):
        extra["answer"] = payload["answer"]
    return extra
