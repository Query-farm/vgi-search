# Changelog

## 0.4.1 — 2026-11-14

- The keyed Tavily `web_answer` example now says it needs a `tavily` secret and
  returns NULL without one, so a keyless run of the example is not mistaken for
  a broken answer (vgi-lint VGI913).
