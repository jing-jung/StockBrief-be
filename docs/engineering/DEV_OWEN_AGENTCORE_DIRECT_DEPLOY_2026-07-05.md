# dev-owen AgentCore Direct Deploy 검증 기록

검증일: 2026-07-05
대상 commit: `fc9538aaed168b598e95e66332f443cc29420c48`
대상 환경: `dev-owen`
AWS account: `465002806203`
Region: `ap-northeast-2`

이 문서는 PR #308 병합 후 `dev-owen`에 AgentCore runtime retry와 chat answer artifact cleanup을 배포한 redacted 기록이다. 원문 모델 답변, secret, token, raw provider payload는 기록하지 않는다.

## 배포

- PR: `80-hours-a-week/StockBrief-be#308`
- 병합 시각: `2026-07-05T14:44:26Z`
- merge commit: `fc9538aaed168b598e95e66332f443cc29420c48`
- workflow: `backend-dev-deploy.yml`
- ref: `main`
- plan-only run: `28744570906`
- apply run: `28744613024`

plan-only 결과:

- `Plan: 0 to add, 1 to change, 0 to destroy.`
- 변경 대상:
  - `module.api_lambda.aws_lambda_function.api`
- Lambda package hash만 변경되는 plan임을 확인했다.

apply 결과:

- GitHub Actions run `28744613024` 성공
- 1차 Terraform apply 성공
- `deploy_agentcore_runtime.py` direct deploy 성공
- AgentCore direct deploy 후 2차 Terraform plan/apply 성공

## 배포 후 상태

API Gateway endpoint:

```text
https://mzvc5xz9d3.execute-api.ap-northeast-2.amazonaws.com
```

Health:

```text
GET /v1/health
HTTP 200
status=ok
service=stockbrief-api
environment=dev-owen
```

CORS preflight:

```text
OPTIONS /v1/chat
Origin: https://main.ds567pg6dpmlp.amplifyapp.com
HTTP 200
access-control-allow-origin=https://main.ds567pg6dpmlp.amplifyapp.com
```

## Smoke 결과

Deployed chat smoke:

```text
scripts/check_deployed_chat_smoke.py --api-base-url https://mzvc5xz9d3.execute-api.ap-northeast-2.amazonaws.com/v1 --timeout-seconds 30
ok=true
allowed_explanation.status_code=200
allowed_explanation.policy_action=ALLOW
allowed_explanation.citation_count=10
allowed_explanation.matched_terms=[]
policy_redirect.status_code=200
policy_redirect.policy_action=REDIRECT
policy_redirect.citation_count=4
policy_redirect.matched_terms=[]
blockers=[]
```

Chat answer artifact spot check:

```text
status_code=200
policy_action=ALLOW
citation_count=4
has_url=false
has_bold=false
has_ev=false
has_any_bracket=false
has_empty_parens=false
has_repeated_comma=false
has_thinking=false
```

Local regression checks on `fc9538aa`:

```text
uv run --extra dev pytest tests/test_agentcore_provider.py tests/test_chat_api.py tests/test_recommendation_api.py tests/test_deployment_docs.py -q
94 passed, 2 skipped

python3 -m compileall app
passed

uv run python scripts/check_prohibited_terms.py
Prohibited financial wording policy passed.
Infra-sensitive identifier policy passed.
```

## 다음 점검 기준

- `/v1/chat`이 다시 503을 반환하면 Lambda log에서 `agentcore_chat_provider_runtime_retry`, `agentcore_chat_provider_fail_closed`, `citation_guard_failed`, `unsafe_output`, `empty_answer`를 먼저 확인한다.
- 답변 본문에 URL, markdown, `ev_`/`rsn_`, bracket, hidden reasoning artifact가 다시 노출되면 `normalize_chat_answer()`와 `has_chat_answer_artifacts()` 회귀 테스트를 먼저 확인한다.
- AgentCore runtime ARN 또는 endpoint 이름이 바뀌면 SSM `/stockbrief/dev-owen/agentcore/*` metadata와 Lambda invoke policy resource를 같이 확인한다.
