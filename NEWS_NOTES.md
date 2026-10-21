# ZEC public news panel

The dashboard reads `zec_news.json` independently of paper trading. Six public
RSS sources cover official releases, media, and English/Chinese Google News
searches. This is a best-effort aggregation, not exhaustive internet coverage.
Google links lead to the aggregator and then the publisher. Only headlines and
metadata are retained; no full articles are copied.

Only dated items in the last seven days are accepted. Duplicate normalized
titles are collapsed; different headlines about the same event can remain.
Publication time is shown in Beijing time. Event occurrence time is unknown
unless the reader verifies the original. Direction is a conservative keyword
heuristic on the headline, not an AI fact check or price forecast. Uncertain or
conflicting words are labelled pending verification. Media are not independently
verified; official releases retain their source label.

Each source has a timeout and health status. Failed sources keep their previous
items within seven days. The JSON is atomically replaced. The panel warns after
two hours without a recent check and on partial/total failures. A successful
empty feed is distinguished from a failed source. No code imports or modifies
paper-trading state, thresholds, positions, or logs.

`.github/workflows/zec_news.yml` supports manual execution on the chosen branch
and hourly scheduling on GitHub's default branch when installed there. This
feature branch is not automatically deployed or scheduled. Publishing the live
Pages site and changing the default branch are separate actions.

Validation: `python -m unittest discover -s tests -v`,
`node tests/test_dashboard.cjs`, and `node tests/test_news.cjs`.
