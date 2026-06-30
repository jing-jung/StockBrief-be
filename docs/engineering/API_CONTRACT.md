# API Contract

This document is the canonical StockBrief public API contract for the
`factor-rank-2026-06-30` score contract.

The API serves mock/seed data only in this sprint. OpenDART, NAVER, KRX,
Bedrock, and RAG ingestion adapters must keep the same response shape when
real data is attached later.

## 1. Base URL

Local backend:

```text
http://localhost:8000/v1
```

Frontend environment variable:

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1
```

All public API paths start with `/v1`.

## 2. Common Response

`GET /v1/health` is the only public endpoint that returns a plain health
object. All other public success responses use this envelope:

```json
{
  "success": true,
  "data": {},
  "message": "요청이 성공적으로 처리되었습니다.",
  "request_id": "req_..."
}
```

Common error response:

```json
{
  "success": false,
  "error": {
    "code": "STOCK_NOT_FOUND",
    "message": "Stock not found.",
    "details": null
  },
  "request_id": "req_..."
}
```

Supported sprint error codes:

| HTTP | Code |
| --- | --- |
| `400` | `INVALID_REQUEST` |
| `404` | `STOCK_NOT_FOUND` |
| `408` | `UPSTREAM_TIMEOUT` |
| `429` | `RATE_LIMITED` |
| `500` | `INTERNAL_ERROR` |
| `503` | `SERVICE_UNAVAILABLE` |

List responses use common pagination:

```json
{
  "limit": 20,
  "offset": 0,
  "total": 30,
  "has_more": true
}
```

## 3. Public Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/health` | Runtime health metadata |
| `GET` | `/v1/stocks/search` | Search seed/mock stocks |
| `GET` | `/v1/stocks/candidates` | List deterministic mock candidates |
| `GET` | `/v1/stocks/{ticker}` | Stock detail for the detail page |
| `GET` | `/v1/stocks/{ticker}/evidence` | Evidence for tabs and chat citations |
| `POST` | `/v1/chat` | Deterministic mock Agent/RAG answer |

Legacy/internal recommendation engine endpoints remain available for backend
compatibility:

- `GET /v1/recommendations/candidates`
- `GET /v1/recommendations/candidates/{ticker}`
- `GET /v1/stocks/{ticker}/score`

New frontend work should prefer the public endpoints in the table above.

Score-backed candidate and score endpoints use stored deterministic scores.
The current mock seed baseline stores `mock-score-rules-2026-06-09` in public
`score.version` fields. After score materialization is connected, newly
materialized scores must store `factor-rank-2026-06-30` in the same field.

Candidate score contract fields:

- `recommendation_score`: total score from `0` to `100`.
- `score_components`: exactly 8 component score records when all persisted
  component data is available. Each component includes `name`, `weight`,
  `raw_score`, `weighted_score`, `reason`, `input_refs`, and `evidence_ids`.
- `evidence_count`: distinct evidence item count used by the score.
- `evidence_level`: `strong`, `medium`, or `weak`.
- `missing_data`: missing input keys. Present even when empty.
- `data_freshness`: freshness metadata, including `as_of`.
- `risk_tags`: risk signal tags associated with the same ticker and score date.

Future materialized score fields:

- `fallback_data`: fallback component names from the score engine contract.
  Downstream persistence must preserve it when score materialization starts.
- Component `rule_version`: the score engine emits this internally, but the
  current public component response does not expose it.
- Score result `score_version`: the score engine emits this internally, while
  the current public API exposes persisted score version as `score.version`.

## 4. GET /health

Response:

```json
{
  "status": "ok",
  "service": "stockbrief-api",
  "version": "0.1.0"
}
```

## 5. GET /stocks/search

Query:

| Name | Required | Default |
| --- | --- | --- |
| `q` | no | empty |
| `market` | no | all |
| `limit` | no | `20` |
| `offset` | no | `0` |

Response `data`:

```json
{
  "items": [
    {
      "ticker": "005930",
      "name": "삼성전자",
      "market": "KOSPI",
      "sector": "반도체",
      "corp_code": "MOCK00126380",
      "match_reason": "name"
    }
  ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 1,
    "has_more": false
  }
}
```

## 6. GET /stocks/candidates

Query:

| Name | Required | Default |
| --- | --- | --- |
| `risk_profile` | no | `balanced` |
| `market` | no | all |
| `sector` | no | all |
| `sort` | no | `score_desc` |
| `limit` | no | `20` |
| `offset` | no | `0` |

