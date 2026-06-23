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

_KEY_FIELDS = ("api_key", "key", "token", "value", "secret", "secret_string")


def key_from_secret(secrets: dict[str, dict[str, Any]], provider: str) -> str | None:
    """Return the API key for ``provider`` from resolved secrets, or ``None``.

    ``secrets`` is the resolved mapping from ``ProcessParams.secrets`` /
    ``SecretsAccessor.to_dict()`` (values are ``pa.Scalar``). Looks up the
    secret type named after the provider and reads the first present key field.
    """
    entry = secrets.get(provider)
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
