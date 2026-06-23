"""Exa provider -- neural web search for AI, with content and highlights.

Exa (formerly Metaphor) is an embeddings-based search engine aimed at AI
pipelines; it returns scored results and optional text/highlights. Docs:
https://docs.exa.ai/

Auth: ``x-api-key`` header. The ``/search`` endpoint returns ``results`` with a
per-item ``score``; it has no offset param, so paging is emulated client-side.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers._dates import parse_published
from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result

EXA_MAX_RESULTS = 25


class ExaProvider(BaseProvider):
    """Query Exa's neural search; map ``results`` to unified rows."""

    name = "exa"
    requires_key = True
    supports_answer = False
    default_base_url = "https://api.exa.ai"

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        want = min(offset + count, EXA_MAX_RESULTS)
        body: dict[str, Any] = {
            "query": query,
            "numResults": min(max(want, 1), EXA_MAX_RESULTS),
            "type": opts.get("type", "auto"),
            "contents": {"text": opts.get("text", True)},
        }
        resp = self.request_with_retry(
            "POST",
            f"{self.base_url}/search",
            json_body=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.require_key(),
            },
        )
        rows = self._parse(resp.json())
        page = rows[offset : offset + count]
        for i, r in enumerate(page):
            r.rank = offset + i + 1
        return page

    def _parse(self, payload: dict[str, Any]) -> list[Result]:
        results: list[Result] = []
        for item in payload.get("results") or []:
            results.append(
                Result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=_snippet(item),
                    rank=None,
                    source=self.name,
                    published=parse_published(item.get("publishedDate")),
                    score=_as_float(item.get("score")),
                    extra=_extra(item),
                )
            )
        return results


def _snippet(item: dict[str, Any]) -> str | None:
    highlights = item.get("highlights")
    if highlights:
        return " ... ".join(str(h) for h in highlights)
    text = item.get("text")
    if isinstance(text, str) and text:
        return text[:500]
    return None


def _as_float(value: object) -> float | None:
    try:
        return None if value is None else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extra(item: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in ("author", "id"):
        if item.get(key) is not None:
            extra[key] = item[key]
    if item.get("highlights"):
        extra["highlights"] = item["highlights"]
    return extra
