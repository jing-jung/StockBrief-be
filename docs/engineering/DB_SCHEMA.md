# DB Schema Contract

## 1. Overview

The MVP database targets PostgreSQL 16 compatible schemas. The schema stores Korean stock master data, public source documents, evidence chunks, deterministic score rules, computed scores, recommendation reasons, risk signals, API cache entries, external API logs, and chat history.

Recommendation means `검토 후보 추천`, not trading advice. Database field names must preserve this distinction by using `recommendation_candidate` or `recommendation_reason`, not trading-oriented names.

## 2. Conventions

- Primary keys use UUID unless an external natural key is explicitly listed.
- `ticker` is a 6-digit string and references `stocks.ticker`.
- Timestamps use `timestamptz`.
- Dates use `date`.
- JSON payloads use `jsonb`.
- Soft deletion is not required for MVP unless noted.
- Tables should include `created_at` and `updated_at` when records are mutable.

Fixed score components for `recommendation_score_rules`, `recommendation_scores.component_scores`, and `recommendation_reasons.component`:

| Component | Weight |
| --- | ---: |
| `financial_stability` | 20 |
| `profitability` | 15 |
| `growth` | 15 |
| `valuation` | 10 |
| `news_attention` | 10 |
| `disclosure_event` | 10 |
| `liquidity` | 10 |
| `momentum_volatility` | 10 |

## 3. Core Tables

### stocks

Stores stock master data.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `ticker` | varchar(6) | yes | `005930` | Primary key. |
| `company_name` | text | yes | `삼성전자` | Korean display name. |
| `company_name_en` | text | no | `Samsung Electronics` | English name when available. |
| `market` | text | yes | `KOSPI` | `KOSPI`, `KOSDAQ`, `KONEX`. |
| `sector` | text | no | `반도체` | Normalized sector. |
| `industry` | text | no | `전자부품 제조업` | Detailed industry. |
| `listing_date` | date | no | `1975-06-11` | Listing date. |
| `is_active` | boolean | yes | `true` | Listed and active. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Last update time. |

Example row:

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "company_name_en": "Samsung Electronics",
  "market": "KOSPI",
  "sector": "반도체",
  "industry": "전자부품 제조업",
  "listing_date": "1975-06-11",
  "is_active": true
}
```

### company_identifiers

Maps stock tickers to external provider identifiers.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `provider` | text | yes | `OpenDART` | `OpenDART`, `NAVER`, `KRX`, `ISIN`. |
| `identifier_type` | text | yes | `corp_code` | Provider-specific type. |
| `identifier_value` | text | yes | `00126380` | Provider-specific value. |
| `is_primary` | boolean | yes | `true` | Primary identifier for provider/type. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### financial_statements

Stores normalized financial metrics by reporting period.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `fiscal_year` | integer | yes | `2026` | Fiscal year. |
| `fiscal_period` | text | yes | `Q1` | `Q1`, `Q2`, `Q3`, `FY`. |
| `period_end_date` | date | yes | `2026-03-31` | Reporting period end. |
| `revenue` | numeric | no | `71915600000000` | KRW. |
| `operating_income` | numeric | no | `6606000000000` | KRW. |
| `net_income` | numeric | no | `6755000000000` | KRW. |
| `total_assets` | numeric | no | `470000000000000` | KRW. |
| `total_liabilities` | numeric | no | `105000000000000` | KRW. |
| `total_equity` | numeric | no | `365000000000000` | KRW. |
| `source_document_id` | uuid | no | `...` | FK to `source_documents.id`. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### disclosures

Stores disclosure metadata from OpenDART or compatible providers.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `provider` | text | yes | `OpenDART` | Source provider. |
| `receipt_no` | text | yes | `20260608000123` | Provider document key. |
| `title` | text | yes | `분기보고서` | Disclosure title. |
| `disclosure_type` | text | yes | `periodic_report` | Normalized type. |
| `published_at` | timestamptz | yes | `2026-06-08T09:00:00Z` | Published time. |
| `source_url` | text | no | `https://dart.fss.or.kr/example` | Public URL. |
| `source_document_id` | uuid | no | `...` | FK to `source_documents.id`. |
| `raw_payload` | jsonb | no | `{}` | Provider payload. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### news_items

