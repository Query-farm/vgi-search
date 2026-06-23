"""DuckDuckGo **Instant Answer** provider -- free, no key, zero-click answers.

IMPORTANT: this is the DuckDuckGo *Instant Answer* API
(``https://api.duckduckgo.com/?q=...&format=json``), NOT a web SERP. It returns
DuckDuckGo's curated "zero-click" box -- a definition, an abstract, a disambiguation
list, or related topics -- for queries that have one. **Most ordinary queries
return empty.** It is a free quick-fact path, not a general web-search backend,
and it is the one provider with a synthesized ``answer`` that needs no key.

We do NOT scrape the DuckDuckGo HTML SERP -- that is fragile and against the ToS.
This is the official, documented, ToS-clean Instant Answer endpoint.
"""

from __future__ import annotations

from typing import Any

from vgi_search.providers.base import BaseProvider
from vgi_search.result import Result


class DdgProvider(BaseProvider):
    """Query the DuckDuckGo Instant Answer API (no key)."""

    name = "ddg"
    requires_key = False
    supports_answer = True
    default_base_url = "https://api.duckduckgo.com"

    def _fetch(self, query: str) -> dict[str, Any]:
        resp = self.request_with_retry(
            "GET",
            f"{self.base_url}/",
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "no_redirect": 1,
                "skip_disambig": 0,
            },
            headers={"Accept": "application/json"},
        )
        # The IA endpoint serves application/x-javascript; parse leniently.
        return resp.json()

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        rows = self._parse(self._fetch(query))
        page = rows[offset : offset + count]
        for i, r in enumerate(page):
            r.rank = offset + i + 1
        return page

    def answer(self, query: str, *, opts: dict[str, Any]) -> str | None:
        payload = self._fetch(query)
        return payload.get("AbstractText") or payload.get("Answer") or None

    def _parse(self, payload: dict[str, Any]) -> list[Result]:
        results: list[Result] = []

        # 1) The primary Abstract (the "zero-click" box), when present.
        abstract = payload.get("AbstractText")
        if abstract:
            results.append(
                Result(
                    title=payload.get("Heading") or None,
                    url=payload.get("AbstractURL") or None,
                    snippet=abstract,
                    source=self.name,
                    extra=_abstract_extra(payload),
                )
            )

        # 2) RelatedTopics (flattening one level of topic groups).
        for topic in payload.get("RelatedTopics") or []:
            if "Topics" in topic:
                for sub in topic.get("Topics") or []:
                    row = _topic_row(sub, self.name)
                    if row is not None:
                        results.append(row)
            else:
                row = _topic_row(topic, self.name)
                if row is not None:
                    results.append(row)
        return results


def _abstract_extra(payload: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {"result_kind": "abstract"}
    for key in ("AbstractSource", "Answer", "AnswerType", "Type"):
        if payload.get(key):
            extra[key] = payload[key]
    return extra


def _topic_row(topic: dict[str, Any], source: str) -> Result | None:
    text = topic.get("Text")
    url = topic.get("FirstURL")
    if not text or not url:
        return None
    return Result(
        title=text.split(" - ")[0] if " - " in text else text,
        url=url,
        snippet=text,
        source=source,
        extra={"result_kind": "related_topic"},
    )
