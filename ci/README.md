# CI: the vgi-search worker integration suite

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the unit tests
and this repo's sqllogictest suite (`test/sql/*.test`) against the vgi-search
VGI worker through the **real DuckDB `vgi` extension** on every push / PR.

## How it works (no C++ build)

Rather than building the vgi DuckDB extension from source, CI drives a
**prebuilt** standalone `haybarn-unittest` (the DuckDB/Haybarn sqllogictest
runner, published in Haybarn's releases) and installs the **signed** `vgi`
extension from the Haybarn community channel:

1. **Install the worker** — `uv sync --frozen --extra http` into a venv.
2. **Download the runner** — the matching `haybarn_unittest-*` asset per platform.
3. **Preprocess** — [`preprocess-require.awk`](preprocess-require.awk) rewrites
   each `require <ext>` into an explicit signed `INSTALL <ext> FROM
   {community,core}; LOAD <ext>;`, and injects `INSTALL vgi FROM community;`
   before each bare `LOAD vgi;`. `require-env` and everything else pass through.
4. **Run** — [`run-integration.sh`](run-integration.sh) stages the preprocessed
   tree, resolves `VGI_SEARCH_WORKER` (the ATTACH `LOCATION`) per `$TRANSPORT`,
   warms the extension cache once, then runs the suite in a single
   `haybarn-unittest` invocation. Any failed assertion fails the job.

## The mock-driven worker (all transports)

The worker LOCATION is the **mock launcher** `scripts/mock_worker.py`, which
starts the in-process canned-response mock provider server, wires every
provider's `base_url` at it, supplies throwaway API keys, then runs the real
`SearchWorker`. So the authoritative SQL suite drives the real worker end to end
against deterministic fixtures — no keys, no cost, no live network egress.

Crucially the launcher **forwards its argv to `SearchWorker.main()`**, so the
**same launcher serves every transport** — the in-process mock server stays
alive for the life of the process whether DuckDB reaches the worker over stdio,
HTTP, or an AF_UNIX socket. No per-transport mock plumbing is needed.

## Transport matrix (subprocess | http | unix)

The same `test/sql/*.test` suite is run over all three VGI transports — the
extension picks the transport from the `LOCATION` string the `.test` files
`ATTACH`, and `run-integration.sh` builds that string from `$TRANSPORT`:

| `TRANSPORT`  | `VGI_SEARCH_WORKER` (LOCATION)            | How the worker is reached |
|--------------|-------------------------------------------|---------------------------|
| `subprocess` | `.venv/bin/python scripts/mock_worker.py` | extension spawns the launcher per query; Arrow IPC over stdin/stdout (default) |
| `http`       | `http://127.0.0.1:<port>`                 | harness boots `mock_worker.py --http --port 0 --port-file <f>`, waits for the port-file, then ATTACHes that URL |
| `unix`       | `unix:///tmp/search-<pid>.sock`           | harness boots `mock_worker.py --unix <sock>`, waits for the socket, then ATTACHes it |

The CI `integration` job is a `transport: [subprocess, http, unix]` × `os`
matrix; each leg runs `ci/run-integration.sh` with `TRANSPORT=<t>`. Run a single
transport locally with e.g. `TRANSPORT=http ci/run-integration.sh`.

### Port / readiness discovery

- **http**: the worker writes its auto-selected port to `--port-file`
  atomically, so the harness watches for that file (not stdout). Boot line:
  `mock_worker.py --http --port 0 --port-file <f>`.
- **unix**: the worker binds the socket and prints `UNIX:<abs-path>`; the
  harness polls for the socket file (`test -S`). Boot line:
  `mock_worker.py --unix <sock>`.

Both out-of-band server processes run with cwd = the repo root (so the launcher
resolves `tests.mock_server` / `search_worker`) and are trap-killed on exit.

### HTTP transport needs the `httpfs` extension (resolved, not gated)

The vgi extension implements HTTP transport on top of DuckDB's **httpfs**
extension, so an `http://` ATTACH binds with `VGI HTTP transport requires the
httpfs extension` unless httpfs is loaded first. This is a **dependency**, not a
protocol limitation, so we resolve it: the http leg injects a signed `INSTALL
httpfs FROM core; LOAD httpfs;` into each staged `.test` (after the awk-injected
`LOAD vgi;`). The leg also needs the worker's `http` extra (waitress) —
`pyproject.toml` ships an `http` extra (`vgi-python[http]`), the PEP 723 headers
(`search_worker.py` and `scripts/mock_worker.py`) list it, and CI runs
`uv sync --frozen --extra http`.

> **Sharp edge — the runner silently SKIPs HTTP errors.** The haybarn/DuckDB
> sqllogictest runner's default skip list skips any statement whose error
> contains `"HTTP"` or `"Unable to connect"`, so a broken http setup reports
> "All tests were skipped" — a green-looking **fake pass**.
> `run-integration.sh` fails the leg unless the runner reports `All tests passed
> (N assertions …)` with N > 0 and zero skips.

### `web_search` pagination over HTTP (externalized cursor — no gate)

`web_search` is a streaming/paging table function: it fetches one provider page
and streams it to DuckDB in `CHUNK_ROWS`-sized batches across multiple `process`
ticks. Streaming table functions run fine over the **stateless** HTTP transport
**because the cursor is externalized**: the per-scan position lives in a
plain-serializable `ScanState(ArrowSerializableDataclass)` (`fetched` /
`emitted` / `page_size`) that the framework round-trips through its continuation
token on every tick; the fetched page itself lives in a process-local cache and
is re-fetched deterministically if evicted. So the http leg runs the **full**
suite including `web_search.test` (contiguous unique ranks 1..N, disjoint
`page := 1`) — nothing is gated. (This is the same "externalize the scan
position into the serialized state" pattern as the vgi-cve cursor fix.)

### Per-transport status

- **subprocess**: GREEN — 59 assertions.
- **http**: GREEN — 67 assertions (59 + the injected httpfs INSTALL/LOAD across
  the four `.test` files). Full suite incl. `web_search.test` paging.
- **unix**: GREEN — 59 assertions.

## Run it locally

```bash
uv sync --python 3.13 --extra http
HAYBARN_UNITTEST=/path/to/haybarn-unittest \
WORKER_CMD="uv run --python 3.13 scripts/mock_worker.py" \
  TRANSPORT=subprocess ci/run-integration.sh    # or TRANSPORT=http / TRANSPORT=unix
```

`TRANSPORT` defaults to `subprocess`, and `WORKER_CMD` defaults to
`uv run --python 3.13 <repo>/scripts/mock_worker.py`.
