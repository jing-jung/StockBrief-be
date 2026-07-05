# Recommendation Quality Criteria

This guide defines the minimum quality bar for a StockBrief recommendation
candidate. It is an operating checklist, not a trading signal.

Recommendation means `검토 후보 추천`. A candidate can help a user decide what to
review next, but it must not tell the user to buy, sell, enter a position, set a
target price, or expect a guaranteed result.

## Quality Bar

A deployed candidate flow is healthy when all checks pass:

| Area | Required Signal | Why It Matters |
| --- | --- | --- |
| Candidate list | `/v1/recommendations/candidates` returns at least one item. | The FE home and explore views have data to render from the canonical recommendation contract. |
| Expected candidates | PR-specific expected tickers appear in the candidate list when `--expected-ticker` is used. | Product-flow checks can prove that representative names still surface, not only that any candidate exists. |
| Evidence count | Each listed item has at least two public evidence records. | A candidate should not be shown from a single weak signal. |
| Freshness | Candidate list items include `data_freshness.live_evidence_latest_at` or legacy `evidence_summary.latest_at`; detail includes `data_freshness.as_of`. | Users need to know the data basis. |
| Score components | Detail includes the 8 fixed score components and component weights sum to 100. | The FE score breakdown and score-agent contract must stay aligned. |
| Detail contract | `/v1/recommendations/candidates/{ticker}` includes `evidence_level`, `evidence_count`, `score_components`, `missing_data`, `risk_tags`, and `recommendation_reasons`. | FE detail and AI explanation need the same source of truth. |
| Risk context | Detail has at least one risk tag. | A candidate without risk context is incomplete. |
| Evidence source | `/v1/stocks/{ticker}/evidence` returns source type and source name for every item. Public provider evidence also includes URL and published timestamp; internal score evidence includes `metadata.source_identifier` and `metadata.as_of_date`. | Users must be able to inspect or trace the basis. |

If provider egress is paused for cost control, the quality smoke can still pass
using the latest stored evidence. Live ingestion reactivation is a separate
operation and requires the ingestion runbook gates.

## Smoke Command

Run this after FE-BE connection changes, ingestion evidence changes, or before
resuming product-flow work:

```bash
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com" \
  uv run python scripts/check_recommendation_quality_smoke.py \
    --limit 3 \
    --max-detail-tickers 3
```

Use `--ticker 005930` when a PR needs to prove one specific candidate only. If
`--ticker` is omitted, the helper selects up to `--max-detail-tickers` tickers
from the candidate list and checks each selected candidate's detail and evidence
contracts.

Use `--expected-ticker` when a PR needs to prove that a representative ticker is
still present in the candidate list. Repeat the option for multiple tickers:

```bash
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com" \
  uv run python scripts/check_recommendation_quality_smoke.py \
    --limit 5 \
    --max-detail-tickers 3 \
    --expected-ticker 005930 \
    --expected-ticker 000660
```

Expected tickers that appear in the list are checked first for detail and
evidence. If an expected ticker is missing, the helper returns
`expected_candidate_ticker_missing` without printing raw provider text.

The script calls:

- `GET /v1/recommendations/candidates`
- `GET /v1/recommendations/candidates/{ticker}` for each selected ticker
- `GET /v1/stocks/{ticker}/evidence` for each selected ticker

The output is redacted by design. It reports counts, basis dates, source type
coverage, score component count/weight coverage, provider URL/date coverage,
internal score source identifier coverage, and structured blocker codes. It
does not print raw provider bodies, full news text, user tokens, or private
account data.

## Interpreting Failures

| Blocker | Meaning | First Check |
| --- | --- | --- |
| `candidate_list_empty` | No candidates are available. | Check seed/live score rows and candidate eligibility. |
| `expected_candidate_ticker_missing` | One or more PR-specific expected tickers did not appear in the candidate list. | Check candidate eligibility, score materialization, and the requested `limit`. |
| `candidate_evidence_below_minimum` | A list item has fewer than two evidence records. | Check ingestion status and evidence joins. |
| `missing_candidate_latest_at` | Candidate summary has no latest evidence timestamp. | Check `evidence_summary` aggregation. |
| `detail_evidence_below_minimum` | Detail has too little evidence. | Check `recommendation_scores.evidence_count`. |
| `missing_risk_tags` | Detail omitted the required `risk_tags` field. | Check API response model, serializer, and `risk_signals` join. |
| `risk_tags_not_array` | Detail contract no longer returns `risk_tags` as an array. | Check API response model and serializer. |
| `missing_data_not_array` | Detail contract no longer returns `missing_data` as an array. | Check API response model and serializer. |
| `missing_data_freshness_as_of` | Detail has no basis date. | Check score freshness fields. |
| `missing_recommendation_reasons` | Detail cannot explain why the candidate appears. | Check reason generation and evidence linkage. |
| `score_component_count_mismatch` | Detail does not expose all 8 fixed score components. | Check score materialization and API serialization. |
| `score_components_missing` | One or more expected score components are absent. | Check score engine output and persisted component names. |
| `score_component_weight_mismatch` | A component weight no longer matches the score contract. | Check `SCORE_ENGINE.md` and score serialization. |
| `score_component_weight_sum_mismatch` | Component weights do not sum to 100. | Check score engine output and migrated score rows. |
| `evidence_items_below_minimum` | Evidence tab has too few records. | Check `/v1/stocks/{ticker}/evidence`. |
| `evidence_item_not_object` | Evidence response contains a malformed item. | Check API response serialization. |
| `evidence_item_missing_source_metadata` | A specific evidence item lacks required source metadata. Public provider evidence requires `source_type`, `source_name`, `url`, and `published_at`; internal score evidence requires `source_type`, `source_name`, `metadata.source_identifier`, and `metadata.as_of_date`. | Check source document normalization, provider date parsing, or internal score evidence metadata. |

## Release Note Template

Use this short summary in PRs or issue comments:

```text
Recommendation quality smoke:
- candidate list: pass, count=<n>, first_ticker=<ticker>
- expected_tickers=<ticker_a>,<ticker_b>, missing_expected_tickers=[]
- selected_tickers=<ticker_a>,<ticker_b>,<ticker_c>
- candidate detail: pass for selected tickers, evidence_count>=<n>, risk_tag_count>=1
- score components: component_count=8, component_weight_sum=100
- stock evidence: pass for selected tickers, evidence_count>=<n>, source_types=<types>
- provider evidence: url_coverage=<n>/<n>, published_at_coverage=<n>/<n>
- internal score evidence: source_identifier_coverage=<n>/<n>, as_of_date_coverage=<n>/<n>
- remaining blockers: none
```
