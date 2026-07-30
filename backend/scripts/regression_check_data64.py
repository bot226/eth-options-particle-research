import sys
import os
import sqlite3
import shutil
import math
import calendar
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine.paper_trade_engine import PaperTradeEngine

def run_regression():
    data_dir = r"C:\Users\User\Desktop\data64"
    if not os.path.exists(data_dir):
        print("data64 directory not found!")
        return

    manual_db_orig = os.path.join(data_dir, "mos_manual.db")
    research_db = os.path.join(data_dir, "mos_research.db")
    
    # Create a copy to avoid modifying the original data64 zip contents directly
    test_db = os.path.join(data_dir, "mos_manual_test.db")
    shutil.copy2(manual_db_orig, test_db)
    
    # 1. Reset the trade back to OPEN
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM paper_trades WHERE entry_snapshot_id = 141")
    row = c.fetchone()
    if not row:
        print("Trade not found!")
        return
        
    print(f"Original status: {row['status']}, Exit Reason: {row['exit_reason']}")
    
    entry_price = float(row['entry_price'])
    entry_ts_str = row['entry_ts']
    if entry_ts_str.endswith('Z'):
        entry_ts_str = entry_ts_str[:-1]
    if '.' in entry_ts_str:
        entry_ts_str = entry_ts_str.split('.')[0]
    try:
        entry_ts_epoch = calendar.timegm(time.strptime(entry_ts_str, '%Y-%m-%dT%H:%M:%S'))
    except Exception:
        entry_ts_epoch = 0
    earliest_allowed_candle_ts = math.floor(entry_ts_epoch / 60) * 60 + 60
    
    c.execute("""
        UPDATE paper_trades 
        SET status = 'OPEN', 
            exit_ts = NULL, 
            exit_price = NULL, 
            exit_reason = NULL, 
            result_r = 0.0, 
            max_mfe_r = 0.0, 
            max_mae_r = 0.0, 
            highest_price_seen = ?, 
            lowest_price_seen = ?, 
            candles_processed_count = 0, 
            last_processed_candle_ts = ?, 
            last_update_candle_ts = NULL, 
            paper_update_last_error = NULL, 
            stop_touched_flag = 0, 
            tp_touched_flag = 0
        WHERE entry_snapshot_id = 141
    """, (entry_price, entry_price, earliest_allowed_candle_ts - 60))
    
    c.execute("DELETE FROM paper_trade_events WHERE paper_trade_id = ?", (row['id'],))
    
    conn.commit()
    conn.close()
    
    # 2. Run engine
    engine = PaperTradeEngine(test_db)
    
    # Load OPEN trades
    engine.start()
    if row['id'] not in engine.open_trades:
        print("Failed to load OPEN trade into engine.")
        return
        
    print("Running update_open_trades_from_db...")
    engine.update_open_trades_from_db(research_db)
    
    # 3. Verify results
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM paper_trades WHERE entry_snapshot_id = 141")
    row_after = c.fetchone()
    
    print("\n--- After update ---")
    print(f"Status: {row_after['status']}")
    print(f"Exit TS: {row_after['exit_ts']}")
    print(f"Exit Reason: {row_after['exit_reason']}")
    print(f"Result R: {row_after['result_r']}")
    print(f"Highest Price: {row_after['highest_price_seen']}")
    print(f"Lowest Price: {row_after['lowest_price_seen']}")
    print(f"Last Processed Candle TS: {row_after['last_processed_candle_ts']}")
    print(f"Candles Processed: {row_after['candles_processed_count']}")
    print(f"TP Touched Flag: {row_after['tp_touched_flag']}")
    
    assert row_after['status'] == 'OPEN' or (row_after['status'] == 'CLOSED' and row_after['exit_ts'] > '2026-06-17T20:37:51Z')
    
    # Check if TP1 was touched (it should be 64236.3)
    if row_after['lowest_price_seen'] <= 64236.3:
        print("SUCCESS: Lowest price seen touched TP1!")
        
    if row_after['exit_ts'] and row_after['exit_ts'] == '2026-06-17T02:02:00Z':
        print("FAILURE: Trade was incorrectly closed with old candle!")
        sys.exit(1)
        
    if row_after['highest_price_seen'] > 64605.5:
        print("NOTE: Stop was touched in new candles.")
    
    print("\nREGRESSION CHECK PASSED!")

if __name__ == "__main__":
    run_regression()
