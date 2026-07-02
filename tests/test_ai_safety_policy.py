from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPOSITORY_ROOT / "docs" / "engineering" / "AI_SAFETY_POLICY.md"
PROVIDER_PATH = REPOSITORY_ROOT / "app" / "services" / "chat" / "providers.py"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"
UV_LOCK_PATH = REPOSITORY_ROOT / "uv.lock"
AGENTCORE_DOCKERFILE_PATH = REPOSITORY_ROOT / "Dockerfile.agentcore"


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
        "scripts/check_deployed_chat_smoke.py",
        "without printing raw model answers",
    ]:
        assert phrase in policy


def test_policy_documents_agentcore_dev_runtime_boundary() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")

    assert "`CHAT_PROVIDER=agentcore`" in policy
    assert "dev-only" in policy
    assert "fail closed with `CHAT_PROVIDER_UNAVAILABLE`" in policy
    assert "API provider boundary" in policy
    assert "AgentCore output is not trusted by itself" in policy
    assert "Strands Agents SDK remains out of the direct Bedrock provider" in policy


def test_provider_factory_explicitly_allows_agentcore_with_locked_strands_extra() -> None:
    provider = PROVIDER_PATH.read_text(encoding="utf-8").casefold()
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8").casefold()
    uv_lock = UV_LOCK_PATH.read_text(encoding="utf-8").casefold()
    dockerfile = AGENTCORE_DOCKERFILE_PATH.read_text(encoding="utf-8").casefold()

    assert "strands" not in provider
    assert 'if name == "agentcore":' in provider
    assert '"agent" + "core"' not in provider
    assert "agentcore = [" in pyproject
    assert '"strands-agents>=1.0.0,<2.0.0"' in pyproject
    assert 'name = "strands-agents"' in uv_lock
    assert "--extra agentcore" in dockerfile
    assert "requirements-agentcore.txt" not in dockerfile
