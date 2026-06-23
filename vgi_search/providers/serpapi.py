"""SerpApi provider -- Google/Bing SERP via a paid scraping service (FLAGGED).

SerpApi (https://serpapi.com/) is a paid service that legally(*) fetches and
parses Google's (and others') SERP for you. We do NOT scrape Google ourselves --
this delegates that to a service whose business is exactly that, and whose own
ToS the *operator* accepts by enabling this provider.

(*) The legality/ToS of SERP scraping is contested; that risk is the operator's,
which is why this provider is OFF by default and gated behind
``VGI_SEARCH_ENABLE_SERP=1``. Auth: ``api_key`` query param.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result


class SerpApiProvider(BaseProvider):
    """Query SerpApi's Google engine; map ``organic_results`` to unified rows."""

    name = "serpapi"
    requires_key = True
    supports_answer = False
    default_base_url = "https://serpapi.com"

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        """Scrape SERP results via SerpAPI, mapped to the unified schema."""
        params: dict[str, Any] = {
            "engine": opts.get("engine", "google"),
            "q": query,
            "num": max(count, 1),
            "start": offset * count,
            "api_key": self.require_key(),
        }
        resp = self.request_with_retry(
            "GET",
            f"{self.base_url}/search.json",
            params=params,
            headers={"Accept": "application/json"},
        )
        return self._parse(resp.json(), base_rank=offset * count)

    def _parse(self, payload: dict[str, Any], *, base_rank: int) -> list[Result]:
        results: list[Result] = []
        for i, item in enumerate(payload.get("organic_results") or []):
            results.append(
                Result(
                    title=item.get("title"),
                    url=item.get("link"),
                    snippet=item.get("snippet"),
                    rank=base_rank + i + 1,
                    source=self.name,
                    published=None,
                    score=None,
                    extra=_extra(item),
                )
            )
        return results


def _extra(item: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in ("displayed_link", "source", "position"):
        if item.get(key) is not None:
            extra[key] = item[key]
    return extra
