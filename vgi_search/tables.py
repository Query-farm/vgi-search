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

import json
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

from vgi_search.meta import object_tags
from vgi_search.providers import DEFAULT_PROVIDER, ProviderError, build_provider, provider_info
from vgi_search.result import Result
from vgi_search.schema_utils import TIMESTAMPTZ, field
from vgi_search.secrets import key_from_secret

_WEB_SEARCH_DOC_LLM = (
    "Run one web search against a pluggable provider and stream a unified, ranked result set as a "
    "table.\n\n"
    "`web_search(query, provider := ..., count := ..., page := ...)` returns rows with a fixed "
    "shape -- `title`, `url`, `snippet`, `rank`, `source`, `published`, `score`, `extra` (JSON) -- "
    "no matter which backend served them (Brave, Tavily, Exa, SearXNG, DuckDuckGo, or the opt-in "
    "SerpApi/Serper scrapers).\n\n"
    "**Use it for RAG / retrieval** when you need live web results to ground a prompt. It is a "
    "table function, so named arguments apply: `provider` selects the backend (defaults to the "
    "configured default), `count` caps results (1-50, default 10), and `page` is a 0-based page "
    "index for pagination (page N skips N*count results) -- note it is `page`, not `offset` "
    "(`offset` is a DuckDB reserved word).\n\n"
    "**Inputs:** `query` (positional), `provider`/`count`/`page` (named). **Output:** the unified "
    "result schema above; `published`/`score`/`extra` are `NULL` when a provider does not expose "
    "them. **Edge cases:** keyed providers (`brave`/`tavily`/`exa`/serp*) need a key from the VGI "
    "secret provider and return no rows when unconfigured; `ddg` works for free; a bad/disabled "
    "provider name fails cleanly at bind; provider/network errors surface as a single DuckDB error, "
    "never a worker crash."
)

_WEB_SEARCH_DOC_MD = (
    "# web_search\n\n"
    "The unified **web-search table function**: one search against a pluggable provider, streamed "
    "as a single ranked result schema.\n\n"
    "## Usage\n\n"
    "```sql\n"
    "SELECT title, url, snippet\n"
    "  FROM search.main.web_search('duckdb arrow protocol', provider := 'ddg', count := 5);\n"
    "SELECT * FROM search.main.web_search('vector database', provider := 'brave', page := 1);\n"
    "```\n\n"
    "## Arguments\n\n"
    "- `query` -- the search string (positional).\n"
    "- `provider :=` -- backend name (`brave`, `tavily`, `exa`, `searxng`, `ddg`, or opt-in "
    "`serpapi`/`serper`); defaults to the configured default provider.\n"
    "- `count :=` -- number of results, 1-50 (default 10).\n"
    "- `page :=` -- 0-based page index for pagination (default 0). It is `page`, not `offset`.\n\n"
    "## Notes\n\n"
    "- Returns the same columns for every provider; `published`/`score`/`extra` are `NULL` when the "
    "provider omits them.\n"
    "- Keyed providers read their key from the VGI secret provider (never from SQL) and return no "
    "rows until configured; `ddg` is free.\n"
    "- Provider/network failures surface as a clean DuckDB error and never crash the worker."
)

_PROVIDERS_DOC_LLM = (
    "List every search provider this worker knows about and whether each is ready to use, as a "
    "table.\n\n"
    "`search_providers()` takes no arguments and returns one row per available provider with "
    "`provider` (its name), `requires_key` (whether an API key is needed), `supports_answer` "
    "(whether it can serve `web_answer`), and `configured` (whether a key -- or a `base_url` for "
    "SearXNG -- is actually present).\n\n"
    "**Use it for discovery / preflight**: call it to see which `provider := ...` values will work "
    "before issuing a `web_search`, or to confirm a secret/key is wired up. It needs no backend and "
    "reveals no secret values -- only the boolean `configured` flag. The opt-in SerpApi/Serper "
    "providers appear only when `VGI_SEARCH_ENABLE_SERP=1`."
)

_PROVIDERS_DOC_MD = (
    "# search_providers\n\n"
    "A zero-argument discovery table listing every search provider and whether it is configured.\n\n"
    "## Usage\n\n"
    "```sql\n"
    "SELECT * FROM search.main.search_providers() ORDER BY provider;\n"
    "SELECT provider FROM search.main.search_providers() WHERE configured;\n"
    "```\n\n"
    "## Notes\n\n"
    "- One row per available provider; reflects keys resolved from the VGI secret provider (with an "
    "env-var fallback) without exposing any secret value.\n"
    "- `configured` means the provider needs no key, has a resolved key, or (for SearXNG) has a "
    "`base_url`.\n"
    "- The opt-in SerpApi/Serper providers are listed only when `VGI_SEARCH_ENABLE_SERP=1`.\n"
    "- Use it before `web_search` to pick a working `provider := ...`."
)