Stores normalized news metadata from NAVER or compatible providers.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `provider` | text | yes | `NAVER` | Source provider. |
| `title` | text | yes | `삼성전자 반도체 실적 개선 기대` | News title. |
| `summary` | text | no | `기사 요약` | Provider or internal summary. |
| `publisher` | text | no | `Example News` | Publisher name. |
| `published_at` | timestamptz | no | `2026-06-09T01:30:00Z` | Published time. |
| `source_url` | text | yes | `https://news.example.com/item` | Public URL. |
| `sentiment_label` | text | no | `neutral` | `positive`, `neutral`, `negative`. |
| `source_document_id` | uuid | no | `...` | FK to `source_documents.id`. |
| `raw_payload` | jsonb | no | `{}` | Provider payload. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### price_metrics

Stores daily price and liquidity metrics.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `trade_date` | date | yes | `2026-06-09` | Market date. |
| `close_price` | numeric | no | `72000` | KRW. |
| `volume` | numeric | no | `12345678` | Shares. |
| `trading_value` | numeric | no | `888888888000` | KRW. |
| `market_cap` | numeric | no | `430000000000000` | KRW. |
| `change_rate` | numeric | no | `1.23` | Percent. |
| `volatility_20d` | numeric | no | `0.21` | 20-day realized volatility. |
| `momentum_20d` | numeric | no | `0.08` | 20-day return ratio. |
| `source` | text | yes | `KRX` | Provider source. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### source_documents

Stores source-level documents for traceability and evidence creation.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | no | `005930` | Nullable for market-wide docs. |
| `source_type` | text | yes | `disclosure` | `disclosure`, `news`, `financial`, `price`. |
| `source_name` | text | yes | `OpenDART` | Provider name. |
| `source_url` | text | no | `https://dart.fss.or.kr/example` | Public URL. |
| `external_id` | text | no | `20260608000123` | Provider key. |
| `title` | text | yes | `분기보고서` | Document title. |
| `published_at` | timestamptz | no | `2026-06-08T09:00:00Z` | Published time. |
| `fetched_at` | timestamptz | yes | `2026-06-09T08:00:00Z` | Fetch time. |
| `content_hash` | text | no | `sha256:...` | Deduplication hash. |
| `raw_content` | text | no | `...` | Raw or extracted text. |
| `metadata` | jsonb | no | `{}` | Provider metadata. |

### evidence_chunks

Stores evidence snippets used by score reasons and AI citations.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `evidence_id` | text | yes | `ev_20260609_005930_001` | Public stable ID. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `source_document_id` | uuid | yes | `...` | FK to `source_documents.id`. |
| `evidence_type` | text | yes | `financial_stability` | Usually score component or risk type. |
| `chunk_text` | text | yes | `재무 안정성 판단 근거...` | Short excerpt. |
| `source_url` | text | no | `https://dart.fss.or.kr/example` | Public citation URL. |
| `published_at` | timestamptz | no | `2026-06-08T09:00:00Z` | Source published time. |
| `fetched_at` | timestamptz | yes | `2026-06-09T08:00:00Z` | Source fetched time. |
| `confidence` | numeric | yes | `0.82` | 0 to 1. |
| `metadata` | jsonb | no | `{}` | Additional extraction data. |

Example row:

```json
{
  "evidence_id": "ev_20260609_005930_001",
  "ticker": "005930",
  "source_document_id": "00000000-0000-0000-0000-000000000001",
  "evidence_type": "financial_stability",
  "chunk_text": "재무 안정성 판단에 사용된 공개 공시 요약입니다.",
  "source_url": "https://dart.fss.or.kr/example",
  "confidence": 0.82
}
```

### recommendation_score_rules

Stores score rule versions and component formulas.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `rule_version` | text | yes | `score-rules-2026-06-01` | Version key. |
| `component` | text | yes | `financial_stability` | One of 8 score components. |
| `weight` | integer | yes | `20` | Fixed weight. |
| `formula` | jsonb | yes | `{}` | Machine-readable scoring rule. |
| `description` | text | yes | `부채비율, 유동비율 기반 점수` | Human-readable rule. |
| `is_active` | boolean | yes | `true` | Active rule flag. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### recommendation_scores

