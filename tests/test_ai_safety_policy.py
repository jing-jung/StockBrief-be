from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPOSITORY_ROOT / "docs" / "engineering" / "AI_SAFETY_POLICY.md"
PROVIDER_PATH = REPOSITORY_ROOT / "app" / "services" / "chat" / "providers.py"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"


def test_bedrock_policy_documents_fail_closed_controls() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")

    for phrase in [
        "`CHAT_PROVIDER=bedrock`",
        "`CHAT_PROVIDER_UNAVAILABLE`",
        "runtime request failures",
        "empty answers",
        "unsafe output",
        "citation guard failures",
        "`provider`",
        "`latency_ms`",
        "`fail_closed_reason`",
        "`citation_guard_failure`",
        "`unsafe_output_block`",
    ]:
        assert phrase in policy


def test_bedrock_policy_keeps_agentcore_deferred() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")

    assert "AgentCore Runtime remains deferred" in policy
    assert "Do not move `/v1/chat` to AgentCore" in policy
    assert "Strands Agents SDK remains out of the direct Bedrock provider" in policy


def test_direct_bedrock_provider_keeps_strands_and_agentcore_out() -> None:
    provider = PROVIDER_PATH.read_text(encoding="utf-8").casefold()
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8").casefold()

    assert "strands" not in provider
    assert "agentcore" not in provider
    assert "strands" not in pyproject
