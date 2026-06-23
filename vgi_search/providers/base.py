"""The pluggable provider surface.

A provider is a thin adapter that turns ``(query, count, offset, opts)`` into a
list of unified :class:`~vgi_search.result.Result` rows by calling a third-party
search API over HTTP. The abstraction is the durable value of this worker: a
single SQL surface (``web_search``) over many interchangeable backends, so no
one API dying takes the feature with it (cf. Bing Web Search API, retired 2025).

Network-worker discipline lives here, applied uniformly to every provider:

* **Per-call timeout** (:data:`DEFAULT_TIMEOUT`, overridable per provider).
* **Bounded retry with backoff** on 429 / 5xx (:func:`request_with_retry`).
* **Never crash the worker**: a provider that fails raises
  :class:`ProviderError`, which the table function turns into a clean DuckDB
  error (or, for transient/empty cases, an empty result) -- it never takes the
  process down.

``base_url`` is configurable per instance so tests can point a provider at a
local mock HTTP server (deterministic, no keys, no cost).
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

import httpx

from vgi_search.result import Result

DEFAULT_TIMEOUT = 15.0
"""Per-call HTTP timeout in seconds (connect + read)."""

MAX_RETRIES = 3
"""Total attempts for a single provider call (1 try + up to 2 retries)."""

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
"""HTTP statuses that warrant a bounded retry with backoff."""

BACKOFF_BASE = 0.25
"""Base seconds for exponential backoff (0.25, 0.5, 1.0, ...)."""


class ProviderError(RuntimeError):
    """A provider failed in a way the worker should surface as a clean error.

    The table function catches this and raises a tidy DuckDB error rather than
    letting an arbitrary exception escape and crash the worker process.
    """


class MissingKeyError(ProviderError):
    """The provider needs an API key and none was configured."""


@runtime_checkable
class Provider(Protocol):
    """Protocol every provider implements.

    Attributes:
        name: The provider's stable identifier (the ``source`` column value and
            the string passed as ``provider :=``).
        requires_key: Whether a configured API key is mandatory.
        supports_answer: Whether the provider exposes a synthesized answer
            (powering ``web_answer``).
    """

    name: str
    requires_key: bool
    supports_answer: bool

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        """Run a search and return unified results (rank assigned by the caller)."""
        ...

    def answer(self, query: str, *, opts: dict[str, Any]) -> str | None:
        """Return a synthesized answer string, or ``None`` if unsupported/empty."""
        ...


class BaseProvider:
    """Shared HTTP plumbing: timeout, bounded retry/backoff, key handling.

    Concrete providers subclass this, set the class attributes, and implement
    :meth:`search` (and optionally :meth:`answer`). ``base_url`` defaults to the
    provider's production endpoint but is injectable for tests.
    """

    name: str = "base"
    requires_key: bool = True
    supports_answer: bool = False
    default_base_url: str = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        """Store credentials and endpoint; an injected client enables testing."""
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self._client = client

    # -- HTTP ---------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=self.timeout)

    def request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue an HTTP request, retrying 429/5xx with exponential backoff.

        Raises :class:`ProviderError` on a non-retryable HTTP error, on
        exhausted retries, or on a transport/timeout failure -- so callers only
        ever see a single, tidy exception type.
        """
        owns_client = self._client is None
        client = self._http()
        last_exc: Exception | None = None
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    resp = client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                    )
                except httpx.HTTPError as exc:  # timeout, connect error, ...
                    last_exc = exc
                else:
                    if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES - 1:
                        last_exc = ProviderError(f"{self.name}: HTTP {resp.status_code}")
                    elif resp.is_error:
                        raise ProviderError(f"{self.name}: HTTP {resp.status_code} {resp.text[:200]!r}")
                    else:
                        return resp
                # Backoff before the next attempt.
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE * (2**attempt))
            raise ProviderError(f"{self.name}: request failed after {MAX_RETRIES} attempts") from last_exc
        finally:
            if owns_client:
                client.close()

    # -- key handling -------------------------------------------------------

    def require_key(self) -> str:
        """Return the API key or raise :class:`MissingKeyError`."""
        if not self.api_key:
            raise MissingKeyError(
                f"provider '{self.name}' requires an API key; configure it via the "
                f"VGI secret provider (or the {self.name.upper()} env var for local dev)"
            )
        return self.api_key

    # -- defaults -----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        opts: dict[str, Any],
    ) -> list[Result]:
        """Run a search and return unified results. Subclasses must override."""
        raise NotImplementedError

    def answer(self, query: str, *, opts: dict[str, Any]) -> str | None:
        """Default: this provider has no synthesized answer."""
        return None
