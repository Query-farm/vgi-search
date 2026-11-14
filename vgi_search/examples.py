"""Composed example queries that combine more than one of the worker's objects.

Each example is defined once here and referenced from every place that publishes
it: the schema-level ``vgi.example_queries`` list (``search_worker.py``) and the
example list of the object that drives the query (``web_answer`` /
``web_search``), so the copies can never drift apart. Both use the free, keyless
DuckDuckGo (``ddg``) provider so they run without any API key.
"""

from __future__ import annotations

from vgi.metadata import FunctionExample

# web_answer drives: the synthesized answer is the point, and web_search supplies
# the ranked URLs to cite next to it -- one grounding row for a RAG prompt.
GROUNDED_ANSWER_EXAMPLE = FunctionExample(
    sql=(
        "SELECT search.main.web_answer('python programming language', 'ddg') AS answer, "
        "list(url ORDER BY rank) AS sources "
        "FROM search.main.web_search('python programming language', provider := 'ddg', count := 5)"
    ),
    description=(
        "One grounding row: DuckDuckGo's Instant Answer summary for a topic, plus the URLs of the "
        "top 5 web_search results for the same query (in rank order) to cite alongside it"
    ),
)

# web_search drives: each related-topic hit is expanded with web_answer on its
# title (a scalar over the table function's rows).
TOPIC_DEFINITIONS_EXAMPLE = FunctionExample(
    sql=(
        "SELECT rank, title, search.main.web_answer(title, 'ddg') AS definition "
        "FROM search.main.web_search('python programming language', provider := 'ddg', count := 6) "
        "WHERE extra->>'result_kind' = 'related_topic' "
        "ORDER BY rank"
    ),
    description=(
        "Expand a search into definitions: for each DuckDuckGo related-topic result, look up the "
        "Instant Answer summary of its title (NULL where DuckDuckGo has none, e.g. category pages)"
    ),
)

COMPOSED_EXAMPLES = [GROUNDED_ANSWER_EXAMPLE, TOPIC_DEFINITIONS_EXAMPLE]
