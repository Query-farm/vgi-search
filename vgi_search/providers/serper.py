"""Serper provider -- Google SERP via the serper.dev paid API (FLAGGED).

Serper (https://serper.dev/) is another paid Google-SERP API. Same framing as
SerpApi: we delegate the actual SERP fetch to a third-party service whose ToS the
*operator* accepts. OFF by default; gated behind ``VGI_SEARCH_ENABLE_SERP=1``.

Auth: ``X-API-KEY`` header. Paging: 1-based ``page`` param.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result


class SerperProvider(BaseProvider):
    """Query serper.dev's Google search; map ``organic`` to unified rows."""

    name = "serper"
    requires_key = True
    supports_answer = False
    default_base_url = "https://google.serper.dev"

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        body: dict[str, Any] = {
            "q": query,
            "num": max(count, 1),
            "page": offset + 1,
        }
        if "gl" in opts:
            body["gl"] = opts["gl"]
        if "hl" in opts:
            body["hl"] = opts["hl"]
        resp = self.request_with_retry(
            "POST",
            f"{self.base_url}/search",
            json_body=body,
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": self.require_key(),
            },
        )
        return self._parse(resp.json(), base_rank=offset * count)

    def _parse(self, payload: dict[str, Any], *, base_rank: int) -> list[Result]:
        results: list[Result] = []
        for i, item in enumerate(payload.get("organic") or []):
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
    for key in ("position", "sitelinks", "date"):
        if item.get(key) is not None:
            extra[key] = item[key]
    return extra
