"""Gated live smoke test against the real DuckDuckGo Instant Answer API.

DDG Instant Answer is free and needs no key, so a single live test is acceptable
-- but it hits the real network, so it is marked ``live`` and excluded from the CI
gate (``pytest -m "not live"``). Run it explicitly with ``make test-live`` /
``pytest -m live``. It self-skips cleanly if the network is unavailable.

The real brave / tavily / exa providers need paid keys and are therefore covered
only by the fixture + mock-server suites, never by a live CI test.
"""

from __future__ import annotations

import httpx
import pytest

from vgi_search.providers import build_provider
from vgi_search.providers.base import ProviderError

pytestmark = pytest.mark.live


def test_ddg_instant_answer_live() -> None:
    p = build_provider("ddg")  # real endpoint, no key
    try:
        rows = p.search("DuckDB", count=5, offset=0, opts={})
    except (ProviderError, httpx.HTTPError) as exc:
        pytest.skip(f"DDG live endpoint unavailable: {exc}")
    # DDG has an Instant Answer for 'DuckDB'; assert the shape, not exact text.
    assert isinstance(rows, list)
    for r in rows:
        assert r.source == "ddg"
        assert r.url is None or r.url.startswith("http")
