"""``web_search`` -- the unified web-search table function.

``web_search(query, provider := ..., count := ..., page := ...)`` runs one
search against the chosen provider and streams the unified result schema:

    title VARCHAR, url VARCHAR, snippet VARCHAR, rank INTEGER, source VARCHAR,
    published TIMESTAMPTZ, score DOUBLE, extra VARCHAR(JSON)

It is a **table function**, so DuckDB's ``name := value`` named arguments apply
(``provider``, ``count``, ``page``). The TIMESTAMPTZ / JSON columns require an
explicit Arrow schema, declared in :meth:`WebSearch.on_bind`.

Pagination as scan state
------------------------
``offset`` selects a provider page; the fetched page is then streamed to DuckDB
in bounded chunks. The **cursor** -- which page we're on and how far into it we've
emitted -- is the externalized, plain-serializable scan state
(:class:`ScanState`), round-tripped across every ``process`` tick (and so across
batch boundaries). This is deliberately the *easy*, non-defensible kind of scan
state (a page/offset int), documented as such: the durable value of this worker
is the pluggable-provider surface, not the pagination.

Network-worker discipline: a provider error (bad key, HTTP failure, timeout) is
caught and re-raised as a clean DuckDB error via :class:`ProviderError`; it never
crashes the worker. Provider keys come from the VGI secret provider (with an env
fallback for local/CI); they are never taken from SQL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg, SecretLookupEntry
from vgi.invocation import BindResponse
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc import ArrowSerializableDataclass
from vgi_rpc.rpc import OutputCollector

from vgi_search.providers import DEFAULT_PROVIDER, ProviderError, build_provider, provider_info
from vgi_search.result import Result
from vgi_search.schema_utils import TIMESTAMPTZ, field
from vgi_search.secrets import key_from_secret

# Rows emitted per process tick. Deliberately small so a single page spans
# several batches -- exercising the scan-state round-trip across batch boundaries.
CHUNK_ROWS = 5

# Process-local cache of the fetched page, keyed by execution id. The *cursor*
# lives in serializable scan state; the (non-serializable, possibly large)
# fetched rows live here, re-fetched only if evicted. This mirrors how a real
# connector externalizes a cheap cursor while holding fetched data in memory.
_PAGE_CACHE: dict[str, list[Result]] = {}

WEB_SEARCH_SCHEMA = pa.schema(
    [
        field("title", pa.string(), "Result title."),
        field("url", pa.string(), "Result URL."),
        field("snippet", pa.string(), "Description / excerpt for the result."),
        field("rank", pa.int32(), "1-based position in the result set."),
        field("source", pa.string(), "Provider that served the result."),
        field("published", TIMESTAMPTZ, "Publication time when the provider exposes one (else NULL)."),
        field("score", pa.float64(), "Provider relevance score when available (else NULL)."),
        field("extra", pa.string(), "Provider-specific fields, JSON-encoded (else NULL)."),
    ]
)

# Provider keys are declared as VGI secret types so the catalog advertises them;
# they resolve through the secret provider (env-var fallback handled downstream).
_KEYED_PROVIDERS = ["brave", "tavily", "exa", "serpapi", "serper"]
_KEYED_SECRETS = [SecretLookupEntry(secret_type=p) for p in _KEYED_PROVIDERS]


@dataclass(slots=True, frozen=True, kw_only=True)
class WebSearchArgs:
    """Arguments for ``web_search`` (one positional query + named options).

    Field names MUST match the SQL named-argument keys (DuckDB routes
    ``name := value`` by field name), so ``provider`` / ``count`` / ``page``
    are spelled exactly as a caller types them.
    """

    query: Annotated[str, Arg(0, doc="The search query string.")]
    provider: Annotated[
        str,
        Arg(
            "provider",
            default="",
            doc=f"Provider name (default '{DEFAULT_PROVIDER}' or VGI_SEARCH_DEFAULT_PROVIDER).",
        ),
    ]
    count: Annotated[int, Arg("count", default=10, ge=1, le=50, doc="Number of results to return (default 10).")]
    # NOTE: the named arg is `page`, NOT `offset` -- `offset` is a DuckDB reserved
    # keyword and `offset := 1` is a parser error. `page` is a 0-based page index
    # (page N == results [N*count, (N+1)*count)), which is exactly how the keyed
    # providers (brave/searxng/serp*) paginate; it is the externalized scan-state
    # cursor. Kept as the field name so callers type `page := 1`.
    page: Annotated[
        int,
        Arg("page", default=0, ge=0, doc="0-based result page index (page N skips N*count; default 0)."),
    ]


@dataclass(kw_only=True)
class ScanState(ArrowSerializableDataclass):
    """Externalized pagination cursor, round-tripped across process ticks.

    Plain-serializable ints/strings only -- the heavy fetched page lives in the
    process-local cache. ``fetched`` flips true once the provider call for this
    page has run; ``emitted`` advances as chunks are streamed out.
    """

    fetched: bool = False
    emitted: int = 0
    page_size: int = 0


def _resolve_provider_name(arg: str) -> str:
    if arg:
        return arg
    return os.environ.get("VGI_SEARCH_DEFAULT_PROVIDER") or DEFAULT_PROVIDER


def _opts_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Pull provider passthrough opts from DuckDB settings (best-effort)."""
    opts: dict[str, Any] = {}
    for key in ("country", "search_lang", "language", "topic", "search_depth", "engines", "categories"):
        scalar = settings.get(f"vgi_search_{key}")
        if scalar is not None:
            value = scalar.as_py() if hasattr(scalar, "as_py") else scalar
            if value is not None:
                opts[key] = value
    return opts


