"""SearXNG provider -- query a SELF-HOSTED SearXNG metasearch instance.

SearXNG (https://docs.searxng.org/) is open-source metasearch you run yourself.
Because there is no public/default endpoint, ``base_url`` is REQUIRED -- you point
this provider at your own instance. ToS responsibility shifts to the operator of
that instance (you), since it aggregates upstream engines on your behalf.

Auth: none by default (your instance may sit behind its own auth/proxy). Paging:
SearXNG exposes a native 1-based ``pageno`` param, so offset paging maps cleanly.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers._dates import parse_published
from vgi_search.providers.base import BaseProvider, ProviderError
from vgi_search.result import Result


class SearxngProvider(BaseProvider):
    """Query a self-hosted SearXNG instance's JSON API."""

    name = "searxng"
    requires_key = False
    supports_answer = False
    default_base_url = ""  # No default: the operator must supply base_url.

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        if not self.base_url:
            raise ProviderError(
                "provider 'searxng' requires a base_url pointing at your self-hosted "
                "instance (set it via the ATTACH 'searxng_base_url' option or the "
                "VGI_SEARCH_SEARXNG_BASE_URL env var)"
            )
        # SearXNG returns a fixed page of results; map offset/count onto pageno.
        page_no = (offset // count) + 1 if count else 1
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": page_no,
        }
        if "categories" in opts:
            params["categories"] = opts["categories"]
        if "engines" in opts:
            params["engines"] = opts["engines"]
        if "language" in opts:
            params["language"] = opts["language"]
        resp = self.request_with_retry(
            "GET",
            f"{self.base_url}/search",
            params=params,
            headers={"Accept": "application/json"},
        )
        rows = self._parse(resp.json())
        page = rows[:count]
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
                    snippet=item.get("content"),
                    rank=None,
                    source=self.name,
                    published=parse_published(item.get("publishedDate")),
                    score=_as_float(item.get("score")),
                    extra=_extra(item),
                )
            )
        return results


def _as_float(value: object) -> float | None:
    try:
        return None if value is None else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extra(item: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if item.get("engine"):
        extra["engine"] = item["engine"]
    if item.get("engines"):
        extra["engines"] = item["engines"]
    if item.get("category"):
        extra["category"] = item["category"]
    return extra
