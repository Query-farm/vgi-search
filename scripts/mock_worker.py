# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python>=0.8.3",
#     "httpx>=0.27",
# ]
# ///
"""Launch the search worker wired to an in-process mock provider server.

This is the worker command the **haybarn SQL E2E** ATTACHes (via
``VGI_SEARCH_WORKER``). It starts the canned-response mock HTTP server
(:mod:`tests.mock_server`) inside this process, points every provider's
``base_url`` at it through the ``VGI_SEARCH_<PROVIDER>_BASE_URL`` env vars (and
sets throwaway API keys so the keyed providers proceed), then runs the real
``SearchWorker`` over stdio.

The result: the authoritative SQL suite drives the *real* worker end to end --
real ATTACH, real bind/init/process, real httpx round-trips -- against
deterministic fixtures, with no keys, no cost, and no real network egress.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo root importable (tests.mock_server, search_worker).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tests.mock_server import MockServer  # noqa: E402


def main() -> None:
    """Start the mock server, wire provider base URLs, and run the worker."""
    server = MockServer()
    server.__enter__()  # start the threaded mock; lives as long as this process
    base = server.base

    # Point each provider at the mock and supply throwaway keys for keyed ones.
    os.environ["VGI_SEARCH_BRAVE_BASE_URL"] = f"{base}/paging"  # brave -> 12-result pager
    os.environ["VGI_SEARCH_TAVILY_BASE_URL"] = base
    os.environ["VGI_SEARCH_EXA_BASE_URL"] = f"{base}/exa"
    os.environ["VGI_SEARCH_DDG_BASE_URL"] = base
    os.environ["VGI_SEARCH_SEARXNG_BASE_URL"] = base
    os.environ.setdefault("BRAVE_API_KEY", "mock-key")
    os.environ.setdefault("TAVILY_API_KEY", "mock-key")
    os.environ.setdefault("EXA_API_KEY", "mock-key")
    os.environ.setdefault("VGI_SEARCH_DEFAULT_PROVIDER", "brave")

    from search_worker import SearchWorker

    SearchWorker.main()


if __name__ == "__main__":
    main()
