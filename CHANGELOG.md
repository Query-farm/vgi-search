# Changelog

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
