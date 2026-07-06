# Score Engine Contract

Active score version: `factor-rank-2026-06-30`

## 1. Purpose

The score engine ranks candidates for a 근거 기반 국내 주식 종목 후보 추천 서비스 using deterministic rules and public evidence. It does not call an LLM and does not produce trading advice.

Recommendation means `검토 후보 추천`. Scores are inputs for review, not instructions to trade.

## 2. Engine Output Contract

Every engine score result must include:

- `ticker`
- `as_of`
- `score.version`
- `score.total`
- `score.evidence_level`
- `score.components`
- `missing_data`
- `fallback_data`
- `data_freshness`
- `risk_tags`

Engine JSON example:

```json
{
  "ticker": "005930",
  "as_of": "2026-06-09",
  "score": {
    "version": "factor-rank-2026-06-30",
    "total": 78.5,
    "evidence_level": "strong",
    "components": [
      {
        "name": "financial_stability",
        "weight": 20,
        "raw_score": 82,
        "weighted_score": 16.4,
        "reason": "부채비율과 유동성 지표가 기준 대비 양호합니다.",
        "input_refs": ["fs_005930_2026q1"],
        "evidence_ids": ["ev_20260609_005930_001"],
        "rule_version": "factor-rank-2026-06-30"
      }
    ]
  },
  "missing_data": [],
  "fallback_data": [],
  "data_freshness": {
    "as_of": "2026-06-09",
    "price_as_of": "2026-06-09",
    "financials_as_of": "2026-03-31",
    "disclosures_fetched_at": "2026-06-09T08:00:00Z",
    "news_fetched_at": "2026-06-09T08:30:00Z"
  },
  "risk_tags": []
}
```

Current public candidate and stock score responses expose persisted component
fields only. They include component `name`, `weight`, `raw_score`,
`weighted_score`, `reason`, `input_refs`, and `evidence_ids`, but do not expose
component `rule_version`, `used_fallback`, or score result `fallback_data`.
Those fields remain part of the engine/materializer contract for downstream
persistence work.

## 3. Components And Weights

The score has 8 fixed components. Weights must sum to 100.

| Component | Weight | Direction | Main Inputs | Example Missing Data Key |
| --- | ---: | --- | --- | --- |
| `financial_stability` | 20 | Higher score for lower leverage and stronger equity basis. | liabilities, equity, current liquidity metrics | `financial_stability.inputs` |
| `profitability` | 15 | Higher score for stronger operating and net margins. | operating income, net income, margins | `profitability.inputs` |
| `growth` | 15 | Higher score for stronger revenue and operating income growth. | revenue growth, operating income growth | `growth.inputs` |
| `valuation` | 10 | Higher score for lower valuation multiples against earnings/book proxies. | market cap, earnings, book value proxies | `valuation.inputs` |
| `news_attention` | 10 | Higher score for recent, confident public news evidence. | NAVER news count, recency, normalized attention | `news_attention.inputs` |
| `disclosure_event` | 10 | Higher score for recent, relevant disclosure evidence. | OpenDART disclosure types, recency, materiality rules | `disclosure_event.inputs` |
| `liquidity` | 10 | Higher score for stronger volume, trading value, and market-cap liquidity. | volume, trading value, market cap | `liquidity.inputs` |
| `momentum_volatility` | 10 | Higher score for positive momentum with lower volatility. | recent momentum and volatility metrics | `momentum_volatility.inputs` |

## 4. Calculation Rules

Each component produces:

- `raw_score`: integer or decimal from 0 to 100.
- `weighted_score`: `raw_score * weight / 100`.
- `reason`: neutral human-readable reason.
- `input_refs`: source data row references or provider document references.
- `evidence_ids`: evidence chunks supporting the component.
- `rule_version`: active score rule version.
- `used_fallback`: whether fallback price metrics were used for that component.
- `missing_data`: component-level missing input keys.

Total score:

