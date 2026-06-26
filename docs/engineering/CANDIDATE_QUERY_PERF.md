# Candidate query performance checks

This note tracks the manual PostgreSQL verification for `/v1/stocks/candidates`
query plans. SQLite unit tests cover SQLAlchemy statement structure in CI, while
PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` should be run against a representative
database before closing performance follow-ups.

## Scope

The candidate API runs four query groups:

- `total`: count eligible candidates for pagination.
- `as_of`: max candidate score date for response metadata.
- `items`: fetch the requested page with the selected sort.
- `details`: bulk-load latest price and evidence summary for returned tickers.

The `total` and `as_of` aggregate queries must not scan `price_metrics`.
The `score_desc` and `updated_desc` item queries must not access `price_metrics`.
The `volume_desc` item query may access `price_metrics` because volume is a
global ordering key and must be evaluated before `LIMIT/OFFSET`.

## CI regression coverage

`tests/test_stock_evidence_api.py` verifies the ORM statement structure without
depending on SQLAlchemy-generated alias names:

- `test_stock_candidate_aggregate_queries_skip_price_metric_join`
- `test_stock_candidate_score_and_updated_sorts_skip_price_metric_join`
- `test_stock_candidate_volume_sort_uses_price_metric_for_global_ordering`

These tests inspect SQLAlchemy `Select` objects for `price_metrics` table
references instead of matching compiled SQL strings.

## PostgreSQL plan procedure

Run this against a non-production PostgreSQL database that has representative
candidate and price history volume.

1. Seed or load representative data.
2. Enable timing in the SQL client.
3. Run each query with `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)`.
4. Record execution time, shared buffer reads/hits, and scanned row counts.
5. Attach the result to the related PR or issue before closing the performance
   follow-up.

The repository includes a helper that builds the current SQLAlchemy candidate
queries instead of copying SQL by hand. By default it does not connect to a
database and only prints the PostgreSQL `EXPLAIN` statements:

```bash
.venv/bin/python scripts/check_candidate_query_perf.py
```

Offline mode intentionally compiles the current `CandidateService` private query
builders with a statement-only session. Keep those builders session-independent;
if a builder starts reading from `self.session`, move query construction to a
dedicated helper or use the `--execute` path with a real PostgreSQL session.

To run the actual PostgreSQL plans, configure the same `DATABASE_URL` or
`DATABASE_SECRET_ARN` settings used by the backend and pass `--execute`.
`--execute` runs five `EXPLAIN ANALYZE` candidate queries, so use staging or an
approved low-traffic window for shared environments. The JSON output
intentionally omits database URLs and secret values; verify the saved report
contains only SQL text and query plans before attaching it to an issue or PR.

```bash
.venv/bin/python scripts/check_candidate_query_perf.py \
  --execute \
  --output /tmp/stockbrief-candidate-query-perf.json
```

Recommended query groups:

```sql
-- total
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*)
FROM (
  SELECT stocks.ticker, recommendation_scores.as_of_date
  FROM stocks
  JOIN recommendation_scores ON recommendation_scores.ticker = stocks.ticker
  LEFT OUTER JOIN (
    SELECT risk_signals.ticker, risk_signals.as_of_date, count(risk_signals.id) AS risk_count
    FROM risk_signals
    GROUP BY risk_signals.ticker, risk_signals.as_of_date
  ) AS risk_counts
    ON risk_counts.ticker = stocks.ticker
   AND risk_counts.as_of_date = recommendation_scores.as_of_date
  WHERE recommendation_scores.is_candidate_eligible IS TRUE
    AND recommendation_scores.evidence_count >= 2
    AND recommendation_scores.missing_data IS NOT NULL
    AND recommendation_scores.data_freshness IS NOT NULL
    AND recommendation_scores.data_freshness ->> 'as_of' IS NOT NULL
) AS candidate_index;

-- as_of
EXPLAIN (ANALYZE, BUFFERS)
SELECT max(candidate_index.as_of_date)
FROM (
  SELECT stocks.ticker, recommendation_scores.as_of_date
  FROM stocks
  JOIN recommendation_scores ON recommendation_scores.ticker = stocks.ticker
  LEFT OUTER JOIN (
    SELECT risk_signals.ticker, risk_signals.as_of_date, count(risk_signals.id) AS risk_count
    FROM risk_signals
    GROUP BY risk_signals.ticker, risk_signals.as_of_date
  ) AS risk_counts
    ON risk_counts.ticker = stocks.ticker
   AND risk_counts.as_of_date = recommendation_scores.as_of_date
  WHERE recommendation_scores.is_candidate_eligible IS TRUE
    AND recommendation_scores.evidence_count >= 2
    AND recommendation_scores.missing_data IS NOT NULL
    AND recommendation_scores.data_freshness IS NOT NULL
    AND recommendation_scores.data_freshness ->> 'as_of' IS NOT NULL
) AS candidate_index;
```

For item queries, capture plans for:

- `score_desc`: order by adjusted score for `risk_profile=balanced`.
- `updated_desc`: order by `recommendation_scores.as_of_date DESC`.
- `volume_desc`: order by latest `price_metrics.volume DESC`.

## Result log

No PostgreSQL `EXPLAIN` result is recorded yet because the project dev RDS is not
provisioned and a representative PostgreSQL dataset has not been selected.

| Date | Environment | Candidate rows | Price rows | Sort | Execution time | Buffers | Notes |
| --- | --- | ---: | ---: | --- | ---: | --- | --- |
| TBD | TBD | TBD | TBD | total | TBD | TBD | Must not scan `price_metrics`. |
| TBD | TBD | TBD | TBD | as_of | TBD | TBD | Must not scan `price_metrics`. |
| TBD | TBD | TBD | TBD | score_desc | TBD | TBD | Must not scan `price_metrics`. |
| TBD | TBD | TBD | TBD | updated_desc | TBD | TBD | Must not scan `price_metrics`. |
| TBD | TBD | TBD | TBD | volume_desc | TBD | TBD | May scan latest price volume for global ordering. |
