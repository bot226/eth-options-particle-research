import sqlite3
import os
import time
import json
from datetime import datetime, timedelta
import calendar

from engine.paper_trade_engine import PaperTradeEngine

def test_paper_trade_engine():
    db_path = os.path.abspath("test_pte.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    from engine.manual_logger import ManualLogger
    ml = ManualLogger(db_path=db_path)
    # The ml init automatically creates all tables and migrations

    pte = PaperTradeEngine(db_path)
    
    # 1. Create trade with entry_ts
    entry_time_str = "2026-06-15T07:02:39Z"
    entry_epoch = calendar.timegm(time.strptime(entry_time_str, '%Y-%m-%dT%H:%M:%SZ'))
    
    # Mock snapshot
    snapshot_payload = {
        "manual_status": "ENTRY_CANDIDATE",
        "candidate_is_new": 1,
        "entry_execution_price": 65000,
        "protective_stop_execution_price": 64000,
        "tp1_execution_price": 66000,
        "tp2_execution_price": 67000,
        "tp3_execution_price": 68000,
        "manual_bias": "LONG",
        "candidate_key": "CANDIDATE_1",
        "selected_setup_level": 65000,
        "ts": entry_time_str,
    }

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO manual_trading_snapshots (id, ts) VALUES (1, ?)", (entry_time_str,))
    conn.commit()
    conn.close()

    pte.process_snapshot(1, snapshot_payload)
    assert len(pte.open_trades) == 1, "Should open 1 trade"
    trade_id = list(pte.open_trades.keys())[0]

    # 2. Update with candles before entry_ts
    old_candle_ts = entry_epoch - 3600
    old_candle = {
        "exchange": "bybit",
        "market_type": "linear",
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "ohlcv_source": "bybit_linear_ethusdt",
        "candle_source_verified": 1,
        "timestamp_utc": old_candle_ts,
        "open": 63000,
        "high": 63500,
        "low": 62500, # This would hit stop if not filtered
        "close": 63000
    }
    pte.update_trades([old_candle])

    # Assert no MFE/MAE/exit changes
    trade = pte.open_trades[trade_id]
    assert trade["status"] == "OPEN", "Trade should be OPEN"
    assert trade["max_mfe_r"] == 0.0, "MFE should be 0.0"
    assert trade["max_mae_r"] == 0.0, "MAE should be 0.0"

    # 3. Update with candle after entry touching TP1
    tp1_candle_ts = entry_epoch + 60
    tp1_candle = {
        "exchange": "bybit",
        "market_type": "linear",
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "ohlcv_source": "bybit_linear_ethusdt",
        "candle_source_verified": 1,
        "timestamp_utc": tp1_candle_ts,
        "open": 65000,
        "high": 66500, # Touches TP1
        "low": 64500,
        "close": 66000
    }
    pte.update_trades([tp1_candle])

    # Assert TP1_TOUCH once
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM paper_trade_events WHERE event_type = 'TP1_TOUCH'")
    tp1_events = cursor.fetchall()
    assert len(tp1_events) == 1, "Should have 1 TP1_TOUCH event"

    # 4. Update same candle again
    pte.update_trades([tp1_candle])
    
    # Assert no duplicate TP1_TOUCH
    cursor.execute("SELECT * FROM paper_trade_events WHERE event_type = 'TP1_TOUCH'")
    tp1_events = cursor.fetchall()
    assert len(tp1_events) == 1, "Should still have 1 TP1_TOUCH event after duplicate candle"

    # 5. Update with stop candle
    stop_candle_ts = entry_epoch + 120
    stop_candle = {
        "exchange": "bybit",
        "market_type": "linear",
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "ohlcv_source": "bybit_linear_ethusdt",
        "candle_source_verified": 1,
        "timestamp_utc": stop_candle_ts,
        "open": 66000,
        "high": 66000,
        "low": 63500, # Hits stop (64000)
        "close": 63800
    }
    pte.update_trades([stop_candle])

    # Assert one STOP_CLOSE, status CLOSED, result_r=-1.0
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,))
    pt = dict(cursor.fetchone())
    assert pt["status"] == "CLOSED", "Status should be CLOSED"
    assert pt["result_r"] == -1.0, "Result R should be -1.0"
    assert pt["exit_reason"] == "STOP", "Exit reason should be STOP"

    cursor.execute("SELECT * FROM paper_trade_events WHERE event_type = 'STOP_CLOSE'")
    stop_events = cursor.fetchall()
    assert len(stop_events) == 1, "Should have 1 STOP_CLOSE event"

    # 6. Update more candles after CLOSED
    after_stop_candle_ts = entry_epoch + 180
    after_stop_candle = {
        "exchange": "bybit",
        "market_type": "linear",
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "ohlcv_source": "bybit_linear_ethusdt",
        "candle_source_verified": 1,
        "timestamp_utc": after_stop_candle_ts,
        "open": 63000,
        "high": 69000, # Would hit TP3
        "low": 62000,
        "close": 68000
    }
    
    pte.update_trades([after_stop_candle])

    cursor.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,))
    pt_after = dict(cursor.fetchone())
    assert pt_after["result_r"] == -1.0, "Result R should still be -1.0"
    assert pt_after["exit_reason"] == "STOP", "Exit reason should still be STOP"

    # 7. Create second same-zone ENTRY while first OPEN
    snapshot_payload_1 = snapshot_payload.copy()
    snapshot_payload_1["ts"] = "2026-06-15T08:00:00Z"
    cursor.execute("INSERT INTO manual_trading_snapshots (id, ts) VALUES (2, ?)", (snapshot_payload_1["ts"],))
    conn.commit()
    pte.process_snapshot(2, snapshot_payload_1)
    
    assert len(pte.open_trades) == 1, "Should have 1 open trade"

    snapshot_payload_2 = snapshot_payload.copy()
    snapshot_payload_2["ts"] = "2026-06-15T08:05:00Z"
    cursor.execute("INSERT INTO manual_trading_snapshots (id, ts) VALUES (3, ?)", (snapshot_payload_2["ts"],))
    conn.commit()
    pte.process_snapshot(3, snapshot_payload_2)

    # Assert skipped ACTIVE_SAME_ZONE
    assert len(pte.open_trades) == 1, "Should still have 1 open trade"
    cursor.execute("SELECT paper_trade_skipped_reason FROM manual_trading_snapshots WHERE id = 3")
    skipped = cursor.fetchone()[0]
    assert skipped == 'ACTIVE_SAME_ZONE', "Should be skipped as ACTIVE_SAME_ZONE"
    
    # 8. Test context exit logic
    snapshot_payload_3 = snapshot_payload.copy()
    snapshot_payload_3["ts"] = "2026-06-15T09:00:00Z"
    snapshot_payload_3["candidate_key"] = "CANDIDATE_2"
    snapshot_payload_3["selected_setup_level"] = 60000
    cursor.execute("INSERT INTO manual_trading_snapshots (id, ts) VALUES (4, ?)", (snapshot_payload_3["ts"],))
    conn.commit()
    pte.process_snapshot(4, snapshot_payload_3)
    
    assert len(pte.open_trades) == 2, "Should have 2 open trades"
    context_trade_id = list(pte.open_trades.keys())[1]
    
    # Insert context update snapshot after entry
    context_ts = "2026-06-15T09:05:00Z"
    cursor.execute("""
        INSERT INTO manual_trading_snapshots 
        (id, ts, latest_main_context_direction, latest_main_context_ts) 
        VALUES (5, ?, 'SHORT', ?)
    """, (context_ts, context_ts))
    conn.commit()
    
    # Move price to profit >= 0.5R
    context_candle_ts = calendar.timegm(time.strptime(context_ts, '%Y-%m-%dT%H:%M:%SZ')) + 60
    context_candle = {
        "exchange": "bybit",
        "market_type": "linear",
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "ohlcv_source": "bybit_linear_ethusdt",
        "candle_source_verified": 1,
        "timestamp_utc": context_candle_ts,
        "open": 65000,
        "high": 65600,
        "low": 65000,
        "close": 65600 # 600 profit on 1000 risk = 0.6R
    }
    pte.update_trades([context_candle])
    
    cursor.execute("SELECT * FROM paper_trades WHERE id = ?", (context_trade_id,))
    pt_ctx = dict(cursor.fetchone())
    assert pt_ctx["status"] == "CLOSED", "Should be CLOSED by context exit"
    assert pt_ctx["exit_reason"] == "CONTEXT_EXIT", "Exit reason should be CONTEXT_EXIT"

    conn.close()

    if os.path.exists(db_path):
        os.remove(db_path)
    print("ALL TESTS PASSED")

if __name__ == "__main__":
    test_paper_trade_engine()