@init_single_worker
class WebSearch(TableFunctionGenerator[WebSearchArgs, ScanState]):
    """Unified web search across pluggable providers (see module docstring).

    Runs single-worker (``max_workers=1``): one search call produces one ordered
    page, so fanning the scan across parallel workers would each re-run the query
    and duplicate rows. A single generator keeps the page (and its 1-based ranks)
    coherent and the provider call made exactly once.
    """

    FunctionArguments: ClassVar[type] = WebSearchArgs

    class Meta:
        name = "web_search"
        description = "Search the web via a pluggable provider; returns the unified result schema"
        categories = ["search", "web", "rag", "retrieval"]
        required_secrets = _KEYED_SECRETS
        examples = [
            FunctionExample(
                sql=(
                    "SELECT title, url, snippet FROM "
                    "web_search('duckdb arrow protocol', provider := 'brave', count := 10)"
                ),
                description="Top-10 web results via Brave",
            ),
            FunctionExample(
                sql="SELECT * FROM web_search('who maintains duckdb', provider := 'ddg')",
                description="DuckDuckGo Instant Answer (zero-click; free, no key)",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParams[WebSearchArgs]) -> BindResponse:
        # Validate the provider eagerly so a bad name fails at bind, cleanly.
        name = _resolve_provider_name(params.args.provider)
        try:
            build_provider(name)
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc
        return BindResponse(output_schema=WEB_SEARCH_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[WebSearchArgs]) -> ScanState:
        return ScanState()

    @classmethod
    def _execution_key(cls, params: ProcessParams[WebSearchArgs]) -> str:
        eid = getattr(params.init_response, "execution_id", None) or getattr(
            params.init_call, "global_execution_id", None
        )
        a = params.args
        return f"{eid}:{a.provider}:{a.query}:{a.count}:{a.page}"

    @classmethod
    def _fetch_page(cls, params: ProcessParams[WebSearchArgs]) -> list[Result]:
        a = params.args
        name = _resolve_provider_name(a.provider)
        secrets: dict[str, Any] = params.secrets or {}
        api_key = key_from_secret(secrets, name)
        opts = _opts_from_settings(params.settings or {})
        try:
            provider = build_provider(name, api_key=api_key)
            return provider.search(a.query, count=a.count, offset=a.page, opts=opts)
        except ProviderError:
            raise
        except Exception as exc:  # defensive: never let an odd error crash the worker
            raise ProviderError(f"{name}: {exc}") from exc

    @classmethod
    def process(
        cls,
        params: ProcessParams[WebSearchArgs],
        state: ScanState,
        out: OutputCollector,
    ) -> None:
        key = cls._execution_key(params)

        if not state.fetched:
            fetched = cls._fetch_page(params)
            _PAGE_CACHE[key] = fetched
            state.fetched = True
            state.page_size = len(fetched)

        cached = _PAGE_CACHE.get(key)
        if cached is None:
            # Cache evicted between ticks (e.g. state migrated to another worker):
            # re-fetch deterministically from the same args.
            cached = cls._fetch_page(params)
            _PAGE_CACHE[key] = cached
        rows: list[Result] = cached

        if state.emitted >= len(rows):
            _PAGE_CACHE.pop(key, None)
            out.finish()
            return

        chunk = rows[state.emitted : state.emitted + CHUNK_ROWS]
        out.emit(_to_batch(chunk, params.output_schema))
        state.emitted += len(chunk)


def _to_batch(rows: list[Result], schema: pa.Schema) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {
            "title": [r.title for r in rows],
            "url": [r.url for r in rows],
            "snippet": [r.snippet for r in rows],
            "rank": [r.rank for r in rows],
            "source": [r.source for r in rows],
            "published": pa.array([r.published for r in rows], type=TIMESTAMPTZ),
            "score": [r.score for r in rows],
            "extra": [r.extra_json() for r in rows],
        },
        schema=schema,
    )


