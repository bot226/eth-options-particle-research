"""Path-aware future outcomes for shadow candidates."""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any


HORIZONS_MINUTES = (5, 15, 30, 60, 120, 240)


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=30.0,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _load_candles(research_db: Path) -> list[dict[str, Any]]:
    connection = _readonly_connection(research_db)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT timestamp_utc, open, high, low, close, exchange, symbol,
                       timeframe, ohlcv_source
                FROM ohlcv_candles
                WHERE timeframe = '1m'
                  AND candle_source_verified = 1
                ORDER BY timestamp_utc
                """
            )
        ]
    finally:
        connection.close()

    # Keep one deterministic candle per minute if more than one source is present.
    by_timestamp: dict[float, dict[str, Any]] = {}
    for row in rows:
        timestamp = float(row["timestamp_utc"])
        current = by_timestamp.get(timestamp)
        if current is None or row.get("exchange") == "bybit":
            by_timestamp[timestamp] = row
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def _directional_extremes(
    direction: str,
    entry_price: float,
    path: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not path:
        return None, None
    highest = max(float(candle["high"]) for candle in path)
    lowest = min(float(candle["low"]) for candle in path)
    if direction == "SHORT":
        mfe = (entry_price - lowest) / entry_price * 100.0
        mae = (entry_price - highest) / entry_price * 100.0
    else:
        mfe = (highest - entry_price) / entry_price * 100.0
        mae = (lowest - entry_price) / entry_price * 100.0
    return round(mfe, 6), round(mae, 6)


def attach_outcomes(
    connection: sqlite3.Connection,
    run_id: str,
    research_db: str | Path,
) -> dict[str, int]:
    candles = _load_candles(Path(research_db))
    if not candles:
        return {"shadow_outcomes": 0, "complete_240m": 0}
    candle_timestamps = [float(candle["timestamp_utc"]) for candle in candles]

    outcome_count = 0
    complete_count = 0
    for candidate in connection.execute(
        """
        SELECT candidate_id, timestamp_utc, direction, entry_price
        FROM shadow_candidates
        WHERE run_id = ? AND entry_price IS NOT NULL
        ORDER BY timestamp_utc
        """,
        (run_id,),
    ):
        entry_timestamp = float(candidate["timestamp_utc"])
        entry_price = float(candidate["entry_price"])
        start_index = bisect_left(candle_timestamps, entry_timestamp)
        returns: dict[int, float | None] = {}
        extremes: dict[int, tuple[float | None, float | None]] = {}

        for horizon in HORIZONS_MINUTES:
            target = entry_timestamp + horizon * 60.0
            end_index = bisect_right(candle_timestamps, target) - 1
            if end_index < start_index or end_index < 0:
                returns[horizon] = None
                extremes[horizon] = (None, None)
                continue
            candle = candles[end_index]
            # Require a close reasonably near the requested horizon.
            if target - float(candle["timestamp_utc"]) > 90.0:
                returns[horizon] = None
            else:
                returns[horizon] = round(
                    (float(candle["close"]) - entry_price) / entry_price * 100.0,
                    6,
                )
            path = candles[start_index : end_index + 1]
            extremes[horizon] = _directional_extremes(
                candidate["direction"], entry_price, path
            )

        complete = int(returns[240] is not None)
        complete_count += complete
        available_end = bisect_right(candle_timestamps, entry_timestamp + 240 * 60.0)
        candles_available = max(0, available_end - start_index)
        connection.execute(
            """
            INSERT INTO shadow_outcomes (
                candidate_id, entry_timestamp_utc, entry_price,
                return_5m, return_15m, return_30m, return_60m,
                return_120m, return_240m,
                mfe_30m_pct, mae_30m_pct, mfe_60m_pct, mae_60m_pct,
                mfe_120m_pct, mae_120m_pct, mfe_240m_pct, mae_240m_pct,
                candles_available, outcome_complete_240m, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["candidate_id"], entry_timestamp, entry_price,
                returns[5], returns[15], returns[30], returns[60],
                returns[120], returns[240],
                extremes[30][0], extremes[30][1],
                extremes[60][0], extremes[60][1],
                extremes[120][0], extremes[120][1],
                extremes[240][0], extremes[240][1],
                candles_available, complete,
                json.dumps(
                    {
                        "price_source": "mos_research.ohlcv_candles",
                        "timeframe": "1m",
                        "return_unit": "percent",
                        "mfe_mae_are_direction_adjusted": candidate["direction"] != "NEUTRAL",
                        "neutral_mfe_mae_use_upside_downside_range": candidate["direction"] == "NEUTRAL",
                    },
                    sort_keys=True,
                ),
            ),
        )
        outcome_count += 1

    connection.commit()
    return {"shadow_outcomes": outcome_count, "complete_240m": complete_count}
