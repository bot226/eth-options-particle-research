"""ETH Options Dashboard — FastAPI backend."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import API_HOST, API_PORT, CORS_ORIGINS, MULTI_EXCHANGE_ENABLED
from routes import market, websocket as ws_route
from engine.ohlcv_collector import OhlcvCollector
from workers.event_outcome_worker import EventOutcomeWorker
from workers.server_snapshot_worker import ServerSnapshotWorker

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Глобальные объекты
if MULTI_EXCHANGE_ENABLED:
    from engine.multi_data_manager import MultiExchangeDataManager
    from api.deribit_adapter import DeribitAdapter
    from api.bybit_adapter import BybitAdapter
    dm = MultiExchangeDataManager(adapters=[DeribitAdapter(), BybitAdapter()])
    bybit_ws = None
else:
    from engine.data_manager import DataManager
    from api.ws_client import BybitWebSocket
    dm = DataManager()
    bybit_ws = BybitWebSocket(on_ticker=dm.on_ws_ticker, on_spot=dm.on_ws_spot)

ohlcv_collector = OhlcvCollector()
event_outcome_worker = EventOutcomeWorker()
server_snapshot_worker = ServerSnapshotWorker(interval_sec=25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    log.info("Starting ETH Options Dashboard backend...")
    
    # --- DIAGNOSTICS ---
    import os
    import subprocess
    from pathlib import Path
    from engine.version import CODE_VERSION, RESEARCH_SCHEMA_VERSION, ENGINE_PATCH_VERSION, FLOW_PRESSURE_SCALE
    from engine.research_logger import ResearchLogger
    from engine.history_db import _DB_PATH as HISTORY_DB_PATH
    
    from engine.manual_logger import ManualLogger
    import sqlite3
    
    cwd = os.getcwd()
    research_db = Path(ResearchLogger().db_path).resolve() if not isinstance(ResearchLogger, type) else Path(os.path.join(cwd, "data", "mos_research.db")).resolve()
    
    manual_logger = ManualLogger()
    manual_db = Path(manual_logger.db_path).resolve()
    
    # Try to get git hash
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("utf-8").strip()
    except Exception:
        git_hash = "unknown"
        
    print("=" * 60)
    print(f"[MOS STARTUP] CWD: {cwd}")
    print(f"[MOS STARTUP] PID: {os.getpid()}")
    print(f"[MOS STARTUP] Manual DB: {manual_db}")
    print(f"[MOS STARTUP] Research DB: {research_db}")
    print(f"[MOS STARTUP] History DB: {Path(HISTORY_DB_PATH).resolve()}")
    print(f"[MOS STARTUP] ResearchLogger: {Path('engine/research_logger.py').resolve()}")
    print(f"[MOS STARTUP] Git Hash: {git_hash}")
    print(f"[MOS STARTUP] Code Version: {CODE_VERSION}")
    print(f"[MOS STARTUP] Schema Version: {RESEARCH_SCHEMA_VERSION}")
    print(f"[MOS STARTUP] Engine Patch: {ENGINE_PATCH_VERSION}")
    print(f"[MOS STARTUP] Flow Scale: {FLOW_PRESSURE_SCALE}")
    print("=" * 60)
    
    # Startup DB Schema Validation
    try:
        conn = sqlite3.connect(str(manual_db))
        c = conn.cursor()
        c.execute("PRAGMA table_info(manual_trading_snapshots)")
        cols = {row[1] for row in c.fetchall()}
        
        required_cols = ['entry_missing_conditions_json', 'entry_quality_components_json', 'entry_guard_reasons_json', 'entry_debug_json']
        missing = [rc for rc in required_cols if rc not in cols]
        if missing:
            log.error(f"[MOS STARTUP FATAL] manual_trading_snapshots schema mismatch! Missing columns: {missing}")
        else:
            log.info(f"[MOS STARTUP] manual_trading_snapshots schema validation passed.")
        conn.close()
    except Exception as e:
        log.error(f"[MOS STARTUP FATAL] Failed to validate schema: {e}")
    # --- END DIAGNOSTICS ---
    
    market.set_data_manager(dm)
    market.set_ohlcv_collector(ohlcv_collector)  # v52: share collector for OHLCV sync check
    ws_route.set_data_manager(dm)
    try:
        research.set_data_manager(dm)
    except NameError:
        pass
    await dm.start()
    await ohlcv_collector.start()
    await event_outcome_worker.start()
    await server_snapshot_worker.start()  # v52: 20-30s server-side snapshot writer
    if bybit_ws:
        await bybit_ws.start()
    log.info("Backend ready")
    yield
    log.info("Shutting down...")
    if bybit_ws:
        await bybit_ws.stop()
    await server_snapshot_worker.stop()
    await event_outcome_worker.stop()
    await ohlcv_collector.stop()
    await dm.stop()


app = FastAPI(
    title="ETH Options Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Маршруты
app.include_router(market.router)
app.include_router(ws_route.router)

try:
    from routes import research
    app.include_router(research.router)
except ImportError as e:
    log.warning(f"Failed to load research router: {e}")


@app.get("/")
async def root():
    return {"service": "ETH Options Dashboard", "status": "running"}


if __name__ == "__main__":
    import sys
    if "--show-entry-diagnostics-summary" in sys.argv:
        import os
        import sqlite3
        import json
        
        DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'data', 'mos_manual.db'))
        if not os.path.exists(DB_PATH):
            print("DB not found at:", DB_PATH)
            sys.exit(1)
            
        # Run migrations
        from engine.manual_logger import ManualLogger
        ManualLogger(db_path=DB_PATH)
            
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT count(*) FROM manual_trading_snapshots")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM manual_trading_snapshots WHERE manual_status = 'WATCH'")
        watch = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM manual_trading_snapshots WHERE manual_status = 'ENTRY_CANDIDATE'")
        entry = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM manual_trading_snapshots WHERE entry_block_reason IS NOT NULL AND entry_block_reason != 'unknown'")
        blocked = cursor.fetchone()[0]
        
        print(f"Total snapshots: {total}")
        print(f"WATCH count: {watch}")
        print(f"ENTRY_CANDIDATE count: {entry}")
        print(f"Blocked candidate count: {blocked}")
        
        print("\n--- entry_block_reason distribution ---")
        cursor.execute("SELECT entry_block_reason, count(*) as c FROM manual_trading_snapshots WHERE entry_block_reason IS NOT NULL GROUP BY entry_block_reason ORDER BY c DESC")
        for r in cursor.fetchall():
            print(f"  {r['entry_block_reason']}: {r['c']}")
            
        print("\n--- entry_block_stage distribution ---")
        cursor.execute("SELECT entry_block_stage, count(*) as c FROM manual_trading_snapshots WHERE entry_block_stage IS NOT NULL GROUP BY entry_block_stage ORDER BY c DESC")
        for r in cursor.fetchall():
            print(f"  {r['entry_block_stage']}: {r['c']}")
            
        print("\n--- setup_type x entry_block_reason table ---")
        cursor.execute("SELECT setup_type, entry_block_reason, count(*) as c FROM manual_trading_snapshots WHERE entry_block_reason IS NOT NULL GROUP BY setup_type, entry_block_reason ORDER BY setup_type, c DESC")
        last_setup = None
        for r in cursor.fetchall():
            if r['setup_type'] != last_setup:
                print(f"{r['setup_type']}:")
                last_setup = r['setup_type']
            print(f"  - {r['entry_block_reason']}: {r['c']}")

        print("\n--- Latest 20 blocked WATCH rows ---")
        cursor.execute("""
            SELECT ts, setup_type, manual_bias, level_result, selected_setup_level, price, 
                   entry_main_context_direction, entry_main_context_alignment, entry_block_reason, entry_missing_conditions_json
            FROM manual_trading_snapshots 
            WHERE manual_status = 'WATCH' AND entry_block_reason IS NOT NULL AND entry_block_reason != 'unknown'
            ORDER BY id DESC LIMIT 20
        """)
        for r in cursor.fetchall():
            print(f"[{r['ts']}] {r['setup_type']} {r['manual_bias']} Lvl:{r['selected_setup_level']} Px:{r['price']}")
            print(f"    Align: {r['entry_main_context_alignment']} | Reason: {r['entry_block_reason']} | Missing: {r['entry_missing_conditions_json']}")
            
        sys.exit(0)

    import uvicorn
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