Stores deterministic score outputs by ticker and date.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `as_of_date` | date | yes | `2026-06-09` | Score basis date. |
| `score_version` | text | yes | `score-rules-2026-06-01` | Score rule version. |
| `total_score` | numeric | yes | `78.5` | 0 to 100. |
| `evidence_level` | text | yes | `strong` | `strong`, `moderate`, `limited`, `insufficient`. |
| `component_scores` | jsonb | yes | `[]` | 8 component outputs. |
| `evidence_count` | integer | yes | `4` | Evidence count. |
| `missing_data` | jsonb | yes | `[]` | Required array. |
| `data_freshness` | jsonb | yes | `{}` | Required freshness object. |
| `is_candidate_eligible` | boolean | yes | `true` | Evidence gate result. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### recommendation_reasons

Stores human-readable reasons linked to scores and evidence.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `reason_id` | text | yes | `rsn_20260609_005930_001` | Public stable ID. |
| `recommendation_score_id` | uuid | yes | `...` | FK to `recommendation_scores.id`. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `component` | text | yes | `financial_stability` | Score component. |
| `summary` | text | yes | `재무 안정성 지표가 기준 대비 양호합니다.` | Neutral reason text. |
| `evidence_ids` | jsonb | yes | `["ev_20260609_005930_001"]` | Public evidence IDs. |
| `source_document_ids` | jsonb | yes | `["00000000-0000-0000-0000-000000000001"]` | Source document IDs for traceability. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### risk_signals

Stores normalized risk signals shown as `risk_tags`.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `as_of` | date | yes | `2026-06-09` | Risk basis date. |
| `risk_tag` | text | yes | `high_volatility` | Normalized risk tag. |
| `severity` | text | yes | `medium` | `low`, `medium`, `high`. |
| `penalty_points` | numeric | yes | `3.5` | Score deduction or risk penalty indicator. |
| `display_text` | text | yes | `최근 변동성 확대가 확인됩니다.` | User-facing neutral risk copy. |
| `description` | text | yes | `최근 변동성 확대가 확인됩니다.` | Neutral risk explanation. |
| `evidence_ids` | jsonb | yes | `["ev_20260609_005930_002"]` | Evidence IDs. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### api_cache_entries

Stores provider response caches.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `cache_key` | text | yes | `opendart:corp:00126380:2026Q1` | Unique cache key. |
| `provider` | text | yes | `OpenDART` | Provider. |
| `request_hash` | text | yes | `sha256:...` | Request hash. |
| `response_payload` | jsonb | yes | `{}` | Cached response. |
| `status_code` | integer | no | `200` | Provider HTTP status. |
| `expires_at` | timestamptz | no | `2026-06-10T09:00:00Z` | Cache expiry. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

### external_api_call_logs

