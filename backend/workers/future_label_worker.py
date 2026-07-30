"""Future Label Worker — Calculates offline future market outcomes.

Continuously scans the `snapshots` table in mos_research.db for snapshots
older than 30 minutes that lack a corresponding entry in `future_labels`.
Computes future returns, max excursions, and realized volatility.
"""

import sqlite3
import time
import os
import math
import logging
from typing import Optional, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
log = logging.getLogger(__name__)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db'))
WORKER_INTERVAL = 60  # seconds

def get_db_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def _get_price_at_offset(cursor: sqlite3.Cursor, base_ts: float, offset_sec: float) -> Optional[float]:
    """Finds the closest spot price near base_ts + offset_sec (within a 2-minute window)."""
    target_ts = base_ts + offset_sec
    cursor.execute('''
        SELECT spot_price FROM snapshots
        WHERE timestamp_utc BETWEEN ? AND ?
        ORDER BY ABS(timestamp_utc - ?) ASC
        LIMIT 1
    ''', (target_ts - 120, target_ts + 120, target_ts))
    row = cursor.fetchone()
    return row[0] if row else None

def _get_price_series(cursor: sqlite3.Cursor, start_ts: float, end_ts: float) -> List[float]:
    """Returns all spot prices between start_ts and end_ts."""
    cursor.execute('''
        SELECT spot_price FROM snapshots
        WHERE timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    ''', (start_ts, end_ts))
    return [row[0] for row in cursor.fetchall()]

def calculate_labels_for_snapshot(cursor: sqlite3.Cursor, ts: float, spot0: float) -> Optional[tuple]:
    """Calculates all future labels for a given snapshot timestamp."""
    
    # Check if we have enough future data (at least 30 mins)
    cursor.execute('SELECT MAX(timestamp_utc) FROM snapshots')
    max_ts = cursor.fetchone()[0]
    if not max_ts or max_ts < ts + 1800:
        return None # Not enough future data yet
        
    p_5m = _get_price_at_offset(cursor, ts, 300)
    p_15m = _get_price_at_offset(cursor, ts, 900)
    p_30m = _get_price_at_offset(cursor, ts, 1800)
    
    # If we are missing critical price snapshots, skip
    if not p_5m or not p_15m or not p_30m:
        return None
        
    ret_5m = (p_5m / spot0 - 1) * 100
    ret_15m = (p_15m / spot0 - 1) * 100
    ret_30m = (p_30m / spot0 - 1) * 100
    
    series_30m = _get_price_series(cursor, ts, ts + 1800)
    if not series_30m:
        return None
        
    max_price = max(series_30m)
    min_price = min(series_30m)
    
    max_up_30m = (max_price / spot0 - 1) * 100
    max_down_30m = (min_price / spot0 - 1) * 100
    
    range_30m = max_up_30m - max_down_30m
    breakout_strength = max(abs(max_up_30m), abs(max_down_30m))
    
    # Realized Volatility proxy (Standard Deviation of returns inside the 30m window)
    returns = []
    for i in range(1, len(series_30m)):
        returns.append((series_30m[i] / series_30m[i-1]) - 1)
        
    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret)**2 for r in returns) / (len(returns) - 1)
        # Annualized Vol proxy: stdev * sqrt(N_periods_in_year)
        # Assuming ~4 snapshots per minute (15s interval) -> 240 per hour -> 2,102,400 per year
        realized_vol_30m = math.sqrt(var) * math.sqrt(2102400) * 100
    else:
        realized_vol_30m = 0.0

    return (
        ts, ret_5m, ret_15m, ret_30m, 
        max_up_30m, max_down_30m, 
        realized_vol_30m, range_30m, breakout_strength
    )

def run_worker():
    log.info(f"Starting Future Label Worker for {DB_PATH}")
    log.info(f"Interval: {WORKER_INTERVAL} seconds")
    
    while True:
        try:
            if not os.path.exists(DB_PATH):
                log.warning(f"Database {DB_PATH} not found. Waiting...")
                time.sleep(WORKER_INTERVAL)
                continue
                
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Find snapshots older than 30 mins that have no future labels
            cursor.execute('''
                SELECT s.timestamp_utc, s.spot_price, s.snapshot_id, s.snapshot_sequence_id
                FROM snapshots s
                LEFT JOIN future_labels f ON s.timestamp_utc = f.timestamp_utc
                WHERE f.timestamp_utc IS NULL
                  AND s.timestamp_utc < ?
                ORDER BY s.timestamp_utc ASC
                LIMIT 500
            ''', (time.time() - 1800,))
            
            unlabeled = cursor.fetchall()
            if not unlabeled:
                conn.close()
                time.sleep(WORKER_INTERVAL)
                continue
                
            log.info(f"Found {len(unlabeled)} unlabeled snapshots. Calculating future labels...")
            
            inserted = 0
            for row in unlabeled:
                ts, spot, snap_id, snap_seq = row
                labels = calculate_labels_for_snapshot(cursor, ts, spot)
                if labels:
                    cursor.execute('''
                        INSERT INTO future_labels (
                            timestamp_utc, snapshot_id, snapshot_sequence_id,
                            future_return_5m, future_return_15m, future_return_30m,
                            future_max_up_30m, future_max_down_30m, future_realized_vol_30m,
                            future_range_30m, future_breakout_strength
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (labels[0], snap_id, snap_seq) + labels[1:])
                    inserted += 1
            
            conn.commit()
            conn.close()
            
            if inserted > 0:
                log.info(f"Successfully labeled {inserted} historical snapshots.")
                
        except Exception as e:
            log.error(f"Worker iteration failed: {e}")
            
        time.sleep(WORKER_INTERVAL)

if __name__ == "__main__":
    run_worker()
