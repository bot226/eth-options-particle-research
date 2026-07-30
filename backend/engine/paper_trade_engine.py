import sqlite3
import logging
import json
import time
import calendar
from typing import Dict, Any, List

log = logging.getLogger(__name__)

PAPER_CONTEXT_EXIT_ENABLED = True
PAPER_CONTEXT_EXIT_MIN_PROFIT_R = 0.5
PAPER_CONTEXT_EXIT_REQUIRE_OPPOSITE_DIRECTION = True

def _parse_iso_ts(ts_str: str) -> int:
    if not ts_str:
        return 0
    if ts_str.endswith('Z'):
        ts_str = ts_str[:-1]
    if '.' in ts_str:
        ts_str = ts_str.split('.')[0]
    try:
        return calendar.timegm(time.strptime(ts_str, '%Y-%m-%dT%H:%M:%S'))
    except Exception:
        return 0

class PaperTradeEngine:
    _instance = None

    @classmethod
    def get_instance(cls, db_path: str):
        if cls._instance is None:
            cls._instance = PaperTradeEngine(db_path)
        return cls._instance

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.open_trades = {}  # trade_id -> dict
        self._init_db()
        self.start()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()

        # Add skipped reason to snapshots
        cursor.execute("PRAGMA table_info(manual_trading_snapshots)")
        existing = {row[1] for row in cursor.fetchall()}
        if "paper_trade_skipped_reason" not in existing:
            cursor.execute("ALTER TABLE manual_trading_snapshots ADD COLUMN paper_trade_skipped_reason TEXT")

        cursor.execute("PRAGMA table_info(paper_trades)")
        existing_pt = {row[1] for row in cursor.fetchall()}
        for col, col_type in [
            ("candles_processed_count", "INTEGER DEFAULT 0"),
            ("last_processed_candle_ts", "REAL"),
            ("paper_update_last_error", "TEXT"),
            ("stop_touched_flag", "INTEGER DEFAULT 0"),
            ("tp_touched_flag", "INTEGER DEFAULT 0")
        ]:
            if col not in existing_pt:
                try:
                    cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_snapshot_id INTEGER,
                entry_snapshot_ts TEXT,
                zone_key TEXT,
                exit_model TEXT,
                last_update_candle_ts REAL,
                last_update_source TEXT,
                candidate_key TEXT,
                setup_type TEXT,
                side TEXT,
                entry_ts TEXT,
                entry_price REAL,
                entry_execution_price REAL,
                protective_stop_execution_price REAL,
                tp1_execution_price REAL,
                tp2_execution_price REAL,
                tp3_execution_price REAL,
                risk_abs REAL,
                risk_pct REAL,
                selected_setup_level REAL,
                selected_setup_side TEXT,
                invalidation_level REAL,
                live_support_level REAL,
                live_resistance_level REAL,
                nearest_level REAL,
                primary_live_level REAL,
                primary_live_side TEXT,
                state TEXT,
                execution TEXT,
                flow TEXT,
                setup_quality TEXT,
                actionability TEXT,
                latest_main_context_direction TEXT,
                latest_main_context_reaction_label TEXT,
                latest_main_context_level_side TEXT,
                latest_main_context_level_price REAL,
                latest_main_context_ts TEXT,
                latest_main_context_age_sec REAL,
                main_context_conflict_active INTEGER,
                status TEXT,
                exit_ts TEXT,
                exit_price REAL,
                exit_reason TEXT,
                result_r REAL,
                max_mfe_r REAL,
                max_mae_r REAL,
                highest_price_seen REAL,
                lowest_price_seen REAL,
                candles_processed_count INTEGER DEFAULT 0,
                last_processed_candle_ts REAL,
                paper_update_last_error TEXT,
                stop_touched_flag INTEGER DEFAULT 0,
                tp_touched_flag INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trade_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_trade_id INTEGER,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                price REAL,
                result_r REAL,
                details_json TEXT
            )
        ''')

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pt_entry_model ON paper_trades(entry_snapshot_id, exit_model)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pt_status_side_zone ON paper_trades(status, side, zone_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pte_trade_id ON paper_trade_events(paper_trade_id)")

        conn.commit()
        conn.close()

    def start(self):
        """Safe startup: Load OPEN trades into memory."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM paper_trades WHERE status = 'OPEN'")
        rows = cursor.fetchall()
        for r in rows:
            trade = dict(r)
            trade_id = trade['id']
            # Track touches for marker events
            cursor.execute("SELECT event_type FROM paper_trade_events WHERE paper_trade_id = ?", (trade_id,))
            events = {ev[0] for ev in cursor.fetchall()}
            trade['_tp1_touched'] = 'TP1_TOUCH' in events
            trade['_tp2_touched'] = 'TP2_TOUCH' in events
            trade['_tp3_touched'] = 'TP3_TOUCH' in events
            
            trade['_entry_ts_epoch'] = _parse_iso_ts(trade.get('entry_ts'))
                
            self.open_trades[trade_id] = trade
        conn.close()
        log.info(f"[PaperTradeEngine] Loaded {len(self.open_trades)} OPEN paper trades.")

    def _get_zone_key(self, side: str, level: float) -> str:
        if level is None:
            return f"UNKNOWN_ZONE_{side}"
        # Rounding to nearest 50 for zone
        rounded_level = round(level / 50.0) * 50.0
        return f"{side}_{int(rounded_level)}"

    def _log_event(self, cursor, trade_id: int, event_type: str, price: float, result_r: float, details: dict):
        ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        cursor.execute('''
            INSERT INTO paper_trade_events (paper_trade_id, ts, event_type, price, result_r, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (trade_id, ts, event_type, price, result_r, json.dumps(details) if details else None))

    def process_snapshot(self, snapshot_id: int, payload: Dict[str, Any]):
        """Evaluate snapshot for opening a paper trade."""
        
        def _update_diag(result: str, skipped_reason: str = None, error: str = None, key: str = None):
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE manual_trading_snapshots
                    SET paper_trade_open_attempted = 1,
                        paper_trade_open_result = ?,
                        paper_trade_skipped_reason = ?,
                        paper_trade_open_error = ?,
                        paper_trade_candidate_key = ?,
                        paper_trade_candidate_is_new = ?
                    WHERE id = ?
                """, (result, skipped_reason, error, key, payload.get("candidate_is_new", 0), snapshot_id))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[PaperTradeEngine] Diag update failed: {e}")
                

        if payload.get("manual_status") != "ENTRY_CANDIDATE":
            return
        if str(payload.get("candidate_is_new", 0)) != "1":
            return

        candidate_key = payload.get("candidate_key")
        
        entry_price = payload.get("entry_execution_price")
        stop_price = payload.get("protective_stop_execution_price")
        if stop_price is None:
            stop_price = payload.get("invalidation_level")
            
        if entry_price is None or stop_price is None:
            _update_diag("SKIPPED", skipped_reason="missing_prices", key=candidate_key)
            return

        exit_model = "CONTEXT_EXIT_V1"
        side = payload.get("manual_bias")  # LONG / SHORT
        if side not in ("LONG", "SHORT"):
            _update_diag("SKIPPED", skipped_reason="invalid_side", key=candidate_key)
            return
            
        tp3_price = payload.get("tp3_execution_price")
        if tp3_price is None:
            risk_abs = abs(entry_price - stop_price)
            tp3_price = entry_price + (risk_abs * 3) if side == "LONG" else entry_price - (risk_abs * 3)


        candidate_key = payload.get("candidate_key")
        selected_level = payload.get("selected_setup_level")
        zone_key = self._get_zone_key(side, selected_level) if selected_level else candidate_key

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()

        try:
            # Active trade guard
            cursor.execute("""
                SELECT id FROM paper_trades 
                WHERE status = 'OPEN' AND (
                    (side = ? AND zone_key = ?) OR candidate_key = ?
                )
            """, (side, zone_key, candidate_key))
            
            existing = cursor.fetchone()
            if existing:
                # Same zone active
                _update_diag("SKIPPED", skipped_reason="ACTIVE_SAME_ZONE", key=candidate_key)
                self._log_event(cursor, None, 'SKIPPED_ACTIVE_SAME_ZONE', entry_price, None, {
                    'entry_snapshot_id': snapshot_id,
                    'zone_key': zone_key,
                    'candidate_key': candidate_key,
                    'active_trade_id': existing[0]
                })
                conn.commit()
                return

            # Idempotency check (entry_snapshot_id + exit_model)
            cursor.execute("SELECT id FROM paper_trades WHERE entry_snapshot_id = ? AND exit_model = ?", (snapshot_id, exit_model))
            if cursor.fetchone():
                _update_diag("SKIPPED", skipped_reason="ALREADY_PROCESSED", key=candidate_key)
                return

            risk_abs = abs(entry_price - stop_price)
            risk_pct = (risk_abs / entry_price * 100) if entry_price else 0

            import math
            entry_ts_str = payload.get("ts")
            entry_ts_epoch = _parse_iso_ts(entry_ts_str)
            earliest_allowed_candle_ts = math.floor(entry_ts_epoch / 60) * 60 + 60

            fields = {
                'entry_snapshot_id': snapshot_id,
                'entry_snapshot_ts': payload.get("ts"),
                'zone_key': zone_key,
                'exit_model': exit_model,
                'candidate_key': candidate_key,
                'setup_type': payload.get("setup_type") or payload.get("manual_setup_type"),
                'side': side,
                'entry_ts': payload.get("ts"),
                'entry_price': entry_price,
                'entry_execution_price': entry_price,
                'protective_stop_execution_price': stop_price,
                'tp1_execution_price': payload.get("tp1_execution_price"),
                'tp2_execution_price': payload.get("tp2_execution_price"),
                'tp3_execution_price': tp3_price,
                'risk_abs': risk_abs,
                'risk_pct': risk_pct,
                'selected_setup_level': selected_level,
                'selected_setup_side': payload.get("selected_setup_side"),
                'invalidation_level': payload.get("invalidation_level"),
                'live_support_level': payload.get("live_support_level"),
                'live_resistance_level': payload.get("live_resistance_level"),
                'nearest_level': payload.get("nearest_level"),
                'primary_live_level': payload.get("primary_live_level"),
                'primary_live_side': payload.get("primary_live_side"),
                'state': payload.get("current_state"),
                'execution': payload.get("execution_timing_state"),
                'flow': payload.get("short_term_flow_direction"),
                'setup_quality': payload.get("setup_quality"),
                'actionability': payload.get("actionability"),
                'latest_main_context_direction': payload.get("latest_main_context_direction"),
                'latest_main_context_reaction_label': payload.get("latest_main_context_reaction_label"),
                'latest_main_context_level_side': payload.get("latest_main_context_level_side"),
                'latest_main_context_level_price': payload.get("latest_main_context_level_price"),
                'latest_main_context_ts': payload.get("latest_main_context_ts"),
                'latest_main_context_age_sec': payload.get("latest_main_context_age_sec"),
                'main_context_conflict_active': payload.get("main_context_conflict_active"),
                'status': 'OPEN',
                'result_r': 0.0,
                'max_mfe_r': 0.0,
                'max_mae_r': 0.0,
                'highest_price_seen': entry_price,
                'lowest_price_seen': entry_price,
                'last_update_candle_ts': None,
                'last_processed_candle_ts': earliest_allowed_candle_ts - 60,
            }

            cols = ", ".join(fields.keys())
            places = ", ".join("?" for _ in fields)
            cursor.execute(f"INSERT INTO paper_trades ({cols}) VALUES ({places})", tuple(fields.values()))
            trade_id = cursor.lastrowid

            self._log_event(cursor, trade_id, 'OPEN', entry_price, 0.0, {
                'zone_key': zone_key,
                'candidate_key': candidate_key
            })
            
            conn.commit()

            # Add to memory
            trade = dict(fields)
            trade['id'] = trade_id
            trade['_tp1_touched'] = False
            trade['_tp2_touched'] = False
            trade['_tp3_touched'] = False
            
            trade['_entry_ts_epoch'] = _parse_iso_ts(trade.get('entry_ts'))
                
            self.open_trades[trade_id] = trade
            
            _update_diag("OPENED", key=candidate_key)

        except Exception as e:
            log.error(f"[PaperTradeEngine] Error processing snapshot: {e}")
            _update_diag("ERROR", error=str(e), key=candidate_key)
        finally:
            conn.close()

    def update_open_trades_from_db(self, research_db_path: str):
        """Update OPEN trades by fetching sequential candles from the mos_research.db"""
        if not self.open_trades:
            return

        import math
        min_ts = float('inf')
        for trade in self.open_trades.values():
            entry_ts_epoch = trade.get("_entry_ts_epoch", 0)
            earliest_allowed_candle_ts = math.floor(entry_ts_epoch / 60) * 60 + 60
            
            if trade.get("last_update_candle_ts"):
                trade_ts = trade["last_update_candle_ts"]
                if trade_ts < earliest_allowed_candle_ts:
                    trade_ts = earliest_allowed_candle_ts
            else:
                trade_ts = earliest_allowed_candle_ts

            if trade_ts < min_ts:
                min_ts = trade_ts

        if min_ts == float('inf'):
            return

        r_conn = sqlite3.connect(research_db_path, timeout=30.0)
        r_conn.row_factory = sqlite3.Row
        r_cursor = r_conn.cursor()

        try:
            r_cursor.execute('''
                SELECT timestamp_utc, open, high, low, close 
                FROM ohlcv_candles
                WHERE exchange = 'bybit' AND market_type = 'linear' 
                  AND symbol = 'BTCUSDT' AND timeframe = '1m'
                  AND ohlcv_source = 'bybit_linear_btcusdt'
                  AND candle_source_verified = 1
                  AND timestamp_utc >= ?
                ORDER BY timestamp_utc ASC
            ''', (min_ts,))
            
            candles = []
            for row in r_cursor.fetchall():
                candles.append({
                    "timestamp_utc": float(row["timestamp_utc"]),
                    "exchange": "bybit",
                    "market_type": "linear",
                    "symbol": "BTCUSDT",
                    "timeframe": "1m",
                    "ohlcv_source": "bybit_linear_btcusdt",
                    "candle_source_verified": 1,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"])
                })
        except Exception as e:
            log.error(f"[PaperTradeEngine] Error fetching candles from research DB: {e}")
            r_conn.close()
            return
            
        r_conn.close()

        if candles:
            self.update_trades(candles)

    def update_trades(self, candles: List[Dict[str, Any]]):
        """Update OPEN trades with new Bybit 1m candles."""
        if not self.open_trades:
            return

        # STRICT FILTERING
        valid_candles = []
        for c in candles:
            if (c.get("exchange") == "bybit" and
                c.get("market_type") == "linear" and
                c.get("symbol") == "BTCUSDT" and
                c.get("timeframe") == "1m" and
                c.get("ohlcv_source") == "bybit_linear_btcusdt" and
                str(c.get("candle_source_verified", 0)) == "1"):
                valid_candles.append(c)
        
        if not valid_candles:
            return

        # Sort chronological
        valid_candles.sort(key=lambda x: x.get("timestamp_utc", 0))

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()

        try:
            for candle in valid_candles:
                ts = candle.get("timestamp_utc")
                high = float(candle.get("high"))
                low = float(candle.get("low"))
                close = float(candle.get("close"))

                closed_ids = []

                for trade_id, trade in self.open_trades.items():
                    if trade.get("status") == "CLOSED":
                        continue

                    import math
                    entry_ts_epoch = trade.get("_entry_ts_epoch", 0)
                    earliest_allowed_candle_ts = math.floor(entry_ts_epoch / 60) * 60 + 60
                        
                    if ts < earliest_allowed_candle_ts:
                        continue

                    # Idempotency check
                    last_ts = trade.get("last_update_candle_ts")
                    if last_ts is not None and ts < last_ts:
                        continue

                    is_new_candle = (last_ts is None or ts > last_ts)

                    # Context Exit Check (Only if not stopped/tp3)
                    is_context_exit = False
                    
                    try:
                        side = trade["side"]
                        entry = float(trade["entry_price"])
                        stop = float(trade["protective_stop_execution_price"])
                        tp1 = float(trade["tp1_execution_price"]) if trade.get("tp1_execution_price") else None
                        tp2 = float(trade["tp2_execution_price"]) if trade.get("tp2_execution_price") else None
                        tp3 = float(trade["tp3_execution_price"])
                        risk = float(trade["risk_abs"])

                        if risk <= 0:
                            continue

                        # Track max_mfe_r and max_mae_r explicitly
                        max_mfe_r = float(trade.get("max_mfe_r", 0.0))
                        max_mae_r = float(trade.get("max_mae_r", 0.0))
                        
                        if side == "LONG":
                            h_seen = max(float(trade.get("highest_price_seen", entry)), high)
                            l_seen = min(float(trade.get("lowest_price_seen", entry)), low)
                            
                            trade["highest_price_seen"] = h_seen
                            trade["lowest_price_seen"] = l_seen
                            
                            trade["max_mfe_r"] = max(max_mfe_r, (h_seen - entry) / risk)
                            trade["max_mae_r"] = max(max_mae_r, (entry - l_seen) / risk)
                            trade["result_r"] = (close - entry) / risk
                            
                        else:
                            l_seen = min(float(trade.get("lowest_price_seen", entry)), low)
                            h_seen = max(float(trade.get("highest_price_seen", entry)), high)
                            
                            trade["lowest_price_seen"] = l_seen
                            trade["highest_price_seen"] = h_seen
                            
                            trade["max_mfe_r"] = max(max_mfe_r, (entry - l_seen) / risk)
                            trade["max_mae_r"] = max(max_mae_r, (h_seen - entry) / risk)
                            trade["result_r"] = (entry - close) / risk

                        trade["last_update_candle_ts"] = ts
                        trade["last_update_source"] = "bybit_linear_btcusdt"

                        # Exit logic
                        is_stop = False
                        is_tp3 = False
                        
                        if side == "LONG":
                            is_stop = (low <= stop)
                            is_tp3 = (high >= tp3)
                            is_tp1 = tp1 and (high >= tp1)
                            is_tp2 = tp2 and (high >= tp2)
                        else:
                            is_stop = (high >= stop)
                            is_tp3 = (low <= tp3)
                            is_tp1 = tp1 and (low <= tp1)
                            is_tp2 = tp2 and (low <= tp2)

                        # Context Exit Check (Only if not stopped/tp3)
                        is_context_exit = False
                        if not is_stop and not is_tp3 and PAPER_CONTEXT_EXIT_ENABLED:
                            if trade["result_r"] >= PAPER_CONTEXT_EXIT_MIN_PROFIT_R:
                                candle_ts_str = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
                                entry_ts_str = trade.get("entry_ts")
                                
                                cursor.execute('''
                                    SELECT latest_main_context_direction 
                                    FROM manual_trading_snapshots 
                                    WHERE ts <= ? 
                                      AND latest_main_context_ts >= ?
                                      AND latest_main_context_direction IS NOT NULL
                                    ORDER BY ts DESC LIMIT 1
                                ''', (candle_ts_str, entry_ts_str))
                                row = cursor.fetchone()
                                if row:
                                    main_dir = row[0]
                                    if PAPER_CONTEXT_EXIT_REQUIRE_OPPOSITE_DIRECTION:
                                        if side == "LONG" and main_dir == "SHORT":
                                            is_context_exit = True
                                        elif side == "SHORT" and main_dir == "LONG":
                                            is_context_exit = True
                                    else:
                                        is_context_exit = True

                        # Priority: STOP -> TP3 -> CONTEXT -> TP1/TP2 Markers
                        close_reason = None
                        close_price = None

                        if is_stop:
                            close_reason = "STOP"
                            close_price = stop
                        elif is_tp3:
                            close_reason = "TP3"
                            close_price = tp3
                        elif is_context_exit:
                            close_reason = "CONTEXT_EXIT"
                            close_price = close

                        if close_reason:
                            if ts < earliest_allowed_candle_ts:
                                log.error(f"[PaperTradeEngine] Invariant failed: exit_candle_ts {ts} < {earliest_allowed_candle_ts} for trade {trade_id}")
                                close_reason = None
                                
                        # Log marker events unconditionally before closing
                        if is_tp1 and not trade.get("_tp1_touched"):
                            trade["_tp1_touched"] = True
                            self._log_event(cursor, trade_id, "TP1_TOUCH", tp1, (tp1 - entry)/risk if side=="LONG" else (entry - tp1)/risk, None)
                        if is_tp2 and not trade.get("_tp2_touched"):
                            trade["_tp2_touched"] = True
                            self._log_event(cursor, trade_id, "TP2_TOUCH", tp2, (tp2 - entry)/risk if side=="LONG" else (entry - tp2)/risk, None)
                        if is_tp3 and not trade.get("_tp3_touched"):
                            trade["_tp3_touched"] = True
                            self._log_event(cursor, trade_id, "TP3_TOUCH", tp3, (tp3 - entry)/risk if side=="LONG" else (entry - tp3)/risk, None)

                        if close_reason:
                            # Recalculate final R based on exit price
                            if close_reason == "STOP":
                                final_r = -1.0
                            else:
                                if side == "LONG":
                                    final_r = (close_price - entry) / risk
                                else:
                                    final_r = (entry - close_price) / risk

                            trade["status"] = "CLOSED"
                            trade["exit_reason"] = close_reason
                            trade["exit_price"] = close_price
                            trade["exit_ts"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
                            trade["result_r"] = final_r
                            
                            if is_new_candle:
                                trade["candles_processed_count"] = trade.get("candles_processed_count", 0) + 1
                            trade["last_processed_candle_ts"] = ts
                            trade["stop_touched_flag"] = trade.get("stop_touched_flag", 0) | (1 if is_stop else 0)
                            trade["tp_touched_flag"] = trade.get("tp_touched_flag", 0) | (1 if (is_tp1 or is_tp2 or is_tp3) else 0)
                            trade["paper_update_last_error"] = None

                            closed_ids.append(trade_id)
                            
                            cursor.execute("""
                                UPDATE paper_trades SET 
                                    status = ?, exit_reason = ?, exit_price = ?, exit_ts = ?, result_r = ?,
                                    max_mfe_r = ?, max_mae_r = ?, highest_price_seen = ?, lowest_price_seen = ?,
                                    last_update_candle_ts = ?, last_update_source = ?,
                                    candles_processed_count = ?, last_processed_candle_ts = ?,
                                    paper_update_last_error = ?, stop_touched_flag = ?, tp_touched_flag = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (
                                trade["status"], trade["exit_reason"], trade["exit_price"], trade["exit_ts"], trade["result_r"],
                                trade["max_mfe_r"], trade["max_mae_r"], trade["highest_price_seen"], trade["lowest_price_seen"],
                                trade["last_update_candle_ts"], trade["last_update_source"],
                                trade["candles_processed_count"], trade["last_processed_candle_ts"],
                                trade["paper_update_last_error"], trade["stop_touched_flag"], trade["tp_touched_flag"],
                                trade_id
                            ))

                            event_type = f"{close_reason}_CLOSE"
                            self._log_event(cursor, trade_id, event_type, close_price, final_r, None)

                        else:
                            if is_new_candle:
                                trade["candles_processed_count"] = trade.get("candles_processed_count", 0) + 1
                            trade["last_processed_candle_ts"] = ts
                            trade["stop_touched_flag"] = trade.get("stop_touched_flag", 0) | (1 if is_stop else 0)
                            trade["tp_touched_flag"] = trade.get("tp_touched_flag", 0) | (1 if (is_tp1 or is_tp2 or is_tp3) else 0)
                            trade["paper_update_last_error"] = None
                            
                            # Update without closing
                            cursor.execute("""
                                UPDATE paper_trades SET 
                                    result_r = ?, max_mfe_r = ?, max_mae_r = ?, highest_price_seen = ?, lowest_price_seen = ?,
                                    last_update_candle_ts = ?, last_update_source = ?,
                                    candles_processed_count = ?, last_processed_candle_ts = ?,
                                    paper_update_last_error = ?, stop_touched_flag = ?, tp_touched_flag = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (
                                trade["result_r"], trade["max_mfe_r"], trade["max_mae_r"], trade["highest_price_seen"], trade["lowest_price_seen"],
                                trade["last_update_candle_ts"], trade["last_update_source"],
                                trade["candles_processed_count"], trade["last_processed_candle_ts"],
                                trade["paper_update_last_error"], trade["stop_touched_flag"], trade["tp_touched_flag"],
                                trade_id
                            ))
                    except Exception as e:
                        err_msg = str(e)
                        log.error(f"[PaperTradeEngine] Error processing candle {ts} for trade {trade_id}: {err_msg}")
                        trade["paper_update_last_error"] = err_msg
                        cursor.execute("""
                            UPDATE paper_trades SET paper_update_last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                        """, (err_msg, trade_id))

                for tid in closed_ids:
                    del self.open_trades[tid]

            conn.commit()

        except Exception as e:
            log.error(f"[PaperTradeEngine] Error updating trades: {e}")
        finally:
            conn.close()
