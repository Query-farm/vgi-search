"""Provider registry and factory.

Maps a provider name to its class and constructs a configured instance, resolving
the API key and ``base_url`` from (in priority order):

1. The values passed in explicitly (the worker passes secrets resolved from the
   VGI **secret provider**, the production path).
2. Environment variables (a local-dev / test fallback -- e.g. ``BRAVE_API_KEY``,
   ``VGI_SEARCH_SEARXNG_BASE_URL``). Tests point ``base_url`` at a mock server.

The secret provider is the documented, production way to supply keys; env vars
exist so the mock-server E2E (and local hacking) can run without standing up a
secret service. Keys NEVER appear inline in example SQL.

The two SERP-scraping providers (serpapi, serper) are OFF unless
``VGI_SEARCH_ENABLE_SERP=1`` -- the operator opts into that ToS risk explicitly.
"""

from __future__ import annotations

import os
from typing import Any

from vgi_search.providers.base import BaseProvider, MissingKeyError, Provider, ProviderError
from vgi_search.providers.brave import BraveProvider
from vgi_search.providers.ddg import DdgProvider
from vgi_search.providers.exa import ExaProvider
from vgi_search.providers.searxng import SearxngProvider
from vgi_search.providers.serpapi import SerpApiProvider
from vgi_search.providers.serper import SerperProvider
from vgi_search.providers.tavily import TavilyProvider

__all__ = [
    "BaseProvider",
    "MissingKeyError",
    "Provider",
    "ProviderError",
    "available_providers",
    "build_provider",
    "key_env_var",
    "provider_info",
]

# Every provider class keyed by its stable name.
_REGISTRY: dict[str, type[BaseProvider]] = {
    cls.name: cls
    for cls in (
        BraveProvider,
        TavilyProvider,
        ExaProvider,
        DdgProvider,
        SearxngProvider,
        SerpApiProvider,
        SerperProvider,
    )
}

# The flagged SERP-scraping providers, enabled only with VGI_SEARCH_ENABLE_SERP=1.
_FLAGGED = frozenset({"serpapi", "serper"})

DEFAULT_PROVIDER = "brave"


def _serp_enabled() -> bool:
    return os.environ.get("VGI_SEARCH_ENABLE_SERP", "").lower() in ("1", "true", "yes")


def available_providers() -> list[str]:
    """Provider names available in this process (flagged ones only if enabled)."""
    return [name for name in _REGISTRY if name not in _FLAGGED or _serp_enabled()]


def key_env_var(name: str) -> str:
    """The environment variable a provider's API key falls back to."""
    return f"{name.upper()}_API_KEY"


def base_url_env_var(name: str) -> str:
    """The environment variable overriding a provider's ``base_url``."""
    return f"VGI_SEARCH_{name.upper()}_BASE_URL"


def _resolve_key(name: str, api_key: str | None) -> str | None:
    if api_key:
        return api_key
    return os.environ.get(key_env_var(name))


def _resolve_base_url(name: str, base_url: str | None) -> str | None:
    if base_url:
        return base_url
    return os.environ.get(base_url_env_var(name))


def build_provider(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client: Any = None,
    timeout: float | None = None,
) -> Provider:
    """Construct a configured provider instance by name.

    Args:
        name: Registered provider name (e.g. ``brave``, ``tavily``, ``ddg``).
        api_key: Explicit API key; falls back to the provider's env var.
        base_url: Explicit endpoint; falls back to the provider's env var.
        client: Optional pre-built HTTP client (used by tests).
        timeout: Optional per-request timeout override in seconds.

    Returns:
        A configured provider instance ready to ``search``/``answer``.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"unknown provider {name!r}; available: {', '.join(available_providers())}")
    if name in _FLAGGED and not _serp_enabled():
        raise ProviderError(
            f"provider {name!r} is a Google/Bing-SERP scraping service and is disabled by "
            f"default; set VGI_SEARCH_ENABLE_SERP=1 to opt into its ToS risk"
        )
    kwargs: dict[str, Any] = {
        "api_key": _resolve_key(name, api_key),
        "base_url": _resolve_base_url(name, base_url),
        "client": client,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return cls(**kwargs)


def provider_info(api_keys: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    """Rows for ``search_providers()``: name, key/answer flags, configured state.

    ``api_keys`` carries keys already resolved from the secret provider (keyed by
    provider name); a provider counts as configured when it needs no key, has a
    resolved secret-provider key, or has its env-var key set.
    """
    api_keys = api_keys or {}
    rows: list[dict[str, Any]] = []
    for name in available_providers():
        cls = _REGISTRY[name]
        key = _resolve_key(name, api_keys.get(name))
        if name == "searxng":
            # SearXNG needs a base_url, not a key, to be usable.
            configured = bool(_resolve_base_url(name, None))
        elif not cls.requires_key:
            configured = True
        else:
            configured = bool(key)
        rows.append(
            {
                "provider": name,
                "requires_key": cls.requires_key,
                "supports_answer": cls.supports_answer,
                "configured": configured,
            }
        )
    return rows