_PROVIDERS_SCHEMA = pa.schema(
    [
        field("provider", pa.string(), "Provider name (pass as provider := ...).", nullable=False),
        field("requires_key", pa.bool_(), "Whether the provider needs an API key.", nullable=False),
        field("supports_answer", pa.bool_(), "Whether the provider exposes a synthesized answer.", nullable=False),
        field("configured", pa.bool_(), "Whether a key (or base_url for searxng) is configured.", nullable=False),
    ]
)


@dataclass(slots=True, frozen=True, kw_only=True)
class _ProvidersArgs:
    """``search_providers()`` takes no arguments."""


@init_single_worker
@bind_fixed_schema
class SearchProviders(TableFunctionGenerator[_ProvidersArgs]):
    """List available providers and whether each is configured (key/base_url).

    Reflects the keys resolved from the VGI secret provider (with the same
    env-var fallback the search path uses), so an operator can see at a glance
    which backends are ready without revealing any secret value.
    """

    FIXED_SCHEMA: ClassVar[pa.Schema] = _PROVIDERS_SCHEMA
    FunctionArguments: ClassVar[type] = _ProvidersArgs

    class Meta:
        name = "search_providers"
        description = "List available search providers and whether each has a key/base_url configured"
        categories = ["search", "metadata"]
        required_secrets = _KEYED_SECRETS
        examples = [
            FunctionExample(
                sql="SELECT * FROM search_providers() ORDER BY provider",
                description="Which providers are available and configured",
            ),
        ]

    @classmethod
    def cardinality(cls, params: BindParams[_ProvidersArgs]) -> TableCardinality:
        n = len(provider_info())
        return TableCardinality(estimate=n, max=n)

    @classmethod
    def process(cls, params: ProcessParams[_ProvidersArgs], state: None, out: OutputCollector) -> None:
        secrets: dict[str, Any] = params.secrets or {}
        keys = {p: key_from_secret(secrets, p) for p in _KEYED_PROVIDERS}
        rows = provider_info(api_keys=keys)
        out.emit(
            pa.RecordBatch.from_pydict(
                {
                    "provider": [r["provider"] for r in rows],
                    "requires_key": [r["requires_key"] for r in rows],
                    "supports_answer": [r["supports_answer"] for r in rows],
                    "configured": [r["configured"] for r in rows],
                },
                schema=params.output_schema,
            )
        )
        out.finish()


TABLE_FUNCTIONS: list[type] = [WebSearch, SearchProviders]
