"""Registry / factory tests: provider construction, flags, key resolution."""

from __future__ import annotations

import pytest

from vgi_search.providers import (
    available_providers,
    build_provider,
    key_env_var,
    provider_info,
)
from vgi_search.providers.base import ProviderError


def test_v1_providers_available_by_default() -> None:
    names = available_providers()
    assert {"brave", "tavily", "exa", "ddg", "searxng"} <= set(names)
    # SERP scrapers are flagged off by default.
    assert "serpapi" not in names
    assert "serper" not in names


def test_serp_providers_gated(monkeypatch) -> None:
    monkeypatch.delenv("VGI_SEARCH_ENABLE_SERP", raising=False)
    with pytest.raises(ProviderError, match="disabled by default"):
        build_provider("serpapi")
    monkeypatch.setenv("VGI_SEARCH_ENABLE_SERP", "1")
    assert "serpapi" in available_providers()
    p = build_provider("serpapi", api_key="k")
    assert p.name == "serpapi"


def test_unknown_provider_raises() -> None:
    with pytest.raises(ProviderError, match="unknown provider"):
        build_provider("does-not-exist")


def test_key_from_env_var(monkeypatch) -> None:
    monkeypatch.setenv(key_env_var("brave"), "env-key")
    p = build_provider("brave")
    assert p.api_key == "env-key"


def test_explicit_key_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv(key_env_var("brave"), "env-key")
    p = build_provider("brave", api_key="explicit")
    assert p.api_key == "explicit"


def test_provider_info_configured_flags(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("VGI_SEARCH_SEARXNG_BASE_URL", raising=False)
    rows = {r["provider"]: r for r in provider_info()}
    assert rows["ddg"]["configured"] is True
    assert rows["brave"]["configured"] is False
    assert rows["searxng"]["configured"] is False
    # A base_url makes searxng count as configured.
    monkeypatch.setenv("VGI_SEARCH_SEARXNG_BASE_URL", "http://localhost:8080")
    rows = {r["provider"]: r for r in provider_info()}
    assert rows["searxng"]["configured"] is True


def test_provider_info_uses_passed_secret_keys() -> None:
    rows = {r["provider"]: r for r in provider_info(api_keys={"exa": "secret-key"})}
    assert rows["exa"]["configured"] is True
