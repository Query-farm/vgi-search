"""Brave Search API provider -- the clean general-purpose default.

Brave operates its own independent web index (not a Google/Bing reseller), so it
is ToS-clean to query directly with a key. Docs:
https://api.search.brave.com/app/documentation/web-search/get-started

Auth: ``X-Subscription-Token`` header. Paging: ``offset`` is a *page* index
(0-based), each page holding ``count`` results, with the API capping ``count`` at
20 and ``offset`` at 9.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers._dates import parse_published
from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result

BRAVE_MAX_COUNT = 20
BRAVE_MAX_OFFSET = 9


class BraveProvider(BaseProvider):
    """Query the Brave Search API and normalize ``web.results`` to unified rows."""

    name = "brave"
    requires_key = True
    supports_answer = False
    default_base_url = "https://api.search.brave.com/res/v1"

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        key = self.require_key()
        params: dict[str, Any] = {
            "q": query,
            "count": min(max(count, 1), BRAVE_MAX_COUNT),
            "offset": min(max(offset, 0), BRAVE_MAX_OFFSET),
        }
        if "country" in opts:
            params["country"] = opts["country"]
        if "search_lang" in opts:
            params["search_lang"] = opts["search_lang"]
        resp = self.request_with_retry(
            "GET",
            f"{self.base_url}/web/search",
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
            },
        )
        payload = resp.json()
        return self._parse(payload, base_rank=offset * count)

    def _parse(self, payload: dict[str, Any], *, base_rank: int) -> list[Result]:
        web = payload.get("web") or {}
        rows = web.get("results") or []
        results: list[Result] = []
        for i, item in enumerate(rows):
            age = item.get("page_age") or item.get("age")
            results.append(
                Result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=item.get("description"),
                    rank=base_rank + i + 1,
                    source=self.name,
                    published=parse_published(age),
                    score=None,  # Brave does not expose a numeric relevance score.
                    extra=_extra(item),
                )
            )
        return results


def _extra(item: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in ("is_source_local", "language", "family_friendly", "page_age"):
        if item.get(key) is not None:
            extra[key] = item[key]
    profile = item.get("profile") or {}
    if profile.get("name"):
        extra["profile_name"] = profile["name"]
    if item.get("meta_url", {}).get("hostname"):
        extra["hostname"] = item["meta_url"]["hostname"]
    return extra
