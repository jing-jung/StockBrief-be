from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/check_candidate_query_perf.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_candidate_query_perf", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_query_perf_report_builds_expected_query_groups() -> None:
    script = _load_script()

    report = script.build_report(
        execute=False,
        risk_profile="balanced",
        market=None,
        sector=None,
        limit=20,
        offset=0,
    )

    assert report["executed"] is False
    assert "session-independent" in report["notes"][0]
    assert [query["name"] for query in report["queries"]] == [
        "total",
        "as_of",
        "score_desc",
        "updated_desc",
        "volume_desc",
    ]
    assert all(
        query["sql"].startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)")
        for query in report["queries"]
    )
    assert "DATABASE_URL" not in str(report)
    assert "password" not in str(report).lower()


def test_candidate_query_perf_report_preserves_price_metric_join_contract() -> None:
    script = _load_script()

    report = script.build_report(
        execute=False,
        risk_profile="balanced",
        market=None,
        sector=None,
        limit=20,
        offset=0,
    )
    sql_by_name = {query["name"]: query["sql"] for query in report["queries"]}

    for name in ("total", "as_of", "score_desc", "updated_desc"):
        assert "price_metrics" not in sql_by_name[name]
    assert "price_metrics" in sql_by_name["volume_desc"]
