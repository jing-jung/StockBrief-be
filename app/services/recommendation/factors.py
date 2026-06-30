from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Literal

FactorDirection = Literal["higher_is_better", "lower_is_better"]
PartitionKind = Literal["market_sector", "market"]

DEFAULT_MIN_PARTITION_SIZE = 3


@dataclass(frozen=True)
class FactorRankInput:
    ticker: str
    market: str
    sector: str | None
    as_of_date: date
    factor_name: str
    raw_value: float | None
    direction: FactorDirection
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorRankResult:
    ticker: str
    market: str
    sector: str | None
    as_of_date: date
    factor_name: str
    raw_value: float | None
    direction: FactorDirection
    source_refs: tuple[str, ...]
    percentile_rank: float | None
    partition_kind: PartitionKind
    partition_key: tuple[str, ...]
    partition_size: int
    used_fallback: bool
    missing_data: tuple[str, ...] = ()


def calculate_factor_ranks(
    rows: Iterable[FactorRankInput],
    *,
    min_partition_size: int = DEFAULT_MIN_PARTITION_SIZE,
) -> list[FactorRankResult]:
    if min_partition_size < 1:
        raise ValueError("min_partition_size must be at least 1")

    indexed_rows = list(enumerate(rows))
    rank_cache: dict[
        tuple[PartitionKind, tuple[str, ...], date, str, str], dict[int, float]
    ] = {}
    results: list[FactorRankResult] = []

    for row_index, row in indexed_rows:
        _validate_direction(row.direction)
        partition = _select_partition(row, indexed_rows, min_partition_size)
        cache_key = (
            partition.kind,
            partition.key,
            row.as_of_date,
            row.factor_name,
            row.direction,
        )
        ranks = rank_cache.setdefault(
            cache_key,
            _percentile_ranks(partition.peer_indexes, indexed_rows, row.direction),
        )
        raw_value = _usable_value(row.raw_value)
        missing_data = () if raw_value is not None else (f"{row.factor_name}.raw_value",)

        results.append(
            FactorRankResult(
                ticker=row.ticker,
                market=row.market,
                sector=row.sector,
                as_of_date=row.as_of_date,
                factor_name=row.factor_name,
                raw_value=raw_value,
                direction=row.direction,
                source_refs=tuple(row.source_refs),
                percentile_rank=ranks.get(row_index),
                partition_kind=partition.kind,
                partition_key=partition.key,
                partition_size=len(partition.peer_indexes),
                used_fallback=partition.used_fallback,
                missing_data=missing_data,
            )
        )

    return results


@dataclass(frozen=True)
class _Partition:
    kind: PartitionKind
    key: tuple[str, ...]
    peer_indexes: tuple[int, ...]
    used_fallback: bool


def _select_partition(
    row: FactorRankInput,
    indexed_rows: list[tuple[int, FactorRankInput]],
    min_partition_size: int,
) -> _Partition:
    sector_peer_indexes = ()
    if row.sector:
        sector_peer_indexes = _peer_indexes(
            row,
            indexed_rows,
            partition_kind="market_sector",
            partition_key=(row.market, row.sector),
        )
        if len(sector_peer_indexes) >= min_partition_size:
            return _Partition(
                kind="market_sector",
                key=(row.market, row.sector),
                peer_indexes=sector_peer_indexes,
                used_fallback=False,
            )

    return _Partition(
        kind="market",
        key=(row.market,),
        peer_indexes=_peer_indexes(
            row,
            indexed_rows,
            partition_kind="market",
            partition_key=(row.market,),
        ),
        used_fallback=bool(row.sector),
    )


def _peer_indexes(
    row: FactorRankInput,
    indexed_rows: list[tuple[int, FactorRankInput]],
    *,
    partition_kind: PartitionKind,
    partition_key: tuple[str, ...],
) -> tuple[int, ...]:
    peers = []
    for peer_index, peer in indexed_rows:
        if _usable_value(peer.raw_value) is None:
            continue
        if (
            peer.as_of_date != row.as_of_date
            or peer.factor_name != row.factor_name
            or peer.direction != row.direction
        ):
            continue
        if partition_kind == "market" and peer.market == partition_key[0]:
            peers.append(peer_index)
        elif (
            partition_kind == "market_sector"
            and peer.market == partition_key[0]
            and peer.sector == partition_key[1]
        ):
            peers.append(peer_index)
    return tuple(peers)


def _percentile_ranks(
    peer_indexes: tuple[int, ...],
    indexed_rows: list[tuple[int, FactorRankInput]],
    direction: FactorDirection,
) -> dict[int, float]:
    if not peer_indexes:
        return {}
    if len(peer_indexes) == 1:
        return {peer_indexes[0]: 100.0}

    rows_by_index = dict(indexed_rows)
    ordered = sorted(
        peer_indexes,
        key=lambda index: (
            -_rank_value(rows_by_index[index].raw_value, direction),
            rows_by_index[index].ticker,
        ),
    )
    ranks: dict[int, float] = {}
    position = 0
    last_position = len(ordered) - 1

    while position < len(ordered):
        current_value = _rank_value(
            rows_by_index[ordered[position]].raw_value, direction
        )
        tie_end = position
        while (
            tie_end + 1 < len(ordered)
            and _rank_value(rows_by_index[ordered[tie_end + 1]].raw_value, direction)
            == current_value
        ):
            tie_end += 1

        average_position = (position + tie_end) / 2
        percentile = round((last_position - average_position) / last_position * 100, 6)
        for tied_position in range(position, tie_end + 1):
            ranks[ordered[tied_position]] = percentile
        position = tie_end + 1

    return ranks


def _rank_value(raw_value: float | None, direction: FactorDirection) -> float:
    value = _usable_value(raw_value)
    if value is None:
        raise ValueError("missing raw_value cannot be ranked")
    return value if direction == "higher_is_better" else -value


def _usable_value(raw_value: float | None) -> float | None:
    if raw_value is None:
        return None
    value = float(raw_value)
    return value if isfinite(value) else None


def _validate_direction(direction: str) -> None:
    if direction not in ("higher_is_better", "lower_is_better"):
        raise ValueError(f"unsupported factor direction: {direction}")
