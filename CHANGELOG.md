# Changelog

## 0.5.0 (2026-11-25)

- Speaks VGI protocol 3.4: require vgi-python `>=0.55.1,<0.56`; uv.lock re-locked. This release
  needs Haybarn 2.0.1 (or stock DuckDB 2.0 with `INSTALL vgi FROM query_farm`): Haybarn 2.0.0
  loads a 3.3 `vgi` extension that cannot attach it.
- SQL tests run on haybarn-unittest 2.0.1rc3 (CI and Makefile).
- New `serve` extra (`vgi-python[http,oauth]`); the Docker image installs it, so the image runs in
  Orchard hosted mode (`VGI_ORCHARD_HOSTED=1` with VGI_JWT_ISSUER, VGI_JWT_AUDIENCE,
  VGI_JWT_JWKS_URI and VGI_SIGNING_KEY). The Docker HEALTHCHECK uses `HEAD /health`, which
  stays anonymous in hosted mode.

## 0.4.2 (2026-11-16)

- Bump vgi-python to 0.52.2.
- Requires Python 3.14 (CI, Docker image and tooling moved to 3.14).

## 0.4.1 — 2026-11-14

- The worker's own ruff run excludes `notebooks/` (`[tool.ruff] extend-exclude`).
  The notebooks are maintained by separate tooling with its own lint config, and
  CI's repo-wide `ruff format --check .` was failing on them.
- The keyed Tavily `web_answer` example now says it needs a `tavily` secret and
  returns NULL without one, so a keyless run of the example is not mistaken for
  a broken answer (vgi-lint VGI913).
