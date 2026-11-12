"""Extracting per-provider API keys from VGI secret-provider material.

The worker declares one VGI secret type per keyed provider (``brave``,
``tavily``, ``exa``, ``serpapi``, ``serper``). At bind/process time the framework
hands us the resolved secrets as ``dict[secret_type -> dict[field -> pa.Scalar]]``.
A secret's API key is read from any of a few conventional field names
(``api_key`` / ``key`` / ``token`` / ``value``), so operators can store it under
whichever the secret provider uses.

If no secret-provider value is present, the registry falls back to the provider's
env var (see :mod:`vgi_search.providers`). Keys are never logged or echoed.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from vgi.catalog.secret_type import SecretTypeSpec

#: Keyed providers -> human label. Each gets its own VGI secret type so one
#: ``CREATE SECRET (TYPE brave, api_key ...)`` configures that backend.
KEYED_PROVIDER_LABELS: dict[str, str] = {
    "brave": "Brave Search API",
    "tavily": "Tavily Search API",
    "exa": "Exa Search API",
    "serpapi": "SerpApi (opt-in; requires VGI_SEARCH_ENABLE_SERP=1)",
    "serper": "Serper.dev (opt-in; requires VGI_SEARCH_ENABLE_SERP=1)",
}

#: Keyed providers -> the page where a user obtains that provider's API key
#: (advertised as the secret schema's ``vgi_doc_url``, VGI1012).
KEYED_PROVIDER_KEY_URLS: dict[str, str] = {
    "brave": "https://brave.com/search/api/",
    "tavily": "https://app.tavily.com/",
    "exa": "https://dashboard.exa.ai/api-keys",
    "serpapi": "https://serpapi.com/manage-api-key",
    "serper": "https://serper.dev/api-key",
}

#: The secret types advertised in the catalog (``Worker.secret_types``). All are
#: optional: ``ddg`` and ``searxng`` work keyless, and each keyed provider also
#: falls back to its ``<PROVIDER>_API_KEY`` environment variable.
SECRET_TYPES: list[SecretTypeSpec] = [
    SecretTypeSpec(
        name=name,
        description=(
            f"Optional {label} key for web_search(provider := {name}) / web_answer. "
            f"Falls back to the {name.upper()}_API_KEY environment variable."
        ),
        schema=pa.schema(
            [
                pa.field(
                    "api_key",
                    pa.string(),
                    metadata={
                        "redact": "true",
                        "vgi_doc": f"{label} key, issued from your account at {KEYED_PROVIDER_KEY_URLS[name]}",
                    },
                )
            ],
            metadata={"vgi_doc_url": KEYED_PROVIDER_KEY_URLS[name]},
        ),
    )
    for name, label in KEYED_PROVIDER_LABELS.items()
]

_KEY_FIELDS = ("api_key", "key", "token", "value", "secret", "secret_string")


def key_from_secret(secrets: dict[str, dict[str, Any]], provider: str) -> str | None:
    """Return the API key for ``provider`` from resolved secrets, or ``None``.

    ``secrets`` is the resolved mapping from ``ProcessParams.secrets`` /
    ``SecretsAccessor.to_dict()`` (values are ``pa.Scalar``). Looks up the
    secret type named after the provider and reads the first present key field.
    """
    entry = secrets.get(provider)
    if not entry:
        # The connector keys resolved secrets by their DuckDB secret *name*
        # (``__default_brave`` for an unnamed ``CREATE SECRET (TYPE brave, ...)``),
        # so select by the secret's ``type`` field when the name differs.
        of_type = getattr(secrets, "of_type", None)
        matches = of_type(provider) if callable(of_type) else []
        entry = matches[0] if matches else None
    if not entry:
        return None
    for fieldname in _KEY_FIELDS:
        scalar = entry.get(fieldname)
        if scalar is None:
            continue
        value = scalar.as_py() if hasattr(scalar, "as_py") else scalar
        if value:
            return str(value)
    return None