`sort` supports `score_desc`, `volume_desc`, and `updated_desc`.
`risk_profile` supports `conservative`, `balanced`, and `aggressive`.
When `sort=score_desc`, risk profile affects ordering:

- `conservative`: fewer risk signals first, then higher score.
- `balanced`: higher score with a small risk-count penalty.
- `aggressive`: higher score first.

Response `data`:

```json
{
  "as_of": "2026-06-09",
  "items": [
    {
      "ticker": "005930",
      "name": "삼성전자",
      "market": "KOSPI",
      "sector": "반도체",
      "score": {
        "total": 78.5,
        "grade": "B",
        "as_of": "2026-06-09",
        "version": "mock-score-rules-2026-06-09",
        "breakdown": {
          "momentum": 7.5,
          "liquidity": 7.8,
          "disclosure": 7.5,
          "news": 7.8
        }
      },
      "price": {
        "close": 70200,
        "change_rate": 0.8,
        "volume": 7800000,
        "trade_date": "2026-06-09"
      },
      "evidence_summary": {
        "news_count": 1,
        "disclosure_count": 1,
        "latest_at": "2026-06-08T09:00:00Z"
      }
    }
  ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 30,
    "has_more": true
  }
}
```

## 7. GET /stocks/{ticker}

Response `data`:

```json
{
  "stock": {
    "ticker": "005930",
    "name": "삼성전자",
    "market": "KOSPI",
    "sector": "반도체",
    "corp_code": "MOCK00126380"
  },
  "price": {
    "close": 70200,
    "change_rate": 0.8,
    "volume": 7800000,
    "trade_date": "2026-06-09"
  },
  "score": {
    "total": 78.5,
    "grade": "B",
    "as_of": "2026-06-09",
    "version": "mock-score-rules-2026-06-09",
    "breakdown": {
      "momentum": 7.5,
      "liquidity": 7.8,
      "disclosure": 7.5,
      "news": 7.8
    }
  },
  "brief": {
    "summary": "삼성전자는 공개 데이터 기반 mock 점수와 근거로 검토 후보에 포함된 종목입니다.",
    "risk_notes": [
      "실데이터 연동 전 mock 데이터 기준입니다.",
      "투자 판단 전 원문과 최신 데이터를 확인해야 합니다."
    ],
    "as_of": "2026-06-09"
  },
  "evidence_preview": [
    {
      "id": "ev_mock_005930_news",
      "source_type": "NEWS",
      "title": "[MOCK NEWS] 삼성전자 산업 동향 데모 기사",
      "source_name": "NAVER_NEWS_MOCK",
      "url": "https://mock.stockbrief.local/naver-news/005930",
      "published_at": "2026-06-08T09:00:00Z"
    }
  ]
}
```

## 8. GET /stocks/{ticker}/evidence

Query:

| Name | Required | Default |
| --- | --- | --- |
| `source_type` | no | all |
| `from_date` | no | none |
| `to_date` | no | none |
| `limit` | no | `20` |
| `offset` | no | `0` |

`source_type` supports `NEWS`, `DISCLOSURE`, `SCORE`, and `CHUNK`.

Response `data`:

```json
{
  "ticker": "005930",
  "items": [
    {
      "id": "ev_mock_005930_news",
      "source_type": "NEWS",
      "title": "[MOCK NEWS] 삼성전자 산업 동향 데모 기사",
      "source_name": "NAVER_NEWS_MOCK",
      "url": "https://mock.stockbrief.local/naver-news/005930",
      "published_at": "2026-06-08T09:00:00Z",
      "snippet": "데모 뉴스 데이터에서 시장 관심도 검토 포인트가 확인됩니다.",
      "metadata": {
        "data_status": "available",
        "source_identifier": "mock-news-005930",
        "as_of_date": "2026-06-08"
      }
    }
  ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 4,
    "has_more": false
  }
}
```

## 9. POST /chat

Chat providers explain stored scores, evidence, freshness, missing data, and
risk tags. They must not generate, replace, or modify score values.
`CHAT_PROVIDER=agentcore` is a dev-only provider that calls an AgentCore
Runtime-compatible `/invocations` endpoint and then repeats the same API
boundary safety and citation validation before returning `/v1/chat` data.

Request:

