from datetime import date

from app.services.recommendation.factors import (
    FactorDirection,
    FactorRankInput,
    FactorRankResult,
    calculate_factor_ranks,
)

AS_OF_DATE = date(2026, 6, 30)


def _row(
    ticker: str,
    factor_name: str,
    raw_value: float | None,
    *,
    market: str = "KOSPI",
    sector: str | None = "semiconductor",
    direction: FactorDirection = "higher_is_better",
) -> FactorRankInput:
    return FactorRankInput(
        ticker=ticker,
        market=market,
        sector=sector,
        as_of_date=AS_OF_DATE,
        factor_name=factor_name,
        raw_value=raw_value,
        direction=direction,
        source_refs=(f"fixture:{ticker}:{factor_name}",),
    )


def _by_ticker(rows: list[FactorRankInput]) -> dict[str, FactorRankResult]:
    return {rank.ticker: rank for rank in calculate_factor_ranks(rows)}


def test_sector_partition_with_enough_rows_uses_market_sector() -> None:
    ranks = _by_ticker(
        [
            _row("AAA", "profitability", 0.30),
            _row("BBB", "profitability", 0.20),
            _row("CCC", "profitability", 0.20),
            _row("DDD", "profitability", 0.10),
            _row("EEE", "profitability", 0.40, sector="battery"),
            _row("FFF", "profitability", 0.05, sector="battery"),
        ]
    )

    assert ranks["AAA"].partition_kind == "market_sector"
    assert ranks["AAA"].partition_key == ("KOSPI", "semiconductor")
    assert ranks["AAA"].partition_size == 4
    assert ranks["AAA"].percentile_rank == 100
    assert ranks["DDD"].percentile_rank == 0
    assert ranks["AAA"].used_fallback is False


def test_small_sector_partition_falls_back_to_market() -> None:
    ranks = _by_ticker(
        [
            _row("AAA", "profitability", 0.30),
            _row("BBB", "profitability", 0.20),
            _row("CCC", "profitability", 0.20),
            _row("DDD", "profitability", 0.10),
            _row("EEE", "profitability", 0.40, sector="battery"),
            _row("FFF", "profitability", 0.05, sector="battery"),
        ]
    )

    assert ranks["EEE"].partition_kind == "market"
    assert ranks["EEE"].partition_key == ("KOSPI",)
    assert ranks["EEE"].partition_size == 6
    assert ranks["EEE"].used_fallback is True
    assert ranks["EEE"].percentile_rank == 100
    assert ranks["FFF"].percentile_rank == 0


def test_inverse_factor_ranks_lower_raw_values_higher() -> None:
    ranks = _by_ticker(
        [
            _row(
                "LOW",
                "momentum_volatility",
                0.12,
                direction="lower_is_better",
            ),
            _row(
                "MID",
                "momentum_volatility",
                0.20,
                direction="lower_is_better",
            ),
            _row(
                "HIGH",
                "momentum_volatility",
                0.30,
                direction="lower_is_better",
            ),
        ]
    )

    assert ranks["LOW"].percentile_rank == 100
    assert ranks["MID"].percentile_rank == 50
    assert ranks["HIGH"].percentile_rank == 0


def test_missing_factor_value_does_not_create_rank() -> None:
    ranks = _by_ticker(
        [
            _row("MISSING", "valuation", None, direction="lower_is_better"),
            _row("CHEAP", "valuation", 8, direction="lower_is_better"),
            _row("FAIR", "valuation", 12, direction="lower_is_better"),
            _row("RICH", "valuation", 16, direction="lower_is_better"),
        ]
    )

    assert ranks["MISSING"].percentile_rank is None
    assert ranks["MISSING"].partition_size == 3
    assert ranks["MISSING"].missing_data == ("valuation.raw_value",)
    assert ranks["CHEAP"].percentile_rank == 100


def test_tied_raw_values_share_deterministic_peer_rank() -> None:
    rows = [
        _row("AAA", "growth", 0.30),
        _row("BBB", "growth", 0.20),
        _row("CCC", "growth", 0.20),
        _row("DDD", "growth", 0.10),
    ]
    ranks = _by_ticker(rows)
    reversed_ranks = _by_ticker(list(reversed(rows)))

    assert ranks["BBB"].percentile_rank == 50
    assert ranks["CCC"].percentile_rank == 50
    assert {
        ticker: rank.percentile_rank for ticker, rank in ranks.items()
    } == {
        ticker: rank.percentile_rank for ticker, rank in reversed_ranks.items()
    }
