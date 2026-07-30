"""Export Dataset CLI — Extracts MOS Research Database to CSV for offline ML/AI analysis."""

import sqlite3
import pandas as pd
import argparse
import os

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db'))

def export_snapshots(output_path: str):
    """Exports snapshots and future labels to a single CSV."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    query = '''
        SELECT s.*, 
               f.future_return_5m, f.future_return_15m, f.future_return_30m,
               f.future_max_up_30m, f.future_max_down_30m, f.future_realized_vol_30m,
               f.future_range_30m, f.future_breakout_strength
        FROM snapshots s
        LEFT JOIN future_labels f ON s.timestamp = f.timestamp
        ORDER BY s.timestamp ASC
    '''
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df.to_csv(output_path, index=False)
    print(f"Success! Exported {len(df)} snapshots to {output_path}")

def export_events(output_path: str):
    """Exports institutional intelligence events."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM events ORDER BY timestamp ASC", conn)
    conn.close()
    
    df.to_csv(output_path, index=False)
    print(f"Success! Exported {len(df)} events to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MOS Research Dataset Exporter")
    parser.add_argument("--type", choices=["snapshots", "events", "all"], default="all", help="What to export")
    parser.add_argument("--outdir", default="data", help="Output directory")
    args = parser.parse_args()
    
    os.makedirs(args.outdir, exist_ok=True)
    
    if args.type in ["snapshots", "all"]:
        export_snapshots(os.path.join(args.outdir, "mos_research_snapshots.csv"))
        
    if args.type in ["events", "all"]:
        export_events(os.path.join(args.outdir, "mos_research_events.csv"))