```json
{
  "message": "삼성전자 최근 근거 요약해줘",
  "ticker": "005930",
  "session_id": "local-session-1"
}
```

Response `data`:

```json
{
  "session_id": "local-session-1",
  "message_id": null,
  "answer": "mock 데이터 기준 설명입니다.",
  "citations": [
    {
      "id": "ev_mock_005930_news",
      "source_type": "NEWS",
      "title": "[MOCK NEWS] 삼성전자 산업 동향 데모 기사",
      "url": "https://mock.stockbrief.local/naver-news/005930",
      "published_at": null
    }
  ],
  "safety": {
    "policy_action": "ALLOW",
    "disclaimer": "이 정보는 투자 조언이 아니며, 투자 판단 전 원문과 최신 데이터를 확인하세요."
  }
}
```

AgentCore Runtime dev contract:

- `GET /ping` returns `{"status":"Healthy"}`.
- `POST /invocations` accepts JSON with `input.message`, `input.ticker`,
  `input.candidate`, `input.evidence`, and `input.baseline`.
- `POST /invocations` returns JSON with `status`, `response.answer`, and
  redacted `response.trace` metadata for selected read-only tools, tool
  latency/error status, metrics, policy status, and citation IDs.

## 10. Authenticated Account Endpoints

These endpoints require the Cognito JWT authorizer or the local test auth
override. They are scoped to the current user.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/me` | Current user profile |
| `PATCH` | `/v1/me` | Update current user profile |
| `GET` | `/v1/me/preferences` | Current user preferences |
| `PUT` | `/v1/me/preferences` | Replace current user preferences |
| `GET` | `/v1/me/watchlist` | Current user watchlist |
| `POST` | `/v1/me/watchlist` | Add a watchlist item |
| `PATCH` | `/v1/me/watchlist/{ticker}` | Update a watchlist item |
| `DELETE` | `/v1/me/watchlist/{ticker}` | Remove a watchlist item |
| `POST` | `/v1/me/watchlist/import` | Merge guest watchlist items into the server watchlist |
| `GET` | `/v1/me/chat-sessions` | List current user chat sessions |
| `POST` | `/v1/me/chat-sessions` | Create an empty current user chat session |
| `GET` | `/v1/me/chat-sessions/{session_id}` | Read current user chat session messages |

`PUT /v1/me/preferences` stores the current user's product preferences. Unknown
preference keys are preserved for forward compatibility, but known keys are
validated:

- `risk_profile`: `conservative`, `balanced`, or `aggressive`
- `notifications.email_enabled`: boolean
- `notifications.watchlist_digest`: `off`, `daily`, or `weekly`

When any known preference key above is present, `null` is rejected as invalid.

Request:

```json
{
  "preferences": {
    "risk_profile": "balanced",
    "markets": ["KOSPI"],
    "notifications": {
      "email_enabled": true,
      "watchlist_digest": "weekly"
    }
  }
}
```

Invalid known preference values return `400 INVALID_PREFERENCES` with field-level
details.

`GET /v1/me/chat-sessions/{session_id}` returns `404 CHAT_SESSION_NOT_FOUND`
when the session does not exist or belongs to another user.

Response:

```json
{
  "session": {
    "session_id": "chat_20260624_001",
    "ticker": "005930",
    "title": "삼성전자 설명",
    "created_at": "2026-06-24T09:00:00Z",
    "updated_at": "2026-06-24T09:05:00Z"
  },
  "messages": [
    {
      "message_id": "msg_20260624_001",
      "role": "user",
      "content": "왜 추천됐나요?",
      "ticker": "005930",
      "citations": [],
      "safety_flags": [],
      "created_at": "2026-06-24T09:00:01Z"
    },
    {
      "message_id": "msg_20260624_002",
      "role": "assistant",
      "content": "공개 데이터 기준 설명입니다.",
      "ticker": "005930",
      "citations": [
        {
          "evidence_id": "ev_mock_005930_news",
          "type": "news",
          "title": "[MOCK NEWS] 삼성전자 산업 동향 데모 기사",
          "source_url": "https://mock.stockbrief.local/naver-news/005930",
          "published_at": "2026-06-08T09:00:00Z"
        }
      ],
      "safety_flags": [
        {
          "policy_status": "allowed"
        }
      ],
      "created_at": "2026-06-24T09:00:02Z"
    }
  ]
}
