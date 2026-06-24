"""Shared per-object discovery/description metadata for the ``vgi-lint`` strict profile.

Every function and table the worker exposes must surface a consistent set of
discovery tags so agents and humans can find and understand it. The
``vgi-lint-check`` strict profile (0.26.0) gates on:

- ``vgi.title`` (VGI124)        -- human-friendly display name (must not
  normalize-equal the machine name; carry an extra word).
- ``vgi.doc_llm`` (VGI112)      -- a Markdown narrative aimed at an LLM/agent
  (what it does, when to use it, inputs/outputs, edge cases).
- ``vgi.doc_md`` (VGI113)       -- a Markdown narrative for human docs (overview
  + usage + notes); DISTINCT content from ``vgi.doc_llm``.
- ``vgi.keywords`` (VGI126)     -- comma-separated search terms/synonyms.
- ``vgi.source_url`` (VGI128)   -- link to the implementing source file.

``source_url(path)`` builds the canonical GitHub blob URL so every object points
at exactly where it lives.
"""

from __future__ import annotations

# Base GitHub blob URL for source files in this repo (pinned to ``main``).
SOURCE_BASE = "https://github.com/Query-farm/vgi-search/blob/main"


def source_url(relative_path: str) -> str:
    """Build the implementation ``vgi.source_url`` for a repo-relative file.

    Args:
        relative_path: Path relative to the repository root,
            e.g. ``"vgi_search/scalars.py"``.

    Returns:
        The canonical GitHub blob URL for that file on ``main``.
    """
    return f"{SOURCE_BASE}/{relative_path}"


def object_tags(
    *,
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: str,
    relative_path: str,
) -> dict[str, str]:
    """Build the five standard per-object discovery/description tags.

    Args:
        title: Human-friendly display name (VGI124).
        doc_llm: Markdown narrative for an LLM/agent audience (VGI112).
        doc_md: Markdown narrative for human docs (VGI113); distinct from ``doc_llm``.
        keywords: Comma-separated search terms/synonyms (VGI126).
        relative_path: Implementing file relative to the repository root (VGI128).

    Returns:
        A tag mapping ready to merge into a function's ``Meta.tags``.
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": keywords,
        "vgi.source_url": source_url(relative_path),
    }