# Guaranteed-runnable, catalog-qualified examples (VGI509). Each `sql` is
# self-contained and re-runnable against an attached `search` worker.
# `search_providers()` needs no backend; the `ddg` calls use the free, keyless
# DuckDuckGo endpoint. `expected_result` is omitted on purpose -- the linter only
# needs each query to execute cleanly, and live web output is not pinnable.
EXECUTABLE_EXAMPLES = (
    "[\n"
    "  {\n"
    '    "description": "List the search providers and whether each is configured (no backend '
    'needed).",\n'
    '    "sql": "SELECT provider, requires_key, configured FROM search.main.search_providers() '
    'ORDER BY provider"\n'
    "  },\n"
    "  {\n"
    '    "description": "Run a free DuckDuckGo web search and read the top result titles/URLs.",\n'
    '    "sql": "SELECT title, url, rank FROM search.main.web_search(\'python programming '
    "language', provider := 'ddg', count := 5) ORDER BY rank\"\n"
    "  },\n"
    "  {\n"
    '    "description": "Get a free synthesized one-line answer via DuckDuckGo Instant Answer.",\n'
    "    \"sql\": \"SELECT search.main.web_answer('python programming language', 'ddg') AS "
    'answer"\n'
    "  }\n"
    "]"
)

_WEB_SEARCH_COLUMNS_MD = (
    "| column | type | description |\n"
    "|---|---|---|\n"
    "| `title` | VARCHAR | Result title. |\n"
    "| `url` | VARCHAR | Result URL. |\n"
    "| `snippet` | VARCHAR | Description / excerpt for the result. |\n"
    "| `rank` | INTEGER | 1-based position in the result set. |\n"
    "| `source` | VARCHAR | Provider that served the result. |\n"
    "| `published` | TIMESTAMPTZ | Publication time when the provider exposes one (else NULL). |\n"
    "| `score` | DOUBLE | Provider relevance score when available (else NULL). |\n"
    "| `extra` | VARCHAR | Provider-specific fields, JSON-encoded (else NULL). |"
)

