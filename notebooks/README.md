# Search notebooks

[01 · Audit Instant Answer shortlists](01_audit_instant_answer_shortlists.ipynb)
examines two real DuckDuckGo Instant Answer responses. It distinguishes abstracts
from related-topic navigation, preserves missing publication dates and scores, and
tracks whether longer rank prefixes add different URL hosts. It does not treat
Instant Answer as a general web SERP or measure relevance, recall or factual accuracy.

## Run from this directory alone

Install [uv](https://docs.astral.sh/uv/) and Git, then:

```sh
cd notebooks
uv sync --locked
uv run --locked python execute.py
uv run --locked python -m ipykernel install --user --name vgi-search-notebooks --display-name 'Python (vgi-search notebooks)'
```

Start Jupyter in this directory and select that kernel. Notebook Python 3.13/3.14
is supported; uv obtains Python 3.13 for the worker. Initial setup downloads the
locked environment, VGI extension and tagged Git worker. No source checkout,
root .venv or local worker build is required. Tested on macOS ARM64 and Haybarn
2.0.0rc11. No paid provider keys or private queries are used.

The visible literal connection is:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'search' (
 TYPE vgi,
 LOCATION 'uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-search.git@v0.4.0 --constraints worker-constraints.txt vgi-search-worker'
);
```

Git tag v0.4.0 resolves to `0d7e9df6fb496688de6190f4208cab906cb40b2b`.
This is a tagged Python package installed and cached by uvx, not a PyPI package or
native GitHub binary. `worker-constraints.txt` pins SDK 0.51.0, RPC 0.53.1, Arrow
26.0.1, httpx 0.28.1 and httpx2 2.16.0 from the source lock. Upgrading the tag or
constraints requires rerunning defaults, experiment, live mode and source checks.

## Recorded and live data

`recordings/manifest.json` describes two actual public JSON bodies, captured
2026-11-14 at 09:59:17 UTC. It stores exact request URLs, capture intervals, HTTP
Date, byte lengths and SHA256 hashes. `python.json` and `arrow.json` are unchanged
responses for `Python (programming language)` and `Apache Arrow`; no records are
synthesized. The notebook shows metadata, short title prefixes and URLs, not full
abstracts. These recordings are for reproducibility, not current facts or a claim
that linked documents were verified.

Default `DATA_MODE="replay"` starts a small loopback HTTP server and sets the
worker's supported `VGI_SEARCH_DDG_BASE_URL` override. The real worker parses,
normalizes, ranks and streams the responses. The helper checks complete query
arguments and hashes, permits exactly two queries with at most two reads each,
and rejects every unmatched request. There is no fabricated or stale fallback.

Set `DATA_MODE="live"` to forward the same bounded reads to the real DDG endpoint.
The helper sends an identifying notebook User-Agent; tagged 0.4.0 predates the
current source's own User-Agent addition. This is documented HTTP forwarding,
not a worker-code change. Live responses are never replaced by recordings. Empty
or non-JSON bodies fail. During exploratory requests the unrelated `DuckDB` query
returned HTTP 200 with an empty/non-JSON body; it was not used or substituted.

One run performs two searches and two separate answer reads. They are independent
live responses, not an atomic snapshot. The helper captures per-read UTC receive
times, hashes and bytes under ignored `artifacts/`. Capture time is not publication
time. `web_answer` hides provider errors as NULL; the strict HTTP audit detects
those failures before interpreting answer availability. Cleanup stops the owned
server and restores the prior endpoint override.

`count := 50` caps returned worker rows; DDG still returns the complete answer
payload before the worker slices it. `TOP_N` filters local materialized rows and
never changes requests. Neither count nor SQL LIMIT is a network-byte budget.
Each query can yield fewer rows, and a page at the 50-row cap may be truncated.
Neither case measures web completeness. Worker mapping is checked independently
against the first response body for each query.

## Experiment and live validation

```sh
uv run --locked python execute.py --parameters '{"TOP_N":8}' --output-dir validation/top-8
uv run --locked python execute.py --parameters '{"DATA_MODE":"live"}' --output-dir validation/live
```

Keep replay selected for the top-8 experiment. Compare selected result kinds,
query–URL memberships and host counts; the complete page fingerprint and prefix
curves must stay fixed. Reset TOP_N to 3. URL-host counts are not independent
sources, and exact URL deduplication does not merge redirects or tracking aliases.
Unknown dates/scores remain missing; response order is not a relevance score.

The runner uses a fresh kernel and replaces saved defaults atomically only after
success. Overrides require another destination and unknown names fail. Experiments
include recordings, helper, branding, documentation and locked environment. Raw
artifacts and separate executions are ignored; successful default outputs are
embedded in the committed notebook. See [STANDARD.md](STANDARD.md).

## Validation

Defaults, a notebooks-only top-8 experiment, and a separate live execution pass,
with two inspected charts each. The experiment selects 12 instead of six rows
while retaining two URL hosts per query, the same 26-row page fingerprint, prefix
curves and four-read audit. Live validation on 2026-11-14 at 10:05:02–04 UTC returned
the same response hashes; that agreement is a recorded outcome, not an invariant
for future live runs. The replay server also rejects an unrecorded query and a
third read of a query even when query arguments are reordered. Three runner guards
reject unsafe or unknown parameter destinations without changing saved defaults.

Source gates pass: 51 offline pytest tests, 71 stdio and 71 Unix SQL assertions
in five cases, plus 81 HTTP assertions (including httpfs prerequisites). Ruff,
formatting, strict mypy (19 source files), and isolated pydoclint 0.8.7 pass. The
source executable metadata gate with vgi-lint-check 2.5.0, fail-on-info and link
checking disabled, scores 100/100 at L2 with zero findings. Source and root locks
remain unchanged.

## SQL credential isolation

The notebook creates Haybarn with a fresh temporary `secret_directory` and
`allow_persistent_secrets=False` before attaching workers. It fully drains and
checks an empty secret inventory. This example needs no saved SQL credential;
its explicit public-data or local-fixture configuration remains in effect.
Cleanup removes the temporary directory after closing the connection.
Existing user SQL secrets are neither read, modified nor deleted.
