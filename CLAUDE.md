# CLAUDE.md — vgi-search

Contributor/agent notes. User-facing docs live in `README.md`; this is the
"how it's built and where the sharp edges are" companion.

## What this is

A [VGI](https://query.farm) worker exposing **unified web search** to DuckDB/SQL
behind **one pluggable provider surface**. `search_worker.py` assembles every
function into one `search` catalog (single `main` schema) over stdio. It is an
**egress connector** (queries leave the engine for a third-party API) and
**commodity access** (value lives in the upstream subscription, not the worker).
The durable value is the pluggable-provider design — see the honest framing in
`README.md` and keep it accurate.

## Layout

```
search_worker.py          repo-root stdio entry; PEP 723 inline deps; main()
serve.py                  HTTP entry (forces --http)
vgi_search/
  result.py               the unified Result dataclass (the one row shape) + extra_json()
  schema_utils.py         Arrow column-comment helper + TIMESTAMPTZ constant
  secrets.py              pull a provider API key out of resolved VGI secret material
  providers/
    base.py               Provider protocol + BaseProvider (timeout, retry/backoff, key handling)
    _dates.py             lenient published-date -> UTC datetime (or NULL)
    brave.py tavily.py exa.py ddg.py searxng.py   v1 providers
    serpapi.py serper.py  flagged Google/Bing-SERP scrapers (VGI_SEARCH_ENABLE_SERP=1)
    __init__.py           registry/factory: name -> class, key+base_url resolution, flags
  tables.py               web_search (table fn, scan-state paging) + search_providers (discovery)
  scalars.py              web_answer (scalar, positional-only)
scripts/mock_worker.py    SQL-E2E launcher: starts the mock server, wires base_urls, runs the worker
tests/
  fixtures/*.json         one captured response shape per provider
  mock_server.py          threaded canned-response HTTP server (also `python -m`)
  harness.py              in-process bind/init/process driver + scan-state round-trip
  test_parsers.py         fixture JSON -> unified schema (+ missing->NULL, extra JSON)
  test_mock_e2e.py        real httpx round-trip per provider vs the mock; retry/backoff/error
  test_tables.py          web_search lifecycle + the scan-state-round-trip headline test
  test_scalars.py         web_answer
  test_registry.py        factory, flags, key resolution
  test_live.py            GATED live DDG Instant Answer smoke (marker `live`, not in CI)
test/sql/*.test           haybarn-unittest sqllogictest — authoritative E2E (mock-driven)
Makefile                  test / test-unit / test-live / test-sql / lint
```

## Core VGI conventions (read first)

1. **Scalars are POSITIONAL-ONLY; only table functions take `name := value`.**
   `web_answer(query, provider)` is a scalar → `provider` is a positional
   `ConstParam`. `web_search` is a table function → `provider` / `count` / `page`
   are named args.
2. **Named table-fn args route by the dataclass FIELD NAME**, not the `Arg()`
   alias. Spell the field exactly what the caller types.
3. **`page`, NOT `offset`.** `offset` is a DuckDB **reserved keyword** — `offset := 1`
   is a parser error (learned the hard way; cf. vgi-translate's `to`/`from`). The
   page argument is named `page` (a 0-based page index). Do not rename it back.
4. **TIMESTAMPTZ / JSON returns need an explicit Arrow type.** `published` is
   `pa.timestamp("us", tz="UTC")` (the `TIMESTAMPTZ` constant in `schema_utils`);
   `extra` is a JSON **string** column (`VARCHAR`), so callers use `->>` /
   `json_extract` — no nested-Arrow plumbing.
5. **`web_search` runs `@init_single_worker` (max_workers=1).** One search call =
   one ordered page; fanning the scan across parallel workers makes each re-run
   the query and **duplicate every row** (the first E2E failure was `count(*)`
   returning 80 instead of 10). Keep it single-worker.
6. **`require vgi` SKIPS under haybarn-unittest** — the `.test` files use explicit
   `LOAD vgi;`.

## Pagination = scan state (the headline)

`web_search` fetches one provider page (selected by `page`), then streams it to
DuckDB in `CHUNK_ROWS`-sized batches. The **cursor** (`fetched` / `emitted` /
`page_size`) is a plain-serializable `ScanState(ArrowSerializableDataclass)`,
round-tripped across every `process` tick (and so across batch boundaries). The
fetched rows themselves live in a process-local `_PAGE_CACHE` keyed by execution
id (re-fetched deterministically if evicted) so the serialized state stays a few
ints. `tests/harness.run_table_function(..., serialize_state=True)` round-trips
the state through `serialize_to_bytes`/`deserialize_from_bytes` between ticks; the
`web_search.test` SQL suite asserts contiguous unique ranks 1..N and a disjoint
`page := 1`. This is the **easy, non-defensible** kind of scan state — documented
as such.

## Providers & secrets

- Add a provider: subclass `BaseProvider`, set `name` / `requires_key` /
  `supports_answer` / `default_base_url`, implement `search` (map JSON →
  `Result`, assign `extra`), register it in `providers/__init__._REGISTRY`. Get
  timeout + retry/backoff + key handling for free from the base.
- **Keys come from the VGI secret provider** (one secret type per keyed provider,
  declared in each function's `Meta.required_secrets` as `SecretLookupEntry`s —
  plain strings are silently dropped by the metadata resolver). For local/CI a
  `<PROVIDER>_API_KEY` env var is the fallback; `secrets.key_from_secret` reads
  the secret, the registry applies the env fallback. **Never read a key from SQL.**
- `base_url` is injectable per provider so tests point it at the mock server.
- **searxng** has no default endpoint (`base_url` required); **serpapi/serper**
  are OFF unless `VGI_SEARCH_ENABLE_SERP=1`. We never scrape Google/Bing/DDG HTML.

## Reliability discipline

Per-call timeout + bounded retry/backoff on 429/5xx live in
`BaseProvider.request_with_retry`. Every failure becomes a single `ProviderError`;
`web_search` surfaces it as a clean DuckDB error, `web_answer` degrades to NULL.
The worker must never crash on a provider error (the `errors.test` suite proves
the worker survives bad-provider queries and still answers the next one).

## Testing

```sh
uv run --no-sync pytest -m "not live"   # fixtures + mock-server E2E (no keys, no network)
uv run --no-sync pytest -m live         # optional real DDG Instant Answer (free, needs network)
make test-sql                           # haybarn E2E over test/sql/* (mock-driven; authoritative)
make test                               # both
uv run --no-sync ruff check . && uv run --no-sync mypy vgi_search
```

`make test-sql` exports `VGI_SEARCH_WORKER="uv run --python 3.13
scripts/mock_worker.py"` and runs `haybarn-unittest --test-dir . "test/sql/*"`
(install once: `uv tool install haybarn-unittest`, then put `~/.local/bin` on
`PATH`). **The SQL suite is authoritative.** CI runs unit + an `e2e` job (mock
launched via `.venv/bin/python scripts/mock_worker.py`) + lint. The `live` marker
is never in the gate; real brave/tavily/exa need paid keys → manual only.

## Licensing — DO NOT regress

Worker code is **MIT**. The one runtime dep `httpx` is BSD-3-Clause; `pyarrow` is
Apache-2.0 — both permissive, no provider SDKs bundled (plain HTTP). Each provider
API has its own ToS, summarized in the README table; keys/subscriptions are the
user's responsibility. Keep the README ToS notes accurate when touching providers.