Stores external API request logs without secrets.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `provider` | text | yes | `NAVER` | Provider. |
| `endpoint` | text | yes | `/v1/search/news` | Sanitized endpoint path. |
| `method` | text | yes | `GET` | HTTP method. |
| `request_params` | jsonb | no | `{"query":"005930"}` | Must not include secrets. |
| `status_code` | integer | no | `200` | HTTP status. |
| `duration_ms` | integer | no | `240` | Request duration. |
| `error_code` | text | no | `rate_limited` | Error code. |
| `called_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Request time. |

### ingestion_runs

Stores ingestion execution state so provider jobs can be retried or replayed
without duplicate writes.

Replay policy:

- `run_id` is unique and protects explicit reruns of the same ledger row.
- `input_hash` is the normalized provider, ticker, source date, and request
  parameter hash. A succeeded run with the same `input_hash` is replayed even
  when a later request uses a different explicit `run_id`.
- An active run with the same `input_hash` blocks another run until the first
  run reaches a terminal status.
- A partial unique index on active or succeeded `input_hash` values prevents
  concurrent first-run workers from creating multiple active ledger rows for the
  same normalized input.

Before applying migration `0004_ingestion_input_hash_guard`, confirm that the
target database has no existing duplicate active/succeeded input hashes:

```sql
select input_hash, count(*) as duplicate_count
from ingestion_runs
where status in ('started', 'succeeded')
group by input_hash
having count(*) > 1;
```

If this query returns rows, resolve the duplicate ledger rows before running the
migration. The migration intentionally fails with an explicit message instead
of creating the index over ambiguous existing data.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `run_id` | text | yes | `opendart-20260618-005930` | Public stable run key. |
| `job_type` | text | yes | `disclosure` | `disclosure`, `news`, `price`, `financial`, `score`. |
| `provider` | text | yes | `OpenDART` | Provider or source name. |
| `target_scope` | jsonb | yes | `{"ticker":"005930"}` | Ticker, date range, or batch scope. |
| `status` | text | yes | `started` | `started`, `succeeded`, `partial_failed`, `failed`, `replayed`. |
| `input_hash` | text | yes | `sha256:...` | Hash of normalized job input. |
| `started_at` | timestamptz | yes | `2026-06-18T09:00:00Z` | Run start time. |
| `completed_at` | timestamptz | no | `2026-06-18T09:01:00Z` | Run completion time. |
| `result_counts` | jsonb | yes | `{"inserted":1,"updated":2}` | Insert, update, skip, and error counts. |
| `error_summary` | jsonb | no | `{"code":"rate_limited"}` | Sanitized error summary. |
| `created_at` | timestamptz | yes | `2026-06-18T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-18T09:01:00Z` | Last state change. |

Provider upsert keys:

| Provider data | Upsert key |
| --- | --- |
| OpenDART disclosure | `provider + receipt_no` |
| NAVER news | `provider + source_url_hash` or provider article id |
| KRX price | `ticker + trade_date + source` |
| Financial statement | `ticker + fiscal_year + fiscal_period + source_document_id` |
| Source document | `source_type + source_name + external_id`, fallback `content_hash` |
| Score | `ticker + as_of_date + score_version` |

Provider ingestion stores raw payload archive references, when available, in
`source_documents.metadata.raw_archive_uri` and each normalized row's
`raw_payload.raw_archive_uri`. The S3 object is the immutable raw provider
payload; RDS rows are the normalized query surface.

### chat_sessions

Stores guest chat sessions.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `session_id` | text | yes | `chat_20260609_001` | Public session key. |
| `ticker` | varchar(6) | no | `005930` | Optional focus ticker. |
| `candidate_as_of` | date | no | `2026-06-09` | Candidate basis date. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Last message time. |

### chat_messages

Stores user and assistant chat messages with safety metadata.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `message_id` | text | yes | `msg_20260609_001` | Public message key. |
| `session_id` | text | yes | `chat_20260609_001` | FK-like reference to `chat_sessions.session_id`. |
| `role` | text | yes | `assistant` | `user`, `assistant`, `system`. |
| `content` | text | yes | `공개 데이터 기준으로 설명합니다.` | Message text. |
| `ticker` | varchar(6) | no | `005930` | Optional focus ticker. |
| `citations` | jsonb | yes | `[]` | Evidence IDs and source URLs. |
| `safety_flags` | jsonb | yes | `[]` | Refusal or policy flags. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |

## 4. Recommended Constraints And Indexes

- `stocks.ticker` primary key.
- Unique `company_identifiers(provider, identifier_type, identifier_value)`.
- Unique `financial_statements(ticker, fiscal_year, fiscal_period)`.
- Unique `disclosures(provider, receipt_no)`.
- Unique `news_items(source_url)`.
- Unique `price_metrics(ticker, trade_date)`.
- Unique `evidence_chunks.evidence_id`.
- Unique `recommendation_score_rules(rule_version, component)`.
- Unique `recommendation_scores(ticker, as_of_date, score_version)`.
- Unique `recommendation_reasons.reason_id`.
- Unique `api_cache_entries.cache_key`.
- Unique `ingestion_runs.run_id`.
- Unique partial `ingestion_runs(input_hash)` for `started` and `succeeded`
  rows.
- Unique `chat_sessions.session_id`.
- Unique `chat_messages.message_id`.
- Index `recommendation_scores(as_of_date, is_candidate_eligible, total_score desc)`.
- Index `evidence_chunks(ticker, evidence_type)`.
- Index `evidence_chunks(ticker, published_at)` for the actual candidate
  evidence summary and evidence listing paths filtered by ticker and ordered or
  aggregated by source publication time.
- Index `source_documents(source_type, id, published_at)` for the joined
  evidence listing and candidate summary source-type filter.
- Index `risk_signals(ticker, as_of)`.
- Index `chat_messages(session_id)` for authenticated chat session detail reads.
- Index `ingestion_runs(job_type, provider, status)`.
- Index `ingestion_runs(started_at)`.
- Index `news_items(ticker, published_at)` for normalized news item lookups by
  ticker and publication time.
- Index `disclosures(ticker, published_at)` for normalized disclosure lookups by
  ticker and publication time.

## 5. Recommendation Score JSON Example

`recommendation_scores.component_scores` example:

```json
[
  {
    "name": "financial_stability",
    "weight": 20,
    "raw_score": 82,
    "weighted_score": 16.4,
    "input_refs": ["fs_005930_2026q1"],
    "rule_version": "score-rules-2026-06-01"
  },
  {
    "name": "profitability",
    "weight": 15,
    "raw_score": 76,
    "weighted_score": 11.4,
    "input_refs": ["fs_005930_2026q1"],
    "rule_version": "score-rules-2026-06-01"
  }
]
```

`recommendation_scores.data_freshness` example:

```json
{
  "as_of": "2026-06-09",
  "price_as_of": "2026-06-09",
  "financials_as_of": "2026-03-31",
  "disclosures_fetched_at": "2026-06-09T08:00:00Z",
  "news_fetched_at": "2026-06-09T08:30:00Z"
}
```

## 6. P1 Account Tables

P1 adds server-side account state while preserving the guest-first MVP. Passwords are stored only by AWS Cognito and must never be stored in the StockBrief database.

### users

Stores the minimal local user profile mapped from Cognito claims.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `cognito_sub` | text | yes | `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` | Unique user key from Cognito JWT `sub`. |
| `email` | text | no | `user@example.com` | Minimal PII copied from verified claims. |
| `email_verified` | boolean | yes | `true` | Copied from Cognito claim. |
| `nickname` | text | no | `researcher` | Optional display name. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Last update time. |

Constraints:

- Unique `users.cognito_sub`.
- No `password`, `password_hash`, or refresh token columns.

### user_preferences

Stores server-side preference JSON for logged-in users.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `user_id` | uuid | yes | `...` | FK to `users.id`, cascade delete. |
| `preferences` | jsonb | yes | `{"risk_profile":"balanced"}` | Product preference object. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Last update time. |

Constraints:

- Unique `user_preferences.user_id`.

### watchlists

Stores server-side 관심종목 for logged-in users.

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | yes | `...` | Primary key. |
| `user_id` | uuid | yes | `...` | FK to `users.id`, cascade delete. |
| `ticker` | varchar(6) | yes | `005930` | FK to `stocks.ticker`. |
| `name` | text | yes | `삼성전자` | Snapshot display name. |
| `market` | varchar(20) | yes | `KOSPI` | Snapshot market. |
| `sector` | text | no | `반도체` | Snapshot sector. |
| `memo` | text | no | `공개 데이터 기준 검토 메모` | Optional user memo. |
| `saved_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | User save time. |
| `created_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Inserted time. |
| `updated_at` | timestamptz | yes | `2026-06-09T09:00:00Z` | Last update time. |

Constraints:

- Unique `watchlists(user_id, ticker)` to prevent duplicate ticker saves per user.
- Index `watchlists(user_id, saved_at)`.

### chat_sessions P1 link

P1 adds nullable columns to `chat_sessions`:

| Field | Type | Required | Example | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | uuid | no | `...` | FK to `users.id`, `SET NULL` on delete. Guest sessions remain possible. |
| `title` | text | no | `삼성전자 설명` | Optional session title. |

Index:

- `chat_sessions(user_id, updated_at)`.
