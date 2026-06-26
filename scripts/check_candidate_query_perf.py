#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db import get_engine, get_session_factory
from app.services.candidate_service import CandidateService


QUERY_NAMES = ("total", "as_of", "score_desc", "updated_desc", "volume_desc")
OFFLINE_DEPENDENCY_NOTE = (
    "Offline mode compiles CandidateService private query builders with a "
    "statement-only session. Keep those builders session-independent, or move "
    "query construction to a dedicated helper before adding session I/O."
)


def build_report(
    *,
    execute: bool,
    risk_profile: str,
    market: str | None,
    sector: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    engine = get_engine() if execute else None
    if execute and engine is not None and engine.dialect.name != "postgresql":
        raise RuntimeError("Candidate query performance execution requires PostgreSQL.")

    session_factory = get_session_factory() if execute else None
    session_context = session_factory() if session_factory else _statement_only_session()
    with session_context as session:
        statements = _candidate_statements(
            session=session,
            risk_profile=risk_profile,
            market=market,
            sector=sector,
            limit=limit,
            offset=offset,
        )
        dialect = engine.dialect if engine is not None else postgresql.dialect()
        queries = [
            _query_report(
                session=session,
                name=name,
                statement=statements[name],
                execute=execute,
                dialect=dialect,
            )
            for name in QUERY_NAMES
        ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executed": execute,
        "dialect": "postgresql" if execute else "postgresql-offline",
        "parameters": {
            "risk_profile": risk_profile,
            "market": market,
            "sector": sector,
            "limit": limit,
            "offset": offset,
        },
        "notes": [OFFLINE_DEPENDENCY_NOTE],
        "queries": queries,
    }


def _candidate_statements(
    *,
    session: Session,
    risk_profile: str,
    market: str | None,
    sector: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    service = CandidateService(session)
    base_statement = service._stock_candidate_base_statement(market=market, sector=sector)
    total_statement, as_of_statement = service._stock_candidate_aggregate_statements(
        base_statement,
    )

    def ordered(sort: str):
        statement = service._stock_candidate_base_statement(market=market, sector=sector)
        return (
            service._order_stock_candidate_statement(
                statement=statement,
                sort=sort,
                risk_profile=risk_profile,
            )
            .limit(limit)
            .offset(offset)
        )

    return {
        "total": total_statement,
        "as_of": as_of_statement,
        "score_desc": ordered("score_desc"),
        "updated_desc": ordered("updated_desc"),
        "volume_desc": ordered("volume_desc"),
    }


def _query_report(
    *,
    session: Session,
    name: str,
    statement: Any,
    execute: bool,
    dialect: Any,
) -> dict[str, Any]:
    sql = _compile_sql(statement, dialect=dialect)
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)\n{sql}"
    report: dict[str, Any] = {
        "name": name,
        "sql": explain_sql,
    }
    if execute:
        report["plan"] = [
            str(row[0])
            for row in session.execute(text(explain_sql)).all()
        ]
    return report


def _compile_sql(statement: Any, *, dialect: Any) -> str:
    return str(
        statement.compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


@contextmanager
def _statement_only_session() -> Iterator[Session]:
    # CandidateService query builders used here must only construct statements.
    yield cast(Session, None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or execute PostgreSQL EXPLAIN plans for candidate queries.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run EXPLAIN against configured PostgreSQL.",
    )
    parser.add_argument(
        "--risk-profile",
        default="balanced",
        choices=("conservative", "balanced", "aggressive"),
    )
    parser.add_argument("--market", default=None)
    parser.add_argument("--sector", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    report = build_report(
        execute=args.execute,
        risk_profile=args.risk_profile,
        market=args.market,
        sector=args.sector,
        limit=max(args.limit, 1),
        offset=max(args.offset, 0),
    )
    body = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{body}\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