_PROVIDERS_COLUMNS_MD = (
    "| column | type | description |\n"
    "|---|---|---|\n"
    "| `provider` | VARCHAR | Provider name (pass as `provider := ...`). |\n"
    "| `requires_key` | BOOLEAN | Whether the provider needs an API key. |\n"
    "| `supports_answer` | BOOLEAN | Whether the provider exposes a synthesized answer. |\n"
    "| `configured` | BOOLEAN | Whether a key (or base_url for searxng) is configured. |"
)

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
        """Function metadata."""

        name = "web_search"
        description = "Search the web via a pluggable provider; returns the unified result schema"
        categories = ["search", "web", "rag", "retrieval"]
        required_secrets = _KEYED_SECRETS
        tags = {  # noqa: RUF012 - declarative metadata, not mutated
            **object_tags(
                title="Unified Web Search",
                doc_llm=_WEB_SEARCH_DOC_LLM,
                doc_md=_WEB_SEARCH_DOC_MD,
                keywords=[
                    "web search",
                    "search",
                    "serp",
                    "results",
                    "retrieval",
                    "rag",
                    "brave",
                    "tavily",
                    "exa",
                    "searxng",
                    "duckduckgo",
                    "ddg",
                    "query",
                    "ranked results",
                    "pagination",
                    "provider",
                ],
            ),
            "vgi.result_columns_md": _WEB_SEARCH_COLUMNS_MD,
        }
        examples = [
            FunctionExample(
                sql=(
                    "SELECT title, url, rank FROM "
                    "search.main.web_search('python programming language', provider := 'ddg', "
                    "count := 5) ORDER BY rank"
                ),
                description="Top web results via the free DuckDuckGo provider",
            ),
            FunctionExample(
                sql=(
                    "SELECT title, url, snippet FROM "
                    "search.main.web_search('duckdb arrow protocol', provider := 'brave', count := 10)"
                ),
                description="Top-10 web results via Brave (needs a key)",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParams[WebSearchArgs]) -> BindResponse:
        """Validate the provider and declare the fixed output schema."""
        # Validate the provider eagerly so a bad name fails at bind, cleanly.
        name = _resolve_provider_name(params.args.provider)
        try:
            build_provider(name)
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc
        return BindResponse(output_schema=WEB_SEARCH_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[WebSearchArgs]) -> ScanState:
        """Return a fresh scan-state cursor for a new execution."""
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
        """Fetch the selected provider page, mapping failures to ProviderError."""
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
        """Stream the fetched page in chunks, advancing the scan-state cursor."""
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
        """Function metadata."""

        name = "search_providers"
        description = "List available search providers and whether each has a key/base_url configured"
        categories = ["search", "metadata"]
        required_secrets = _KEYED_SECRETS
        tags = {  # noqa: RUF012 - declarative metadata, not mutated
            **object_tags(
                title="Search Provider Directory",
                doc_llm=_PROVIDERS_DOC_LLM,
                doc_md=_PROVIDERS_DOC_MD,
                keywords=[
                    "providers",
                    "search providers",
                    "discovery",
                    "capabilities",
                    "configured",
                    "api key",
                    "preflight",
                    "brave",
                    "tavily",
                    "exa",
                    "searxng",
                    "duckduckgo",
                    "serpapi",
                    "serper",
                ],
            ),
            "vgi.result_columns_md": _PROVIDERS_COLUMNS_MD,
            "vgi.executable_examples": EXECUTABLE_EXAMPLES,
        }
        examples = [
            FunctionExample(
                sql="SELECT * FROM search.main.search_providers() ORDER BY provider",
                description="Which providers are available and configured",
            ),
        ]

    @classmethod
    def cardinality(cls, params: BindParams[_ProvidersArgs]) -> TableCardinality:
        """Report the exact provider count as the table cardinality."""
        n = len(provider_info())
        return TableCardinality(estimate=n, max=n)

    @classmethod
    def process(cls, params: ProcessParams[_ProvidersArgs], state: None, out: OutputCollector) -> None:
        """Emit one row per provider with its configured/key/answer flags."""
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


# --- Table view of the parameterless provider directory -------------------
#
# `search_providers()` takes no arguments and always returns the same row set,
# so it is also exposed as a regular table (VGI311): `SELECT * FROM
# search.main.search_providers` (no parentheses). The table scans the same
# generator and shares its column schema; it carries its own discovery tags
# (table-flavored, distinct from the function's) and constraints.

_PROVIDERS_TABLE_DOC_MD = (
    "# search_providers (table)\n\n"
    "A regular table view of the provider directory: one row per search provider this worker "
    "knows about, with whether each is ready to use. It scans the same data as the "
    "`search_providers()` function but reads as a plain table, so you can `SELECT * FROM "
    "search.main.search_providers` without parentheses.\n\n"
    "## Usage\n\n"
    "```sql\n"
    "SELECT * FROM search.main.search_providers ORDER BY provider;\n"
    "SELECT provider FROM search.main.search_providers WHERE configured;\n"
    "```\n\n"
    "## Columns\n\n"
    "- `provider` -- provider name (the value to pass as `provider := ...` to `web_search`); "
    "primary key.\n"
    "- `requires_key` -- whether the provider needs an API key.\n"
    "- `supports_answer` -- whether the provider can serve `web_answer`.\n"
    "- `configured` -- whether a key (or, for SearXNG, a `base_url`) is actually present.\n\n"
    "## Notes\n\n"
    "- Reflects keys resolved from the VGI secret provider (with an env-var fallback) without "
    "exposing any secret value.\n"
    "- The opt-in SerpApi/Serper providers appear only when `VGI_SEARCH_ENABLE_SERP=1`.\n"
    "- Use it before `web_search` to pick a working `provider := ...`."
)

_PROVIDERS_TABLE_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": "List every provider and whether each is configured.",
            "sql": "SELECT * FROM search.main.search_providers ORDER BY provider",
        },
        {
            "description": "Just the providers that are ready to use.",
            "sql": "SELECT provider FROM search.main.search_providers WHERE configured",
        },
        {
            "description": "Providers that can serve web_answer.",
            "sql": "SELECT provider FROM search.main.search_providers WHERE supports_answer",
        },
    ]
)

PROVIDERS_TABLE_TAGS: dict[str, str] = {
    "vgi.title": "Search Provider Directory (table)",
    "vgi.doc_llm": _PROVIDERS_DOC_LLM,
    "vgi.doc_md": _PROVIDERS_TABLE_DOC_MD,
    "vgi.keywords": json.dumps(
        [
            "providers",
            "search providers",
            "directory",
            "discovery",
            "capabilities",
            "configured",
            "api key",
            "preflight",
            "brave",
            "tavily",
            "exa",
            "searxng",
            "duckduckgo",
            "serpapi",
            "serper",
        ]
    ),
    # VGI123 classifying tags (bare keys) for faceting.
    "domain": "information-retrieval",
    "category": "web-search",
    "topic": "search-providers",
    "vgi.example_queries": _PROVIDERS_TABLE_EXAMPLE_QUERIES,
    "vgi.result_columns_md": _PROVIDERS_COLUMNS_MD,
}
