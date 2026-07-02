from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/check_deployed_chat_smoke.py"


spec = importlib.util.spec_from_file_location("check_deployed_chat_smoke", SCRIPT_PATH)
assert spec is not None
smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


class FakeFetcher:
    def __init__(
        self,
        *,
        missing_citations: bool = False,
        redirect_policy: str = "REDIRECT",
        unsafe_answer: bool = False,
    ) -> None:
        self.missing_citations = missing_citations
        self.redirect_policy = redirect_policy
        self.unsafe_answer = unsafe_answer
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, body: bytes, timeout_seconds: float):
        request = json.loads(body.decode("utf-8"))
        self.calls.append((url, request, timeout_seconds))
        message = request["message"]
        policy_action = "ALLOW"
        answer = "공개 근거 ev_mock_005930_news 기준으로 검토할 수 있습니다."
        citations = [
            {
                "id": "ev_mock_005930_news",
                "source_type": "NEWS",
                "title": "원문 제목은 출력하지 않습니다.",
            }
        ]

        if "매수" in message:
            policy_action = self.redirect_policy
            answer = "투자 판단 대신 공개 근거와 위험 요인을 확인하세요."
        if self.unsafe_answer:
            answer = "매수 표현이 포함된 응답"
        if self.missing_citations:
            citations = []

        return smoke.HttpResponse(
            status_code=200,
            body=json.dumps(
                {
                    "success": True,
                    "data": {
                        "answer": answer,
                        "citations": citations,
                        "safety": {
                            "policy_action": policy_action,
                            "disclaimer": "이 정보는 투자 조언이 아닙니다.",
                        },
                    },
                }
            ).encode("utf-8"),
        )


def test_deployed_chat_smoke_redacts_answer_and_validates_scenarios() -> None:
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        api_base_url="https://api.example.com",
        ticker="005930",
        timeout_seconds=2,
        fetch=fetcher,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert "공개 근거 ev_mock_005930_news 기준" not in serialized
    assert "투자 판단 대신 공개 근거" not in serialized
    assert "원문 제목은 출력하지 않습니다." not in serialized
    assert result["checks"]["allowed_explanation"]["summary"]["policy_action"] == "ALLOW"
    assert result["checks"]["allowed_explanation"]["summary"]["citation_count"] == 1
    assert result["checks"]["allowed_explanation"]["summary"]["answer_sha256_prefix"]
    assert result["checks"]["policy_redirect"]["summary"]["policy_action"] == "REDIRECT"
    assert [call[0] for call in fetcher.calls] == [
        "https://api.example.com/v1/chat",
        "https://api.example.com/v1/chat",
    ]


def test_deployed_chat_smoke_fails_without_deployed_api_base_url(monkeypatch) -> None:
    monkeypatch.delenv(smoke.DEFAULT_API_ENV, raising=False)

    args = smoke.parse_args([])
    result = smoke.run_smoke(api_base_url=args.api_base_url, fetch=FakeFetcher())

    assert result == {
        "ok": False,
        "api_base_url_configured": False,
        "ticker": "005930",
        "checks": {},
        "blockers": [{"code": "missing_api_base_url", "env": "STOCKBRIEF_API_BASE_URL"}],
    }


def test_deployed_chat_smoke_reports_missing_citations() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        fetch=FakeFetcher(missing_citations=True),
    )

    assert result["ok"] is False
    assert {
        "check": "allowed_explanation",
        "code": "citation_count_below_minimum",
        "citation_count": 0,
        "min_citation_count": 1,
    } in result["blockers"]


def test_deployed_chat_smoke_reports_unexpected_policy_action() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        fetch=FakeFetcher(redirect_policy="ALLOW"),
    )

    assert result["ok"] is False
    assert {
        "check": "policy_redirect",
        "code": "unexpected_policy_action",
        "policy_action": "ALLOW",
        "expected_policy_actions": ["REDIRECT"],
    } in result["blockers"]


def test_deployed_chat_smoke_blocks_prohibited_terms_without_raw_answer() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        fetch=FakeFetcher(unsafe_answer=True),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert "매수 표현이 포함된 응답" not in serialized
    assert {
        "check": "allowed_explanation",
        "code": "prohibited_terms_detected",
        "matched_terms": ["매수"],
    } in result["blockers"]
