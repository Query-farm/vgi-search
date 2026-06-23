"""``web_answer`` -- a synthesized one-line answer for a query (scalar).

``web_answer(query, provider)`` returns a single VARCHAR: a synthesized answer
from a provider that exposes one (``tavily`` with a key, or ``ddg`` Instant
Answer for free). For a provider without an answer, or a query with none, it
returns NULL -- never an error, never a crash.

Scalars are POSITIONAL-ONLY in VGI/DuckDB (``name := value`` is a table-function
feature), so ``provider`` is a positional :class:`ConstParam`, not a named arg::

    SELECT web_answer('who maintains duckdb', 'tavily');
    SELECT web_answer('python programming language', 'ddg');

The ``tavily`` key is supplied via the VGI secret provider (declared as an
optional :class:`Secret` so a missing key degrades to NULL rather than failing
the whole scan); ``ddg`` needs no key. Keys are never read from SQL.
"""

from __future__ import annotations

from typing import Annotated, Any

import pyarrow as pa
from vgi.arguments import ConstParam, Param, Returns, Secret
from vgi.metadata import FunctionExample
from vgi.scalar_function import ScalarFunction

from vgi_search.providers import ProviderError, build_provider
from vgi_search.secrets import key_from_secret


class WebAnswer(ScalarFunction):
    """``web_answer(query, provider)`` -> synthesized answer string (or NULL)."""

    class Meta:
        name = "web_answer"
        description = "Synthesized one-line answer for a query (tavily/ddg); NULL when unavailable"
        categories = ["search", "web", "rag"]
        required_secrets = ["tavily"]
        examples = [
            FunctionExample(
                sql="SELECT web_answer('who maintains duckdb', 'tavily')",
                description="Tavily-synthesized answer (needs a key)",
            ),
            FunctionExample(
                sql="SELECT web_answer('python programming language', 'ddg')",
                description="DuckDuckGo Instant Answer (free, no key)",
            ),
        ]

    @classmethod
    def compute(
        cls,
        query: Annotated[pa.StringArray, Param(doc="Query to answer.")],
        provider: Annotated[str, ConstParam("Provider name: 'tavily' or 'ddg'.")],
        tavily_secret: Annotated[dict[str, pa.Scalar[Any]] | None, Secret("tavily")] = None,
    ) -> Annotated[pa.StringArray, Returns()]:
        # Resolve a key from the (optional) secret material; env fallback is
        # applied inside build_provider.
        secrets: dict[str, Any] = {"tavily": tavily_secret} if tavily_secret else {}
        api_key = key_from_secret(secrets, provider)

        try:
            backend = build_provider(provider, api_key=api_key)
        except ProviderError:
            # Unknown/disabled provider -> NULL for every row (never crash).
            return pa.array([None] * len(query), type=pa.string())

        if not getattr(backend, "supports_answer", False):
            return pa.array([None] * len(query), type=pa.string())

        out: list[str | None] = []
        for value in query.to_pylist():
            if value is None:
                out.append(None)
                continue
            try:
                out.append(backend.answer(value, opts={}))
            except ProviderError:
                out.append(None)
            except Exception:  # noqa: BLE001 - answers must never crash a scan
                out.append(None)
        return pa.array(out, type=pa.string())


SCALAR_FUNCTIONS: list[type] = [WebAnswer]