```text
score.total = round(sum(component.weighted_score), 1)
```

Rules:

- Missing required inputs reduce component confidence and must be listed in `missing_data`.
- If a component has no usable input, set `raw_score` to `null`, `weighted_score` to `0`, and add a `missing_data` entry.
- Do not redistribute missing component weight to other components.
- Persist all 8 components even when some inputs are missing.
- Use `score_version="factor-rank-2026-06-30"` for the score result and each component `rule_version`.
- If fallback price metrics are used, list the component names in `fallback_data`.
- `momentum_volatility` uses the latest KRX `price_metrics.momentum_20d` and
  `price_metrics.volatility_20d`. KRX stock-universe bootstrap and ticker-level
  KRX refresh calculate those fields only after at least 21 real KRX
  close-price rows exist for the ticker.
- Financial statement components must use real `financial_statements` rows.
  Mock or fallback financial rows are excluded; missing real statements should
  remain visible as component `missing_data` instead of being backfilled with
  synthetic values.
- `disclosure_event` uses OpenDART disclosure evidence whose `published_at` is
  on or before the score `as_of` date. This lets an operator fetch historical
  filings after the fact and still materialize the correct historical score.

## 5. Evidence Level

`evidence_level` is derived from evidence quantity and quality.

| Level | Rule |
| --- | --- |
| `strong` | `evidence_count >= 4` and `missing_data` count is `<= 1`. |
| `medium` | `evidence_count >= 2`. |
| `weak` | Any other case. |

## 6. Evidence Gate

A stock can appear in `GET /v1/recommendations/candidates` only when all checks pass:

- At least 2 evidence chunks.
- At least 1 risk signal.
- `data_freshness.as_of` is present.
- `missing_data` is present.

Gate result example:

```json
{
  "passed": true,
  "checks": {
    "min_evidence_count": true,
    "min_risk_count": true,
    "has_data_basis_date": true,
    "has_missing_data_field": true
  },
  "fail_reasons": []
}
```

Failed gate example:

```json
{
  "passed": false,
  "checks": {
    "min_evidence_count": false,
    "min_risk_count": true,
    "has_data_basis_date": true,
    "has_missing_data_field": true
  },
  "fail_reasons": ["evidence_count_below_minimum"]
}
```

## 7. Component Reason Requirements

Reasons must:

- Use neutral review language.
- Reference evidence IDs.
- Mention uncertainty when input data is missing or stale.
- Avoid prohibited user-facing wording:
  - `매수`
  - `매도`
  - `목표가`
  - `진입가`
  - `손절가`
  - `수익 보장`
  - `확실`
  - `무조건`

Reason example:

```json
{
  "reason_id": "rsn_20260609_005930_001",
  "component": "financial_stability",
  "summary": "재무 안정성 지표가 기준 대비 양호해 검토해볼 수 있습니다.",
  "evidence_ids": ["ev_20260609_005930_001"],
  "missing_data": []
}
```

## 8. Rule Version Example

`recommendation_score_rules.formula` example:

```json
{
  "component": "financial_stability",
  "weight": 20,
  "inputs": [
    "total_liabilities",
    "total_equity",
    "total_assets"
  ],
  "normalization": {
    "method": "threshold_band",
    "bands": [
      {
        "metric": "debt_to_equity",
        "lte": 0.5,
        "score": 90
      },
      {
        "metric": "debt_to_equity",
        "lte": 1.0,
        "score": 75
      }
    ]
  },
  "missing_input_behavior": "set_component_weighted_score_to_zero"
}
```

## 9. Validation Checklist

- The component list contains exactly 8 components.
- Component weights sum to 100.
- `score.total` is rounded to one decimal place.
- `missing_data` is always present.
- `data_freshness.as_of` is present for candidate eligibility.
- `fallback_data` is always present, even when empty.
- `risk_tags` is always present, even when empty.
- Candidate list includes only evidence-gate-passing scores.
- No LLM call is made during scoring.
