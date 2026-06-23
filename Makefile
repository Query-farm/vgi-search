# vgi-search — test targets.
#
# Usage:
#   make test         # unit/integration tests + SQL (end-to-end) tests
#   make test-unit    # pytest suite (fixtures + mock-server E2E)
#   make test-live    # pytest live smoke (real DDG Instant Answer, no key; needs network)
#   make test-sql     # DuckDB sqllogictest E2E via haybarn-unittest (mock-server driven)
#   make lint         # ruff + mypy
#
# The SQL E2E suite drives the *real* worker as a DuckDB subprocess through the
# haybarn-unittest sqllogictest runner, with every provider's base_url pointed at
# a local mock HTTP server (started by the .test fixture). Deterministic, no keys,
# no cost, no real network egress.

# The worker command DuckDB runs for the `vgi` extension's ATTACH. The SQL E2E
# uses the mock-wired launcher (starts the canned-response server, points every
# provider's base_url at it) so the suite is deterministic and key-free.
VGI_SEARCH_WORKER ?= uv run --python 3.13 scripts/mock_worker.py

# haybarn-unittest is a uv tool; ~/.local/bin must be on PATH to find it.
HAYBARN ?= haybarn-unittest
LOCAL_BIN := $(HOME)/.local/bin

TEST_DIR     = .
TEST_PATTERN = test/sql/*

.PHONY: test test-unit test-live test-sql lint ensure-haybarn

test: test-unit test-sql

# Full unit suite: fixture parser tests + the in-process mock-server E2E.
# The live smoke test is gated by the `live` marker and excluded here.
test-unit:
	uv run --no-sync pytest -q -m "not live"

# Optional live smoke: hits the real DuckDuckGo Instant Answer API (free, no
# key). Needs network; not part of the CI gate.
test-live:
	uv run --no-sync pytest -q -m live

# Install the haybarn-unittest sqllogictest runner if it isn't already present.
ensure-haybarn:
	@if ! PATH="$(LOCAL_BIN):$$PATH" command -v $(HAYBARN) >/dev/null 2>&1; then \
		echo "Installing haybarn-unittest..."; \
		uv tool install haybarn-unittest; \
	fi

# End-to-end SQL tests: load `vgi`, ATTACH the worker, run the .test glob.
# CRITICAL: under haybarn-unittest, `require vgi` SKIPS — the .test files use an
# explicit `LOAD vgi;` instead.
test-sql: ensure-haybarn
	PATH="$(LOCAL_BIN):$$PATH" VGI_SEARCH_WORKER="$(VGI_SEARCH_WORKER)" \
		$(HAYBARN) --test-dir "$(TEST_DIR)" "$(TEST_PATTERN)"

lint:
	uv run --no-sync ruff check .
	uv run --no-sync mypy vgi_search/
