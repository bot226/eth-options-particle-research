import { fetchManualSetupWatchlist, postManualSnapshot, postManualEvent, postManualHealth, fetchManualLoggingStatus } from '../api/client.js';
import { ManualChart } from '../widgets/ManualChart.js';

const API_BASE = '';

// Frontend build version — identifies this JS bundle in manual_trading_snapshots
const FRONTEND_BUILD_VERSION = '2026-06-11-snapshot-dedup-v1';

async function fetchWithTimeout(url, timeoutMs = 8000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: controller.signal });
    clearTimeout(id);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

async function fetchManualCurrent() {
  return fetchWithTimeout(`${API_BASE}/api/manual-trading/current`);
}

async function fetchPaperSummary() {
  return fetchWithTimeout(`${API_BASE}/api/manual-trading/paper-summary`);
}

async function fetchKlines() {
  return fetchWithTimeout(`${API_BASE}/api/manual-trading/kline`);
}

// Compact labels for confirmation_needed in watchlist
const CONFIRMATION_SHORT_MAP = [
  [/чистого MOS-сетапа/i,           'ждать чистый сетап'],
  [/нового контекста расширения/i,  'ждать расширение MOS'],
  [/стабилизации структуры/i,       'ждать стаб. структуры'],
  [/взаимодействия с уровнем/i,     'ждать реакцию уровня'],
  [/неудачного нисходящего/i,       'ждать разворот flow → BULLISH'],
  [/неудачного восходящего/i,       'ждать разворот flow → BEARISH'],
  [/удержания ретеста/i,            'ждать ретест + продолж.'],
  [/higher low/i,                   'higher low / возврат'],
  [/lower high/i,                   'lower high / отказ'],
  [/края диапазона/i,               'пробой края диапазона'],
  [/принятия цены.*расширения/i,    'принятие в расш.'],
  [/более чистого/i,                'ждать чистый сетап'],
  [/независимого подтверждения/i,   'ждать подтверждение'],
];

function confirmationShort(text) {
  if (!text || text === '—') return '—';
  for (const [re, label] of CONFIRMATION_SHORT_MAP) {
    if (re.test(text)) return label;
  }
  return text.length > 28 ? text.slice(0, 26) + '…' : text;
}

export function safeNumber(value) {
  if (value === null) return null;
  if (value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  if (typeof value === "string" && ["null", "undefined", "nan", "-", "none"].includes(value.trim().toLowerCase())) return null;

  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n;
}

export class ManualTradingPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.currentData = null;
    this.watchlistRows = [];
    this.watchlistLoading = false;
    this.watchlistLastFetch = 0;
    this.currentLoading = false;
    this.currentLastFetch = 0;
    this.klinesLastFetch = 0;
    this.klinesLastSuccess = Date.now();
    this.currentLastSuccess = Date.now();
    this._lastCandleTs = null;    // unix seconds (number)
    this._lastCandleIso = null;   // ISO UTC string for logging
    this._lastCandleClose = null;
    this.filters = { statuses: new Set(['ENTRY_CANDIDATE', 'WATCH']), biases: new Set(), actionableOnly: false };
    this._chart = new ManualChart((msg, field) => {
        this._lastChartStatus = 'DEGRADED';
        this._lastDegradedSource = 'overlay';
        this._logHealth(msg, 'skipped', 'overlay', '/api/manual-trading/current', 200, 0, 0);
    });
    this._lastKlines = [];
    this._chartMounted = false;  // mount only when page is visible

    this.frontendSessionId = 'mt-' + Math.random().toString(36).slice(2, 11);
    this._lastSnapshotLogTs = 0;
    this._lastHealthLogTs = 0;
    this._prevManualState = {};
    this._tabVisible = document.visibilityState === 'visible' ? 1 : 0;
    this._lastChartStatus = 'LIVE';
    this._lastDegradedSource = null;
    this._watchdogInterval = null;

    // Version info — fetched from backend on mount
    this._codeVersion = 'unknown';
    this._enginePatchVersion = 'unknown';
    this._manualLoggerVersion = 'unknown';
    this._manualUiVersion = '1.0';
    
    document.addEventListener('visibilitychange', () => {
      this._tabVisible = document.visibilityState === 'visible' ? 1 : 0;
    });
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="mt-page">

        <!-- STATUS BAR -->
        <div class="mt-status-bar" id="mt-status-bar">
          <div class="mt-status-item mt-status-item--decision">
            <span class="mt-status-label">DECISION</span>
            <span class="mt-status-value mt-status-value--hero" id="mt-s-status">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">SETUP</span>
            <span class="mt-status-value mt-status-value--sm" id="mt-s-setup">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">BIAS</span>
            <span class="mt-status-value" id="mt-s-bias">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">QUALITY</span>
            <span class="mt-status-value" id="mt-s-quality">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">STATE</span>
            <span class="mt-status-value mt-status-value--sm" id="mt-s-state">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">EXECUTION</span>
            <span class="mt-status-value mt-status-value--sm" id="mt-s-exec">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item">
            <span class="mt-status-label">FLOW</span>
            <span class="mt-status-value" id="mt-s-flow">—</span>
          </div>
          <div class="mt-status-sep"></div>
          <div class="mt-status-item mt-status-item--time">
            <span class="mt-status-label">UPDATED</span>
            <span class="mt-status-value mt-status-value--sm" id="mt-s-time">—</span>
          </div>
        </div>

        <!-- MAIN CONTENT -->
        <div class="mt-main">

          <!-- LEFT -->
          <div class="mt-left">

            <!-- Decision Card -->
            <div class="mt-card mt-card--decision">
              <div class="mt-card-header"><span class="mt-card-icon">MT</span> CURRENT MANUAL DECISION</div>
              <div class="mt-card-body" id="mt-decision-card">
                <div class="mt-placeholder">ОЖИДАНИЕ ДАННЫХ...</div>
              </div>
            </div>

            <!-- Decision Ladder -->
            <div class="mt-card mt-card--ladder">
              <div class="mt-card-header"><span class="mt-card-icon">▶</span> DECISION LADDER</div>
              <div class="mt-card-body" id="mt-ladder"><div class="mt-placeholder">—</div></div>
            </div>

            <!-- Missing -->
            <div class="mt-card mt-card--missing" id="mt-missing-card" style="display:none">
              <div class="mt-card-header"><span class="mt-card-icon">!</span> MISSING CONDITIONS</div>
              <div class="mt-card-body" id="mt-missing-body"></div>
            </div>

          </div>

          <!-- RIGHT -->
          <div class="mt-right">

            <!-- Chart panel — LW Charts mounts here -->
            <div class="mt-chart-panel">
              <div class="mt-chart-header">
                <span>ETH 1M · PRICE + SETUP</span>
                <span id="mt-chart-stale-ind" style="margin-left:12px; font-size:9px; color:var(--text-dim);"></span>
                <button id="mt-chart-refresh" class="mt-btn-sm" style="margin-left:auto; font-size:9px; padding:2px 8px; cursor:pointer; background:transparent; border:1px solid #303040; color:#8890a8; border-radius:3px; text-transform:uppercase;">Refresh Chart</button>
                <button id="mt-chart-reset" class="mt-btn-sm" style="margin-left:8px; margin-right:12px; font-size:9px; padding:2px 8px; cursor:pointer; background:transparent; border:1px solid #303040; color:#8890a8; border-radius:3px; text-transform:uppercase;">Reset View</button>
                <span class="mt-chart-price" id="mt-chart-price">—</span>
              </div>
              <div class="mt-chart-body" id="mt-chart-area"></div>
            </div>

            <!-- Ribbons -->
            <div class="mt-ribbons">
              <div class="mt-ribbon" id="mt-ribbon-state">
                <div class="mt-ribbon-label">STATE</div>
                <div class="mt-ribbon-value" id="mt-rb-state">—</div>
                <div class="mt-ribbon-sub" id="mt-rb-candidate">—</div>
              </div>
              <div class="mt-ribbon" id="mt-ribbon-exec">
                <div class="mt-ribbon-label">EXECUTION</div>
                <div class="mt-ribbon-value" id="mt-rb-exec">—</div>
                <div class="mt-ribbon-sub" id="mt-rb-exec-sub">—</div>
              </div>
              <div class="mt-ribbon" id="mt-ribbon-flow">
                <div class="mt-ribbon-label">FLOW</div>
                <div class="mt-ribbon-value" id="mt-rb-flow">—</div>
                <div class="mt-ribbon-sub" id="mt-rb-flow-sub">—</div>
              </div>
              <div class="mt-ribbon" id="mt-ribbon-level">
                <div class="mt-ribbon-label">NEAREST LEVEL</div>
                <div class="mt-ribbon-value" id="mt-rb-level">—</div>
                <div class="mt-ribbon-sub" id="mt-rb-level-sub">—</div>
              </div>
            </div>

          </div>
        </div>

        <!-- WATCHLIST -->
        <div class="mt-watchlist-section">
          <div class="mt-section-header">
            <span class="mt-card-icon">WL</span> MANUAL SETUP WATCHLIST
            <div class="mt-wl-filters" id="mt-wl-filters">
              <button class="mt-filter-btn is-active" data-mt-filter="status" data-mt-value="ENTRY_CANDIDATE">ENTRY_CANDIDATE</button>
              <button class="mt-filter-btn is-active" data-mt-filter="status" data-mt-value="WATCH">WATCH</button>
              <button class="mt-filter-btn" data-mt-filter="status" data-mt-value="AVOID">AVOID</button>
              <button class="mt-filter-btn" data-mt-filter="bias" data-mt-value="LONG">LONG</button>
              <button class="mt-filter-btn" data-mt-filter="bias" data-mt-value="SHORT">SHORT</button>
              <button class="mt-filter-btn" data-mt-filter="actionable" data-mt-value="ONLY" style="color:var(--green); border-color:rgba(0,230,118,0.45); margin-left: 8px;">ACTIONABLE ONLY</button>
            </div>
          </div>
          <div class="mt-wl-table-wrap">
            <table class="mt-wl-table">
              <thead><tr>
                <th>time</th><th>setup_type</th><th>actionability</th><th>status</th><th>bias</th><th>quality</th>
                <th>missing</th><th>price</th><th>level</th><th>state</th><th>execution</th>
                <th>event</th><th>level_result</th><th>flow</th><th>confirmation</th><th>inval</th>
              </tr></thead>
              <tbody id="mt-wl-body">
                <tr><td colspan="15" class="mt-wl-empty">ЗАГРУЗКА...</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- PAPER TRADES -->
        <div class="mt-watchlist-section" style="margin-top: 16px;">
          <div class="mt-section-header">
            <span class="mt-card-icon">PT</span> PAPER TRADES V1
            <div class="mt-wl-filters" id="mt-pt-stats" style="margin-left:auto; display:flex; gap:12px; font-size:12px; font-weight:bold; align-items:center;">
               <span>TOTAL: <span id="pt-total">0</span></span>
               <span>OPEN: <span id="pt-open">0</span></span>
               <span style="color:var(--green)">WIN RATE: <span id="pt-win">0%</span></span>
               <span>TOTAL R: <span id="pt-r">0</span></span>
            </div>
          </div>
          <div class="mt-wl-table-wrap">
            <table class="mt-wl-table">
              <thead><tr>
                <th>time</th><th>setup</th><th>side</th><th>entry</th><th>stop</th><th>tp3</th><th>status</th><th>exit_reason</th><th>exit_price</th><th>result_r</th><th>max_mfe</th><th>max_mae</th>
              </tr></thead>
              <tbody id="mt-pt-body">
                <tr><td colspan="12" class="mt-wl-empty">ЗАГРУЗКА...</td></tr>
              </tbody>
            </table>
          </div>
        </div>

      </div>
    `;

    this.container.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-mt-filter]');
      if (btn && this.container.contains(btn)) {
        this._toggleFilter(btn.dataset.mtFilter, btn.dataset.mtValue);
      }
      if (e.target.id === 'mt-chart-reset') {
        this._chart.resetView();
      }
      if (e.target.id === 'mt-chart-refresh') {
        this._updateStaleInd('REFRESHING...', Date.now());
        this._pollKlines(true);
        this._pollCurrent(true);
        this._refreshWatchlist(true);
      }
    });

    this.isInitialized = true;
    // NOTE: chart is NOT mounted here because container is hidden at init time.
    // Mount happens in onActivate() when the page becomes visible.
    this._pollCurrent();
    this._refreshWatchlist(true);
    
    // ── Singleton watchdog: clear any previous instance (e.g. from old page) ──
    if (this._watchdogInterval) clearInterval(this._watchdogInterval);
    if (window._manualTradingWatchdogId) clearInterval(window._manualTradingWatchdogId);
    const wdId = setInterval(() => this._watchdogTick(), 5000);
    this._watchdogInterval = wdId;
    window._manualTradingWatchdogId = wdId;

    this._pollPaperTrades();
    this._ptInterval = setInterval(() => this._pollPaperTrades(), 10000);
  }
  
  _watchdogTick() {
    if (!this.isInitialized) return;

    // Do NOT log snapshots until backend version is confirmed.
    // This prevents 'unknown' code_version rows in manual_trading_snapshots.
    if (this._codeVersion === 'unknown') {
      // Still fetch version if we haven't yet
      if (!this._versionFetchPending) {
        this._versionFetchPending = true;
        this._fetchVersionInfo().finally(() => { this._versionFetchPending = false; });
      }
      return;
    }

    const now = Date.now();
    
    // 1. Polling loops (every 5s)
    if (now - (this.currentLastSuccess || 0) > 5000) {
      this._pollCurrent();
    }
    if (now - (this.klinesLastSuccess || 0) > 5000) {
      this._pollKlines();
    }
    if (now - (this.watchlistLastSuccess || 0) > 6000) {
      this._refreshWatchlist();
    }

    // 2. Snapshot logging continuity
    const lastWrite = window._lastManualSnapshotWriteTs || 0;
    
    // Check if we need to write a heartbeat (15s interval, or force if >30s gap)
    if (now - this._lastSnapshotLogTs >= 15000 || now - lastWrite >= 30000) {
      if (!this.currentData || this._lastChartStatus === 'FETCH_ERROR' || this._lastChartStatus === 'DEGRADED') {
        this._logSnapshot({
            manual_status: 'DEGRADED',
            setup_quality: 'INCOMPLETE',
            actionability: 'NOT_ACTIONABLE',
            decision_blocker: 'SNAPSHOT_DEGRADED_PAYLOAD'
        }, {
            price: this._lastCandleClose,
            live_source_snapshot_id: this._lastDegradedSource || 'snapshot_logging'
        }, new Date().toISOString(), {}, {});
      } else {
         this._logSnapshot(
            this.currentData.manual_setup || {},
            this.currentData.source_fields || {},
            new Date().toISOString(), // monotonic ts
            this.currentData.level_context_meta || {},
            this.currentData.live_level_context || {}
         );
      }
      this._lastSnapshotLogTs = now;
    }

    // 2. Error recovery Watchdog
    const diffSecKlines = Math.floor((now - this.klinesLastSuccess) / 1000);
    const diffSecCurrent = Math.floor((now - this.currentLastSuccess) / 1000);
    const maxDiff = Math.max(diffSecKlines, diffSecCurrent);

    if ((this._lastChartStatus === 'FETCH_ERROR' || this._lastChartStatus === 'DEGRADED' || this._lastChartStatus === 'STALE') && maxDiff > 60) {
        if (maxDiff > 120 && this._chartMounted) {
            // Force data refresh visually without resetting zoom
            this._chart.update(this._lastKlines, this.currentData, this.watchlistRows);
        }
        // Force retry polling independently
        this._pollCurrent(true);
        this._pollKlines(true);
        this._refreshWatchlist(true);
    }
  }

  async _pollPaperTrades() {
    try {
      const data = await fetchPaperSummary();
      if (data && data.status === 'ok') {
        const total = document.getElementById('pt-total');
        const open = document.getElementById('pt-open');
        const win = document.getElementById('pt-win');
        const rr = document.getElementById('pt-r');
        
        let allModelsR = 0;
        let allModelsTotal = 0;
        let allModelsWins = 0;
        for(let m in data.models) {
            allModelsR += data.models[m].r;
            allModelsTotal += data.models[m].total;
            allModelsWins += data.models[m].wins;
        }
        
        if (total) total.textContent = data.total_trades;
        if (open) open.textContent = data.open_count;
        if (win) win.textContent = allModelsTotal > 0 ? (allModelsWins/allModelsTotal*100).toFixed(1) + '%' : '0%';
        if (rr) rr.textContent = allModelsR.toFixed(2);
        
        const tbody = document.getElementById('mt-pt-body');
        if (tbody) {
          const trades = [...(data.active_trades || []), ...(data.recent_closed || [])];
          if (trades.length === 0) {
              tbody.innerHTML = '<tr><td colspan="12" class="mt-wl-empty">НЕТ ДАННЫХ</td></tr>';
          } else {
              tbody.innerHTML = trades.map(t => {
                  return `<tr>
                    <td>${new Date(t.entry_ts).toLocaleTimeString('ru-RU')}</td>
                    <td>${t.setup_type || '-'}</td>
                    <td class="mt-bc-${t.side === 'LONG' ? 'long' : 'short'}">${t.side}</td>
                    <td>${t.entry_price ? t.entry_price.toFixed(1) : '-'}</td>
                    <td>${t.protective_stop_execution_price ? t.protective_stop_execution_price.toFixed(1) : '-'}</td>
                    <td>${t.tp3_execution_price ? t.tp3_execution_price.toFixed(1) : '-'}</td>
                    <td style="color:${t.status === 'OPEN' ? 'var(--blue)' : 'var(--text-dim)'}">${t.status}</td>
                    <td>${t.exit_reason || '-'}</td>
                    <td>${t.exit_price ? t.exit_price.toFixed(1) : '-'}</td>
                    <td style="color:${t.result_r > 0 ? 'var(--green)' : (t.result_r < 0 ? 'var(--red)' : '')}">${t.result_r ? t.result_r.toFixed(2) : '-'}</td>
                    <td>${t.max_mfe_r ? t.max_mfe_r.toFixed(2) : '-'}</td>
                    <td>${t.max_mae_r ? t.max_mae_r.toFixed(2) : '-'}</td>
                  </tr>`;
              }).join('');
          }
        }
      }
    } catch(e) {
      console.warn("Paper trades poll failed:", e);
    }
  }

  // Called from store on every WS update
  update(state) {
    if (!this.isInitialized) return;
    const ms = state.market_state;

    // Execution price for chart header (use execution_price from price_source_info, fallback to spot)
    const psi = state.market_state?.price_source_info || this.currentData?.price_source_info || null;
    const execPrice = psi?.execution_price
      || state.market_state?.manual_setup_source_fields?.price
      || state.data?.spot
      || null;
    const el = document.getElementById('mt-chart-price');
    if (el) el.textContent = execPrice ? this._fmtETH(execPrice) : '—';

    // Keep latest klines; push to chart when mounted (include watchlist markers)
    if (Array.isArray(state.klines) && state.klines.length) {
      this._lastKlines = state.klines;
      if (this._chartMounted) {
        this._chart.update(this._lastKlines, this.currentData, this.watchlistRows);
      }
    }

    // Fallback: render from WS data if API hasn't loaded yet
    if (ms && !this.currentData) {
      const manual = ms.manual_setup || {};
      const sf     = ms.manual_setup_source_fields || {};
      const ts     = ms.timestamp || ms.ts || null;
      this._updateStatusBar(manual, sf, ts);
      this._updateDecisionCard(manual, sf);
      this._updateLadder(manual);
      this._updateMissingBlock(manual);
      this._updateRibbons(manual, sf);
    }

    this._updateStaleInd();
    this._pollKlines();
    this._pollCurrent();
    this._refreshWatchlist();
  }

  onActivate() {
    // Use rAF to ensure the container is visible and has real dimensions
    requestAnimationFrame(() => {
      if (!this._chartMounted) {
        const chartArea = document.getElementById('mt-chart-area');
        if (chartArea && chartArea.clientWidth > 0) {
          this._chart.mount(chartArea);
          this._chartMounted = true;
          this._chart.update(this._lastKlines, this.currentData, this.watchlistRows);
        }
      } else {
        this._chart._onResize();
      }
    });
    this._pollCurrent(true);
    this._pollKlines();        // always fetch fresh candles on page open
    this._refreshWatchlist(true);
    this._fetchVersionInfo(); // fetch backend version for logging
  }

  /** Fetch backend code_version and logger version once on mount */
  async _fetchVersionInfo() {
    try {
      const status = await fetchManualLoggingStatus();
      this._codeVersion = status.code_version || 'unknown';
      this._enginePatchVersion = status.engine_patch_version || 'unknown';
      this._manualLoggerVersion = status.manual_logger_version || 'unknown';
      console.log(`[ManualTrading] versions loaded: code=${this._codeVersion}, engine=${this._enginePatchVersion}, logger=${this._manualLoggerVersion}`);
    } catch (e) {
      console.warn('[ManualTrading] could not fetch version info:', e.message);
    }
  }

  /** Direct kline fetch — bypasses store so chart works even if store klines were empty */
  async _pollKlines(force = false) {
    const DEBUG_CHART = true;
    const now = Date.now();
    
    if (this.klinesLoading) {
      if (DEBUG_CHART) console.log('[ManualTrading] fetch skipped because in-flight');
      return;
    }
    if (!force && now - this.klinesLastFetch < 5000) return;
    this.klinesLoading = true;
    this.klinesLastFetch = now;
    
    if (DEBUG_CHART) console.log('[ManualTrading] fetch started');
    const startTs = Date.now();
    try {
      const data = await fetchKlines();
      
      if (data?.status === 'DEGRADED') {
          if (DEBUG_CHART) console.log('[ManualTrading] klines fetch DEGRADED:', data.error_reason);
          this._lastChartStatus = 'DEGRADED';
          this._lastDegradedSource = 'candles';
          this._updateStaleInd('DEGRADED', this.klinesLastSuccess, this._lastKlines);
          this._logHealth(data.error_reason || 'kline degraded', 'skipped', 'candles', '/api/manual-trading/kline', 200, Date.now() - startTs, 0);
          return;
      }
      
      let klines = data?.klines;
      if (Array.isArray(klines)) {
         klines = klines.filter(c => {
             if (c == null) return false;
             const ts = safeNumber(c.ts ?? c.time ?? c.timestamp);
             const o = safeNumber(c.o ?? c.open);
             const h = safeNumber(c.h ?? c.high);
             const l = safeNumber(c.l ?? c.low);
             const c_val = safeNumber(c.c ?? c.close);
             if (ts === null || o === null || h === null || l === null || c_val === null) return false;
             if (h < l) return false;
             return true;
         });
      }
      
      if (!klines || klines.length === 0) {
         if (DEBUG_CHART) console.log('[ManualTrading] NO_VALID_CANDLES_AFTER_FILTER');
         this._lastChartStatus = 'DEGRADED';
         this._lastDegradedSource = 'candles';
         this._updateStaleInd('DEGRADED', this.klinesLastSuccess, this._lastKlines);
         this._logHealth('NO_VALID_CANDLES_AFTER_FILTER', 'skipped', 'candles', '/api/manual-trading/kline', 200, Date.now() - startTs, 0);
         return;
      }
      
      if (DEBUG_CHART) console.log('[ManualTrading] fetch success');
      
      this.klinesLastSuccess = Date.now();
      let mode = 'skipped';
      
      if (Array.isArray(klines) && klines.length) {
        this._lastKlines = klines;
        
        // Normalize latest candle timestamp right here — single source of truth
        const latest = klines[klines.length - 1];
        const rawTime = latest.ts ?? latest.time ?? latest.timestamp ?? null;
        if (rawTime != null) {
          const ms = Number(rawTime);
          const sec = ms > 1e12 ? Math.floor(ms / 1000) : ms;
          this._lastCandleTs = sec;
          this._lastCandleIso = sec > 0 ? new Date(sec * 1000).toISOString() : null;
        }
        this._lastCandleClose = latest.c ?? latest.close ?? null;
        
        if (this._chartMounted) {
          const res = this._chart.update(this._lastKlines, this.currentData, this.watchlistRows);
          if (res) mode = res.mode;
        }
        if (DEBUG_CHART) {
          console.log(`[ManualTrading] candles count: ${klines.length}, latest ts raw: ${rawTime}, normalized: ${this._lastCandleTs}, iso: ${this._lastCandleIso}, update mode: ${mode}`);
        }
      }
      
      if (this._lastChartStatus === 'FETCH_ERROR') {
          this._lastChartStatus = 'LIVE';
          this._updateStaleInd('LIVE', this.klinesLastSuccess, this._lastKlines);
          this._logHealth(null, 'recovered', 'candles', '/api/manual-trading/kline', 200, Date.now() - startTs, 1);
      } else if (this._lastChartStatus === 'DEGRADED' && this._lastDegradedSource === 'candles') {
          this._lastChartStatus = 'LIVE';
          this._lastDegradedSource = null;
          this._updateStaleInd('LIVE', this.klinesLastSuccess, this._lastKlines);
          this._logHealth(null, 'recovered', 'candles', '/api/manual-trading/kline', 200, Date.now() - startTs, 1);
      } else {
          this._updateStaleInd(this._lastChartStatus === 'DEGRADED' ? 'DEGRADED' : 'LIVE', this.klinesLastSuccess, this._lastKlines);
      }
      
      const timeNow = Date.now();
      if (timeNow - this._lastHealthLogTs >= 15000) {
        this._logHealth(null, mode, null, '/api/manual-trading/kline', 200, timeNow - startTs, 0);
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        if (DEBUG_CHART) console.log('[ManualTrading] fetch aborted');
      } else {
        if (DEBUG_CHART) console.log('[ManualTrading] fetch failed:', e.message);
      }
      this._lastChartStatus = 'FETCH_ERROR';
      this._lastDegradedSource = 'candles';
      this._updateStaleInd('FETCH_ERROR', this.klinesLastSuccess, this._lastKlines);
      let status = null;
      if (e.message && e.message.includes('HTTP')) {
          const match = e.message.match(/HTTP\s+(\d+)/);
          if (match) status = parseInt(match[1]);
      }
      this._logHealth(e.message || 'fetch error', 'skipped', 'candles', '/api/manual-trading/kline', status, Date.now() - startTs, 0);
    } finally {
      this.klinesLoading = false;
      this.klinesLastFetch = Date.now();
    }
  }

  _updateStaleInd(statusOverride, lastSuccessMs, klines) {
    const DEBUG_CHART = true;
    if (statusOverride) this._lastStatus = statusOverride;
    let status = this._lastStatus || 'LIVE';
    lastSuccessMs = lastSuccessMs || this.klinesLastSuccess || Date.now();
    klines = klines || this._lastKlines;
    
    const now = Date.now();
    const diffSec = Math.floor((now - lastSuccessMs) / 1000);
    
    if (status === 'DEGRADED' || status === 'FETCH_ERROR') {
      if (this._lastDegradedSource === 'current') {
        status = 'DEGRADED: current error';
      } else if (this._lastDegradedSource === 'candles' || this._lastDegradedSource === 'kline') {
        status = 'DEGRADED: kline error';
      } else if (this._lastDegradedSource === 'snapshot_logging') {
        status = 'SNAPSHOT LOG ERROR';
      } else if (this._lastDegradedSource === 'overlay') {
        status = 'DEGRADED: overlay error';
      }
    } else if (diffSec > 60 && status === 'LIVE') {
      status = 'STALE: no fresh candles';
    }
    
    const el = document.getElementById('mt-chart-stale-ind');
    
    let latestText = '';
    if (Array.isArray(klines) && klines.length > 0) {
      const latest = klines[klines.length - 1];
      
      const rawTime = this._lastCandleTs; // already unix seconds from _pollKlines
      let candleTimeSec = rawTime || 0;
      const closePrice = this._lastCandleClose ?? 0;
      
      let tsText = '-';
      if (candleTimeSec > 0 && !isNaN(candleTimeSec)) {
        tsText = new Date(candleTimeSec * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      }
      
      if (DEBUG_CHART) {
        console.log(`[ManualTrading] time debug - raw: ${rawTime}, normalized: ${candleTimeSec}, formatted: ${tsText}`);
      }
      
      latestText = ` | count: ${klines.length} | latest: ${tsText} (c: ${closePrice})`;
      
      if (status === 'LIVE' && candleTimeSec > 0) {
         const isCandleOld = (now / 1000) - candleTimeSec > 65;
         if (isCandleOld) {
            status = 'waiting for new candle';
         }
      }
    }
    
    const dTime = new Date(lastSuccessMs).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    this._lastChartStatus = status;
    if (!el) return;
    
    el.textContent = `${status} | last fetch: ${dTime} (${diffSec}s ago)${latestText}`;
    
    if (status === 'LIVE' || status === 'waiting for new candle') el.style.color = 'var(--green)';
    else if (status.includes('ERROR') || status.includes('STALE')) el.style.color = 'var(--red)';
    else el.style.color = 'var(--orange)';
  }

  // ── Primary data source: /api/manual-trading/current ─────────────
  async _pollCurrent(force = false) {
    const now = Date.now();
    if (this.currentLoading) return;
    if (!force && now - this.currentLastFetch < 5000) return;
    this.currentLoading = true;
    this.currentLastFetch = now;
    const startTs = Date.now();
    try {
      const payload = await fetchManualCurrent();
      this.currentData = payload;
      this.currentLastSuccess = Date.now();
      this._renderFromCurrent(payload);
      if (this._lastChartStatus === 'DEGRADED' && this._lastDegradedSource === 'current') {
         this._lastChartStatus = 'LIVE';
         this._updateStaleInd();
         this._logHealth(null, 'recovered', 'current', '/api/manual-trading/current', 200, Date.now() - startTs, 1);
         this._lastDegradedSource = null;
      }
    } catch (e) {
      if (this._lastChartStatus === 'LIVE') {
          this._lastChartStatus = 'DEGRADED';
          this._updateStaleInd();
      }
      this._lastDegradedSource = 'current';
      let status = null;
      if (e.message && e.message.includes('HTTP')) {
          const match = e.message.match(/HTTP\s+(\d+)/);
          if (match) status = parseInt(match[1]);
      }
      this._logHealth(e.message || 'fetch error', 'skipped', 'current', '/api/manual-trading/current', status, Date.now() - startTs, 0);
    } finally {
      this.currentLoading = false;
    }
  }

  _renderFromCurrent(payload) {
    if (!payload) return;
    if (payload.status === 'loading') return;
    const manual = payload.manual_setup || {};
    const sf = payload.source_fields || {};
    const timestamp = payload.timestamp;
    const levelMeta = payload.level_context_meta || {};
    const liveCtx   = payload.live_level_context  || {};

    this._updateStatusBar(manual, sf, timestamp);
    this._updateDecisionCard(manual, sf, levelMeta, liveCtx, payload.price_source_info || null);
    this._updateLadder(manual);
    this._updateMissingBlock(manual);
    this._updateRibbons(manual, sf);

    // Update chart overlay (only if mounted)
    if (this._chartMounted) {
      if (this._lastDegradedSource === 'overlay') {
          this._lastChartStatus = 'LIVE';
          this._lastDegradedSource = null;
      }
      this._chart.update(this._lastKlines, payload, this.watchlistRows);
      this._updateStaleInd();
    }

    // Logging checks - only log on event change here, periodic is handled by watchdog
    this._checkAndLogEvent(manual, sf, timestamp);
  }

  // ── Status Bar ───────────────────────────────────────────────────
  _updateStatusBar(manual, sf, timestamp) {
    const status  = manual.manual_status     || '—';
    const setup   = manual.manual_setup_type || '—';
    const bias    = manual.manual_bias       || '—';
    const quality = manual.setup_quality     || '—';
    const state   = sf.current_state         || '—';
    const exec    = sf.execution_timing_state|| '—';
    const flow    = sf.short_term_flow_direction || '—';

    this._setText('mt-s-status',  status);
    this._setText('mt-s-setup',   setup);
    this._setText('mt-s-bias',    bias);
    this._setText('mt-s-quality', quality);
    this._setText('mt-s-state',   state);
    this._setText('mt-s-exec',    exec);
    this._setText('mt-s-flow',    flow);
    this._setText('mt-s-time',    timestamp ? this._fmtTime(timestamp) : '—');

    const statusEl = document.getElementById('mt-s-status');
    if (statusEl) statusEl.className = `mt-status-value mt-status-value--hero mt-sc-${this._statusCls(status)}`;
    const biasEl = document.getElementById('mt-s-bias');
    if (biasEl) biasEl.className = `mt-status-value mt-bc-${this._biasCls(bias)}`;
    const flowEl = document.getElementById('mt-s-flow');
    if (flowEl) flowEl.className = `mt-status-value mt-fc-${this._flowCls(flow)}`;
    const qualityEl = document.getElementById('mt-s-quality');
    if (qualityEl) qualityEl.className = `mt-status-value mt-qc-${this._qualityCls(quality)}`;
  }

  // ── Decision Card ────────────────────────────────────────────────
  _updateDecisionCard(manual, sf, levelMeta = {}, liveCtx = {}, psi = null) {
    const el = document.getElementById('mt-decision-card');
    if (!el) return;
    if (!manual?.manual_status) {
      el.innerHTML = '<div class="mt-placeholder">ОЖИДАНИЕ ДАННЫХ...</div>';
      return;
    }

    const statusCls = this._statusCls(manual.manual_status);

    // ── Live level context badge ──────────────────────────────────────
    const liveLevelResult = sf.live_level_result || liveCtx.live_level_result;
    const liveNearest     = sf.live_nearest_level ?? liveCtx.live_nearest_level;
    const liveLevelSide   = sf.live_level_side   || liveCtx.live_level_side;
    const liveContextUsed = sf.live_context_used ?? liveCtx.live_context_used ?? false;
    const liveDistPct     = sf.live_distance_pct ?? liveCtx.live_distance_pct;
    const liveSource      = sf.live_context_source || liveCtx.live_context_source || 'live';
    const liveLevelType   = sf.live_level_type   || liveCtx.live_level_type || '';

    let ctxBadge = '';
    if (liveLevelResult) {
      const distLabel = liveDistPct != null ? ` Δ${liveDistPct.toFixed(2)}%` : '';
      ctxBadge = `<span class="mt-ctx-badge mt-ctx-fresh" title="source: ${this._esc(liveSource)} | type: ${this._esc(liveLevelType)}">● LIVE${distLabel}</span>`;
    } else {
      ctxBadge = `<span class="mt-ctx-badge mt-ctx-stale">⚠ NO LIVE CTX</span>`;
    }

    // ── Replay reference (raw, dimmed, debug only) ───────────────────────
    let rawLevelRow = '';
    const rawResult = levelMeta.raw_level_result || sf.raw_level_result;
    const rawLevel  = levelMeta.raw_nearest_level || sf.raw_nearest_level;
    const rawAge    = levelMeta.age_sec ?? sf.level_context_age_sec;
    if (rawResult) {
      const ageLabel = rawAge != null ? `${Math.round(rawAge)}s ago` : 'age?';
      rawLevelRow = `
        <div class="mt-dm-row mt-dm-stale-row" title="replay/outcome table only, not used for live decisions">
          <span class="mt-dm-label">raw_replay</span>
          <span class="mt-dm-val">${this._esc(rawResult)}${rawLevel ? ' @ ' + this._fmtETH(rawLevel) : ''} <span style="color:var(--text-dim);font-size:7px">(${ageLabel})</span></span>
        </div>`;
    }

    let displayStatus = this._esc(manual.manual_status);
    if (manual.manual_status === 'ENTRY_CANDIDATE' && manual.candidate_is_new === 0) {
      displayStatus = 'ENTRY CANDIDATE <span style="font-size:0.5em; opacity:0.8; vertical-align: middle;">· already active</span>';
    } else if (manual.manual_status === 'WATCH' && manual.actionability === 'FORMING' && manual.forming_is_new === 0) {
      displayStatus = 'ACTIVE FORMING <span style="font-size:0.5em; opacity:0.8; vertical-align: middle;">· already active</span>';
    }
    
    let blockerBadge = '';
    if (manual.decision_blocker && manual.decision_blocker !== 'NONE') {
      blockerBadge = `<div style="font-size: 11px; font-weight: 700; color: #ff4444; margin-top: 8px; padding: 4px 8px; border: 1px solid #ff4444; border-radius: 4px; display: inline-block; letter-spacing: 0.5px;">BLOCKED: ${this._esc(manual.decision_blocker.replace(/_/g, ' '))}</div>`;
    }

    el.innerHTML = `
      <div class="mt-decision-hero mt-sc-${statusCls}">
        ${displayStatus}
        ${blockerBadge ? '<br>' + blockerBadge : ''}
      </div>
      <div class="mt-decision-meta">
        <div class="mt-dm-row">
          <span class="mt-dm-label">setup_type</span>
          <span class="mt-dm-val mt-setup-${this._setupTypeCls(manual.manual_setup_type)}">${this._esc(manual.manual_setup_type || 'NONE')}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">manual_bias</span>
          <span class="mt-dm-val mt-bc-${this._biasCls(manual.manual_bias)}">${this._esc(manual.manual_bias || '—')}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">setup_quality</span>
          <span class="mt-dm-val mt-qc-${this._qualityCls(manual.setup_quality)}">${this._esc(manual.setup_quality || '—')}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">synthetic_flow</span>
          <span class="mt-dm-val">${safeNumber(sf.raw_synthetic_flow_pressure) !== null ? safeNumber(sf.raw_synthetic_flow_pressure).toFixed(1) + ' <span style="color:var(--text-dim);font-size:8px">(' + this._esc(sf.flow_direction_source || '') + ')</span>' : '—'}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">support</span>
          <span class="mt-dm-val">${safeNumber(liveCtx.live_support_level) !== null ? this._fmtETH(liveCtx.live_support_level) + ' Δ' + (safeNumber(liveCtx.live_support_distance_pct) || 0).toFixed(2) + '%' : '—'} <span style="color:var(--text-dim);font-size:8px">(${this._esc(liveCtx.live_support_source || '')})</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">resistance</span>
          <span class="mt-dm-val">${safeNumber(liveCtx.live_resistance_level) !== null ? this._fmtETH(liveCtx.live_resistance_level) + ' Δ' + (safeNumber(liveCtx.live_resistance_distance_pct) || 0).toFixed(2) + '%' : '—'} <span style="color:var(--text-dim);font-size:8px">(${this._esc(liveCtx.live_resistance_source || '')})</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">primary_live</span>
          <span class="mt-dm-val">${safeNumber(liveCtx.primary_live_level) !== null ? this._esc(liveCtx.primary_live_side) + ' ' + this._fmtETH(liveCtx.primary_live_level) + ' Δ' + (safeNumber(liveCtx.primary_live_distance_pct) || 0).toFixed(2) + '%' : '—'}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">selected_for_setup</span>
          <span class="mt-dm-val">${safeNumber(manual.selected_setup_level) !== null ? this._esc(manual.selected_setup_side) + ' ' + this._fmtETH(manual.selected_setup_level) : '—'}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">invalidation_level</span>
          <span class="mt-dm-val">${this._fmtETH(manual.invalidation_level)}</span>
        </div>
        ${rawLevelRow}
      </div>

      <!-- Price Source Info block (v34) -->
      <div class="mt-price-source-block">
        <div class="mt-psb-title">PRICE SOURCE</div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">Ref price</span>
          <span class="mt-dm-val" style="color:var(--text-dim)">${psi?.reference_price != null ? this._fmtETH(psi.reference_price) : (manual.reference_price != null ? this._fmtETH(manual.reference_price) : '—')}
            <span style="font-size:8px;color:var(--text-dim)"> (index)</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">Exec price</span>
          <span class="mt-dm-val" style="color:var(--yellow,#f0b429);font-weight:600">${psi?.execution_price != null ? this._fmtETH(psi.execution_price) : (manual.execution_price != null ? this._fmtETH(manual.execution_price) : '—')}
            <span style="font-size:8px;color:var(--text-dim)"> (${this._esc((psi?.execution_symbol || manual.execution_symbol) ?? 'perp')})</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">Basis</span>
          <span class="mt-dm-val" style="color:${((psi?.basis || manual.basis || 0) > 0 ? 'var(--green)' : (psi?.basis || manual.basis || 0) < 0 ? 'var(--red)' : 'var(--text-dim)')}">${(psi?.basis || manual.basis) != null ? (psi?.basis ?? manual.basis).toFixed(1) : '—'} / ${(psi?.basis_pct || manual.basis_pct) != null ? (psi?.basis_pct ?? manual.basis_pct).toFixed(3) + '%' : '—'}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">Entry source</span>
          <span class="mt-dm-val" style="color:var(--text-dim);font-size:9px">${this._esc((psi?.price_source_for_entry || manual.price_source_for_entry) ?? 'execution_price')}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">Levels src</span>
          <span class="mt-dm-val" style="color:var(--text-dim);font-size:9px">${this._esc((psi?.price_source_for_levels || manual.price_source_for_levels) ?? 'execution_ohlcv+reference_walls')}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">OHLCV src</span>
          <span class="mt-dm-val" style="color:var(--text-dim);font-size:9px">${this._esc((psi?.ohlcv_source || manual.ohlcv_source) ?? 'binance_spot_ethusdt')}</span>
        </div>
      </div>

      <div class="mt-copy-block">
        <div class="mt-copy-label">ПРИЧИНА (WHY)</div>
        <div class="mt-copy-text">${this._esc(manual.manual_reason || '—')}</div>
      </div>
      <div class="mt-copy-block">
        <div class="mt-copy-label">ПОДТВЕРЖДЕНИЕ</div>
        <div class="mt-copy-text">${this._esc(manual.confirmation_needed || '—')}</div>
      </div>
      <div class="mt-copy-block">
        <div class="mt-copy-label">ОТМЕНА СЕТАПА</div>
        <div class="mt-copy-text">${this._esc(manual.invalidation_condition || '—')}</div>
      </div>
    `;
  }

  // ── Decision Ladder ──────────────────────────────────────────────
  _updateLadder(manual) {
    const el = document.getElementById('mt-ladder');
    if (!el) return;
    const ld = manual?.decision_ladder;
    if (!ld || typeof ld !== 'object') {
      el.innerHTML = '<div class="mt-placeholder">—</div>';
      return;
    }

    const ok   = '<span class="mt-ldi mt-ldi--ok">✅</span>';
    const warn = '<span class="mt-ldi mt-ldi--warn">⚠️</span>';
    const fail = '<span class="mt-ldi mt-ldi--fail">❌</span>';

    const env        = ld.environment || {};
    const envState   = env.state   || '—';
    const envQuality = env.quality || envState;
    const execState  = ld.execution || '—';
    const event      = ld.event;
    const levelReact = ld.level_reaction;
    const nearLevel  = ld.nearest_level;
    const flow       = ld.flow || '—';
    const priceConf  = ld.price_confirmation;
    const finalDec   = ld.final_decision || '—';

    const execOk    = ['EXECUTION_WINDOW_OPEN','EXECUTION_WINDOW','EXPANSION_CONFIRMING'].includes(execState);
    const flowOk    = !['NEUTRAL','','—'].includes(flow);
    const finalCls  = this._statusCls(finalDec);

    const envActive = ['ACTIVE','EXPANSION','BREAKOUT','HEDGE_CHASE','EXHAUSTION']
      .some(q => (envQuality || '').includes(q));

    const rows = [
      {
        label: 'Environment',
        icon: envActive ? ok : warn,
        val: `<b>${this._esc(envState)}</b> <span class="mt-ld-sub">${this._esc(envQuality)}</span>`,
      },
      {
        label: 'Execution',
        icon: execOk ? ok : warn,
        val: this._esc(execState),
      },
      {
        label: 'Event',
        icon: event ? ok : fail,
        val: event ? this._esc(event) : '<span class="mt-ld-missing">missing</span>',
      },
      {
        label: 'Level Reaction',
        icon: levelReact ? ok : fail,
        val: levelReact ? this._esc(levelReact) : '<span class="mt-ld-missing">missing</span>',
      },
      {
        label: 'Nearest Level',
        icon: nearLevel ? ok : fail,
        val: nearLevel ? this._fmtETH(nearLevel) : '<span class="mt-ld-missing">missing</span>',
      },
      {
        label: 'Flow',
        icon: (() => {
          // Flow is PASS only when setup is actionable and bias matches flow direction
          const setupType  = manual.manual_setup_type || '';
          const bias       = manual.manual_bias || 'NEUTRAL';
          const isNoSetup  = ['NO_ACTIONABLE_SETUP','NO_TRADE_CHOP','UNSTABLE_STRUCTURE_AVOID','NONE'].includes(setupType);
          const flowBullish = ['BULLISH','LONG','UP','BUYING'].includes(flow);
          const flowBearish = ['BEARISH','SHORT','DOWN','SELLING'].includes(flow);
          const flowAligned = (bias === 'LONG' && flowBullish) || (bias === 'SHORT' && flowBearish);
          if (!flowOk) return warn;           // neutral/missing flow
          if (isNoSetup || bias === 'NEUTRAL') return warn; // flow exists but no setup
          if (flowAligned) return ok;          // flow matches bias = true PASS
          return warn;                         // flow exists but not aligned
        })(),
        val: `<span class="mt-fc-${this._flowCls(flow)}">${this._esc(flow)}</span>`,
      },
      {
        label: 'Price Confirmation',
        icon: priceConf ? ok : fail,
        val: priceConf ? 'confirmed' : '<span class="mt-ld-missing">not confirmed</span>',
      },
      {
        label: 'Final Decision',
        icon: '',
        val: `<span class="mt-pill mt-sc-${finalCls}">${this._esc(finalDec)}</span>`,
        bold: true,
      },
    ];

    el.innerHTML = rows.map(r =>
      `<div class="mt-ladder-row${r.bold ? ' mt-ladder-row--final' : ''}">
        <div class="mt-ladder-label">${this._esc(r.label)}</div>
        <div class="mt-ladder-val">${r.icon}${r.val}</div>
      </div>`
    ).join('');
  }


  // ── Missing ──────────────────────────────────────────────────────
  _updateMissingBlock(manual) {
    const card = document.getElementById('mt-missing-card');
    const body = document.getElementById('mt-missing-body');
    if (!card || !body) return;
    const conds = manual?.missing_conditions;
    if (!Array.isArray(conds) || !conds.length) { card.style.display = 'none'; return; }
    card.style.display = '';
    body.innerHTML = conds.map(c => `<div class="mt-missing-item">• ${this._esc(c)}</div>`).join('');
  }

  // ── Ribbons — data from source_fields ───────────────────────────
  _updateRibbons(manual, sf) {
    const state  = sf.current_state             || '—';
    const exec   = sf.execution_timing_state    || '—';
    const flow   = sf.short_term_flow_direction || '—';
    const level  = sf.nearest_level             ?? null;
    const result = sf.level_result              || '—';

    this._setText('mt-rb-state',     state);
    this._setText('mt-rb-candidate', `quality: ${manual.setup_quality || '—'}`);
    this._setText('mt-rb-exec',      exec);
    this._setText('mt-rb-exec-sub',  `bias: ${manual.manual_bias || '—'}`);
    this._setText('mt-rb-flow',      flow);
    this._setText('mt-rb-flow-sub',  `confidence: ${manual.manual_confidence || '—'}`);
    this._setText('mt-rb-level',     level !== null ? this._fmtETH(level) : '—');
    this._setText('mt-rb-level-sub', `reaction: ${result}`);

    this._colorRibbon('mt-ribbon-state', state);
    this._colorRibbon('mt-ribbon-flow',  flow);
    this._colorRibbon('mt-ribbon-exec',  exec);
  }

  _colorRibbon(id, v) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'mt-ribbon';
    if (['BULLISH','UP','LONG','BUYING'].includes(v))                    el.classList.add('mt-ribbon--green');
    else if (['BEARISH','DOWN','SHORT','SELLING'].includes(v))           el.classList.add('mt-ribbon--red');
    else if (['EXPANSION','BREAKOUT_SETUP','HEDGE_CHASE'].includes(v))  el.classList.add('mt-ribbon--orange');
    else if (['PINNING','COMPRESSION'].includes(v))                     el.classList.add('mt-ribbon--dim');
    else if (v === 'STRUCTURE_UNSTABLE')                                el.classList.add('mt-ribbon--red');
    else if (['EXECUTION_WINDOW_OPEN','EXECUTION_WINDOW'].includes(v))  el.classList.add('mt-ribbon--green');
  }

  // ── Watchlist ────────────────────────────────────────────────────
  async _refreshWatchlist(force = false) {
    const now = Date.now();
    if (this.watchlistLoading) return;
    if (!force && now - this.watchlistLastFetch < 6000) return;
    this.watchlistLoading = true;
    this.watchlistLastFetch = now;
    const startTs = Date.now();
    try {
      const payload = await fetchManualSetupWatchlist();
      this.watchlistRows = Array.isArray(payload.rows) ? payload.rows : [];
      this._renderWatchlist();
      if (this._lastChartStatus === 'DEGRADED' && this._lastDegradedSource === 'watchlist') {
         this._lastChartStatus = 'LIVE';
         this._updateStaleInd();
         this._logHealth(null, 'recovered', 'watchlist', '/api/manual-trading/watchlist', 200, Date.now() - startTs, 1);
         this._lastDegradedSource = null;
      }
    } catch (e) {
      if (this._lastChartStatus === 'LIVE') {
          this._lastChartStatus = 'DEGRADED';
          this._updateStaleInd();
      }
      this._lastDegradedSource = 'watchlist';
      let status = null;
      if (e.message && e.message.includes('HTTP')) {
          const match = e.message.match(/HTTP\s+(\d+)/);
          if (match) status = parseInt(match[1]);
      }
      this._logHealth(e.message || 'fetch error', 'skipped', 'watchlist', '/api/manual-trading/watchlist', status, Date.now() - startTs, 0);
      this._renderWatchlistError();
    }
    finally  { this.watchlistLoading = false; }
  }

  _toggleFilter(kind, value) {
    if (kind === 'actionable') {
      this.filters.actionableOnly = !this.filters.actionableOnly;
    } else {
      const set = kind === 'bias' ? this.filters.biases : this.filters.statuses;
      set.has(value) ? set.delete(value) : set.add(value);
    }
    this._syncFilterButtons();
    this._renderWatchlist();
  }

  _syncFilterButtons() {
    if (!this.container) return;
    this.container.querySelectorAll('[data-mt-filter]').forEach(btn => {
      const kind = btn.dataset.mtFilter;
      if (kind === 'actionable') {
        btn.classList.toggle('is-active', this.filters.actionableOnly);
      } else {
        const set = kind === 'bias' ? this.filters.biases : this.filters.statuses;
        btn.classList.toggle('is-active', set.has(btn.dataset.mtValue));
      }
    });
  }

  _getActionability(row) {
    if (row.actionability) return row.actionability;
    if (row.manual_status === 'ENTRY_CANDIDATE') return 'ACTIONABLE';
    if (row.manual_status === 'WATCH') {
      const curStatus = this.currentData?.manual_setup?.manual_status;
      const curType = this.currentData?.manual_setup?.manual_setup_type;
      if (curStatus === 'AVOID' && (curType === 'UNSTABLE_STRUCTURE_AVOID' || curType === 'NO_ACTIONABLE_SETUP')) {
        return 'BLOCKED';
      }
      const mc = row.missing_conditions || {};
      if (mc.short_term_flow_direction || mc.level_result) return 'NOT_ACTIONABLE';
      if (row.setup_quality === 'INCOMPLETE') return 'NOT_ACTIONABLE';
      if (row.setup_quality === 'FORMING') return 'FORMING';
      if (row.setup_quality === 'ACTIONABLE') return 'ACTIONABLE';
      return 'NOT_ACTIONABLE';
    }
    return 'NOT_ACTIONABLE';
  }

  _filteredRows() {
    const order = { ENTRY_CANDIDATE: 0, WATCH: 1, AVOID: 2 };
    return this.watchlistRows
      .filter(row => {
        if (this.filters.actionableOnly) {
          const act = this._getActionability(row);
          if (act !== 'ACTIONABLE' && act !== 'FORMING') return false;
        }
        if (this.filters.statuses.size && !this.filters.statuses.has(row.manual_status)) return false;
        if (this.filters.biases.size   && !this.filters.biases.has(row.manual_bias))     return false;
        return true;
      })
      .sort((a, b) => {
        const sa = order[a.manual_status] ?? 3;
        const sb = order[b.manual_status] ?? 3;
        if (sa !== sb) return sa - sb;
        // descending time within same status
        const ta = Number(a.time) || 0;
        const tb = Number(b.time) || 0;
        return tb - ta;
      });
  }

  _renderWatchlist() {
    const tbody = document.getElementById('mt-wl-body');
    if (!tbody) return;
    this._syncFilterButtons();
    const rows = this._filteredRows();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="15" class="mt-wl-empty">НЕТ ЗАПИСЕЙ</td></tr>';
      return;
    }

    const currentSetupType = this.currentData?.manual_setup?.manual_setup_type || '';
    const isUnstableBlock = currentSetupType === 'UNSTABLE_STRUCTURE_AVOID';

    tbody.innerHTML = rows.map((row, idx) => {
      const mc = this._missingCompact(row.missing_conditions);
      const mf = this._missingFull(row.missing_conditions);
      const confShort = confirmationShort(row.confirmation_needed);
      // Mark first row as CURRENT (most recent / highest priority)
      const isCurrent = idx === 0;
      const rowClass  = isCurrent ? ' class="mt-wl-current"' : '';

      let displayStatus = this._esc(row.manual_status || '—');
      if (row.manual_status === 'ENTRY_CANDIDATE' && row.candidate_is_new === 0) {
        displayStatus = 'ENTRY CANDIDATE <span style="font-size:0.6em; opacity:0.8; vertical-align: middle;">· already active</span>';
      }

      let statusHtml = `<span class="mt-pill mt-sc-${this._statusCls(row.manual_status)}">${displayStatus}</span>`;
      if (isUnstableBlock && row.manual_status === 'WATCH') {
        statusHtml += `<div style="margin-top:4px; font-size:8px; color:var(--red); font-weight:700; line-height:1; letter-spacing:0.2px;">BLOCKED:<br>structure unstable</div>`;
      }
      
      let candidateBadge = '';
      if (row.manual_status === 'ENTRY_CANDIDATE') {
        if (row.candidate_is_new === 1) {
          candidateBadge = `<div style="margin-top:4px; font-size:9px; color:var(--green); font-weight:700;">NEW SIGNAL</div>`;
        } else if (row.candidate_cooldown_active === 1) {
          candidateBadge = `<div style="margin-top:4px; font-size:9px; color:var(--text-dim); font-weight:700;">ACTIVE SIGNAL</div>`;
        }
      }
      statusHtml += candidateBadge;

      let setupHtml = this._esc(row.setup_type || '—');
      if (row.setup_type === 'FLOW_EXHAUSTION_REVERSAL_SETUP' && row.short_term_flow_direction?.includes('BEARISH') && row.manual_bias === 'NEUTRAL') {
        setupHtml += `<div style="margin-top:2px; font-size:7px; color:var(--orange); font-weight:700;">ЖДАТЬ РАЗВОРОТ FLOW</div>`;
      }

      const act = this._getActionability(row);
      const actCls = { ACTIONABLE: 'mt-ac-actionable', FORMING: 'mt-ac-forming', NOT_ACTIONABLE: 'mt-ac-not-actionable', BLOCKED: 'mt-ac-blocked' }[act] || 'mt-ac-not-actionable';
      let actHtml = `<span class="mt-act-badge ${actCls}">${act.replace('_', ' ')}</span>`;
      if (act === 'NOT_ACTIONABLE' && row.decision_blocker && row.decision_blocker !== 'NONE') {
         actHtml += `<div style="margin-top:4px; font-size:8px; color:var(--red); font-weight:700; line-height:1.2; letter-spacing:0.2px;">BLOCKED:<br>${this._esc(row.decision_blocker.replace(/_/g, ' '))}</div>`;
      }

      return `<tr${rowClass}>
        <td>${this._esc(this._fmtTime(row.time))}${isCurrent ? ' <span class="mt-wl-cur-badge">NOW</span>' : ''}</td>
        <td class="mt-wl-setup">${setupHtml}</td>
        <td>${actHtml}</td>
        <td>${statusHtml}</td>
        <td><span class="mt-pill mt-bc-${this._biasCls(row.manual_bias)}">${this._esc(row.manual_bias || '—')}</span></td>
        <td><span class="mt-pill mt-qc-${this._qualityCls(row.setup_quality)}">${this._esc(row.setup_quality || '—')}</span></td>
        <td class="mt-wl-missing" title="${this._esc(mf)}">${this._esc(mc)}</td>
        <td class="mt-wl-price">${this._fmtETH(row.price)}</td>
        <td class="mt-wl-price">${this._fmtETH(row.nearest_level)}</td>
        <td>${this._esc(row.current_state || '—')}</td>
        <td>${this._esc(row.execution_timing_state || '—')}</td>
        <td>${this._esc(row.event_type || '—')}</td>
        <td>${this._esc(row.level_result || '—')}</td>
        <td>${this._esc(row.short_term_flow_direction || '—')}</td>
        <td class="mt-wl-conf" title="${this._esc(row.confirmation_needed || '')}">${this._esc(confShort)}</td>
        <td class="mt-wl-price">${this._fmtETH(row.invalidation_level)}</td>
      </tr>`;
    }).join('');
  }

  _renderWatchlistError() {
    const tbody = document.getElementById('mt-wl-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="15" class="mt-wl-empty">WATCHLIST НЕДОСТУПЕН</td></tr>';
  }

  // ── Helpers ──────────────────────────────────────────────────────
  _setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }

  _esc(v) {
    return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  _fmtETH(val) {
    const n = safeNumber(val);
    if (n === null) return '—';
    return n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  _fmtTime(value) {
    if (!value) return '—';
    const n = Number(value);
    const d = isFinite(n) ? new Date(n < 1e12 ? n * 1000 : n) : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  _missingCompact(v) {
    if (!Array.isArray(v) || !v.length) return '—';
    return v.length === 1 ? v[0] : `${v.length} missing`;
  }

  _missingFull(v) {
    if (!Array.isArray(v) || !v.length) return '';
    return v.join('\n');
  }

  _statusCls(s)  { return { ENTRY_CANDIDATE:'entry', WATCH:'watch', AVOID:'avoid' }[s] || 'avoid'; }
  _biasCls(b)    { return { LONG:'long', SHORT:'short', NEUTRAL:'neutral' }[b] || 'neutral'; }
  _qualityCls(q) { return { ACTIONABLE:'actionable', FORMING:'forming', INCOMPLETE:'incomplete', INVALIDATED:'invalidated' }[q] || 'incomplete'; }
  _flowCls(f)    {
    if (['BULLISH','UP','LONG','BUYING','POSITIVE'].includes(f)) return 'long';
    if (['BEARISH','DOWN','SHORT','SELLING','NEGATIVE'].includes(f)) return 'short';
    return 'neutral';
  }
  _setupTypeCls(t) {
    if (['NO_TRADE_CHOP','UNSTABLE_STRUCTURE_AVOID'].includes(t)) return 'avoid';
    if (['NO_ACTIONABLE_SETUP','NONE'].includes(t)) return 'incomplete';
    return 'watch';
  }

  // ── Manual Logging ─────────────────────────────────────────────────────────
  _checkAndLogEvent(manual, sf, timestamp) {
    const prev = this._prevManualState;
    const curr = {
      status: manual.manual_status,
      setup: manual.manual_setup_type,
      bias: manual.manual_bias,
      exec: sf.execution_timing_state,
      flow: sf.short_term_flow_direction
    };
    
    if (prev.status !== curr.status ||
        prev.setup !== curr.setup ||
        prev.bias !== curr.bias ||
        prev.exec !== curr.exec ||
        prev.flow !== curr.flow) {
        
        postManualEvent({
            ts: timestamp || new Date().toISOString(),
            frontend_session_id: this.frontendSessionId,
            previous_status: prev.status,
            new_status: curr.status,
            previous_setup_type: prev.setup,
            new_setup_type: curr.setup,
            previous_bias: prev.bias,
            new_bias: curr.bias,
            previous_execution: prev.exec,
            new_execution: curr.exec,
            previous_flow: prev.flow,
            new_flow: curr.flow,
            price: sf.price,
            nearest_level: sf.nearest_level,
            invalidation_level: manual.invalidation_level,
            reason: manual.manual_reason,
            source_snapshot_id: manual.snapshot_id || sf.snapshot_id,
            candidate_key: manual.candidate_key ?? null,
            candidate_is_new: manual.candidate_is_new ?? null
        });
        
        this._prevManualState = curr;
        return true;
    }
    return false;
  }

  _logSnapshot(manual, sf, timestamp, levelMeta = {}, liveCtx = {}) {
    const now = Date.now();
    const lastWrite = window._lastManualSnapshotWriteTs || 0;
    
    // Heartbeat lock/throttle to prevent duplicate bursts
    if (now - lastWrite < 10000) {
        return; // skip duplicate write
    }
    
    // Allow if >= 15s or force if > 30s
    window._lastManualSnapshotWriteTs = now;
    this._lastSnapshotLogTs = now;
    
    // Canonical timestamp for this write
    const canonicalTs = timestamp || new Date().toISOString();

    postManualSnapshot({
      ts: canonicalTs,
      frontend_session_id: this.frontendSessionId,
      frontend_build_version: FRONTEND_BUILD_VERSION,
      code_version: this._codeVersion,
      manual_logger_version: this._manualLoggerVersion,
      manual_ui_version: this._manualUiVersion,
      price: sf.price,
      manual_status: manual.manual_status,
      setup_type: manual.manual_setup_type,
      manual_bias: manual.manual_bias,
      setup_quality: manual.setup_quality,
      actionability: this._getActionability(manual, sf),
      current_state: sf.current_state,
      execution_timing_state: sf.execution_timing_state,
      short_term_flow_direction: sf.short_term_flow_direction,
      event_type: sf.event_type,
      level_result: sf.level_result,
      nearest_level: sf.nearest_level,
      invalidation_level: manual.invalidation_level,
      manual_reason: manual.manual_reason,
      confirmation_needed: manual.confirmation_needed,
      invalidation_condition: manual.invalidation_condition,
      missing_conditions: manual.missing_conditions,
      chart_status: this._lastChartStatus || 'LIVE',
      latest_candle_ts: this._lastCandleIso || null,
      last_fetch_ts: this.klinesLastSuccess ? new Date(this.klinesLastSuccess).toISOString() : null,
      seconds_since_last_fetch: this.klinesLastSuccess ? (Date.now() - this.klinesLastSuccess) / 1000 : null,
      candles_count: this._lastKlines ? this._lastKlines.length : 0,
      source_snapshot_id: sf.live_source_snapshot_id || manual.snapshot_id || sf.snapshot_id,
      source_event_id: sf.live_source_event_id || sf.event_id,
      // Replay reference (event_level_reactions, always stale)
      level_context_ttl_sec:     levelMeta.ttl_sec   ?? sf.level_context_ttl_sec  ?? null,
      level_context_age_sec:     levelMeta.age_sec   ?? sf.level_context_age_sec  ?? null,
      level_context_stale:       levelMeta.is_stale  ?? sf.level_context_stale    ?? true,
      level_context_used:        false,  // replay is never used for live decisions
      source_level_reaction_id:  levelMeta.reaction_id ?? sf.level_context_reaction_id ?? null,
      source_level_reaction_ts:  levelMeta.reaction_ts ?? sf.level_context_reaction_ts ?? null,
      source_event_ts:           null,
      raw_level_result:          levelMeta.raw_level_result  ?? sf.raw_level_result  ?? null,
      raw_nearest_level:         levelMeta.raw_nearest_level ?? sf.raw_nearest_level ?? null,
      raw_level_side:            levelMeta.raw_level_side    ?? sf.raw_level_side    ?? null,
      // Live-safe context (from gamma walls + OHLCV)
      live_level_result:         sf.live_level_result  ?? liveCtx.live_level_result  ?? null,
      live_nearest_level:        sf.live_nearest_level ?? liveCtx.live_nearest_level ?? null,
      live_level_side:           sf.live_level_side    ?? liveCtx.live_level_side    ?? null,
      live_level_type:           sf.live_level_type    ?? liveCtx.live_level_type    ?? null,
      live_distance_pct:         sf.live_distance_pct  ?? liveCtx.live_distance_pct  ?? null,
      live_context_used:         sf.live_context_used  ?? liveCtx.live_context_used  ?? false,
      live_context_source:       sf.live_context_source ?? liveCtx.live_context_source ?? null,
      live_context_ts:           sf.live_context_ts    ?? liveCtx.live_context_ts    ?? null,
      live_source_event_id:      sf.live_source_event_id ?? liveCtx.live_source_event_id ?? null,
      live_source_snapshot_id:   sf.live_source_snapshot_id ?? liveCtx.live_source_snapshot_id ?? null,
      live_source_snapshot_sequence_id: sf.live_source_snapshot_sequence_id ?? liveCtx.live_source_snapshot_sequence_id ?? null,
      
      live_support_level:        sf.live_support_level ?? liveCtx.live_support_level ?? null,
      live_support_distance_pct: sf.live_support_distance_pct ?? liveCtx.live_support_distance_pct ?? null,
      live_support_source:       sf.live_support_source ?? liveCtx.live_support_source ?? null,
      live_support_result:       sf.live_support_result ?? liveCtx.live_support_result ?? null,
      live_support_type:         sf.live_support_type ?? liveCtx.live_support_type ?? null,
      
      live_resistance_level:        sf.live_resistance_level ?? liveCtx.live_resistance_level ?? null,
      live_resistance_distance_pct: sf.live_resistance_distance_pct ?? liveCtx.live_resistance_distance_pct ?? null,
      live_resistance_source:       sf.live_resistance_source ?? liveCtx.live_resistance_source ?? null,
      live_resistance_result:       sf.live_resistance_result ?? liveCtx.live_resistance_result ?? null,
      live_resistance_type:         sf.live_resistance_type ?? liveCtx.live_resistance_type ?? null,
      
      primary_live_level:        sf.primary_live_level ?? liveCtx.primary_live_level ?? null,
      primary_live_side:         sf.primary_live_side ?? liveCtx.primary_live_side ?? null,
      primary_live_distance_pct: sf.primary_live_distance_pct ?? liveCtx.primary_live_distance_pct ?? null,
      primary_live_source:       sf.primary_live_source ?? liveCtx.primary_live_source ?? null,
      primary_live_result:       sf.primary_live_result ?? liveCtx.primary_live_result ?? null,
      
      selected_setup_level: manual.selected_setup_level,
      selected_setup_side: manual.selected_setup_side,
      selected_setup_level_source: manual.selected_setup_level_source,
      selected_setup_level_distance_pct: manual.selected_setup_level_distance_pct,
      selected_setup_level_result: manual.selected_setup_level_result,
      selected_setup_level_type: manual.selected_setup_level_type,
      setup_side_required: manual.setup_side_required,
      
      raw_synthetic_flow_pressure: sf.raw_synthetic_flow_pressure ?? null,
      raw_short_term_flow_direction: sf.raw_short_term_flow_direction ?? null,
      flow_direction_source: sf.flow_direction_source ?? null,
      flow_threshold_used: sf.flow_threshold_used ?? null,
      derived_short_term_flow_direction: sf.derived_short_term_flow_direction ?? null,
      derived_flow_strength: sf.derived_flow_strength ?? null,
      decision_blocker: manual.decision_blocker ?? null,
      
      candidate_key: manual.candidate_key ?? null,
      candidate_is_new: manual.candidate_is_new ?? null,
      candidate_cooldown_active: manual.candidate_cooldown_active ?? null,
      candidate_first_seen_ts: manual.candidate_first_seen_ts ?? null,
      candidate_last_seen_ts: manual.candidate_last_seen_ts ?? null,
      candidate_cooldown_sec: manual.candidate_cooldown_sec ?? null,
      candidate_age_sec: manual.candidate_age_sec ?? null,
      
      recent_return_3m: manual.recent_return_3m ?? null,
      recent_return_5m: manual.recent_return_5m ?? null,
      latest_close: manual.latest_close ?? null,
      latest_low: manual.latest_low ?? null,
      latest_high: manual.latest_high ?? null,
      close_vs_selected_level: manual.close_vs_selected_level ?? null,
      fresh_lower_low_after_touch: manual.fresh_lower_low_after_touch ?? null,
      fresh_higher_high_after_touch: manual.fresh_higher_high_after_touch ?? null,
      price_confirmation_status: manual.price_confirmation_status ?? null,
      falling_knife_guard: manual.falling_knife_guard ?? null,
      impulse_guard_reason: manual.impulse_guard_reason ?? null,
      
      price_context_source: manual.price_context_source ?? null,
      price_context_candle_count: manual.price_context_candle_count ?? null,
      price_context_latest_ts: manual.price_context_latest_ts ?? null,
      
      forming_key: manual.forming_key ?? null,
      forming_is_new: manual.forming_is_new ?? null,
      forming_cooldown_active: manual.forming_cooldown_active ?? null,
      forming_first_seen_ts: manual.forming_first_seen_ts ?? null,
      forming_age_sec: manual.forming_age_sec ?? null,
      
      previous_candidate_key: manual.previous_candidate_key ?? null,
      previous_candidate_ts: manual.previous_candidate_ts ?? null,
      previous_candidate_entry_price: manual.previous_candidate_entry_price ?? null,
      previous_candidate_level: manual.previous_candidate_level ?? null,
      previous_candidate_invalidation: manual.previous_candidate_invalidation ?? null,
      previous_candidate_mfe_r: manual.previous_candidate_mfe_r ?? null,
      previous_candidate_reached_1r: manual.previous_candidate_reached_1r ?? null,
      level_ladder_guard_active: manual.level_ladder_guard_active ?? null,

      main_context_conflict_active: manual.main_context_conflict_active ?? null,
      main_context_conflict_side: manual.main_context_conflict_side ?? null,
      main_context_conflict_reason: manual.main_context_conflict_reason ?? null,
      main_context_conflict_reaction_label: manual.main_context_conflict_reaction_label ?? null,
      main_context_conflict_level_side: manual.main_context_conflict_level_side ?? null,
      main_context_conflict_level_price: manual.main_context_conflict_level_price ?? null,
      main_context_conflict_ts: manual.main_context_conflict_ts ?? null,
      main_context_conflict_age_sec: manual.main_context_conflict_age_sec ?? null,
      main_context_conflict_stale: manual.main_context_conflict_stale ?? null,
      main_context_conflict_source: manual.main_context_conflict_source ?? null,
      latest_main_context_direction: manual.latest_main_context_direction ?? null,
      latest_main_context_reaction_label: manual.latest_main_context_reaction_label ?? null,
      latest_main_context_level_side: manual.latest_main_context_level_side ?? null,
      latest_main_context_age_sec: manual.latest_main_context_age_sec ?? null,
      checked_main_context_count: manual.checked_main_context_count ?? null,
      // v34: price source separation
      reference_price:          manual.reference_price          ?? null,
      execution_price:          manual.execution_price          ?? null,
      basis:                    manual.basis                    ?? null,
      basis_pct:               manual.basis_pct               ?? null,
      execution_venue:          manual.execution_venue          ?? null,
      execution_symbol:         manual.execution_symbol         ?? null,
      reference_venue:          manual.reference_venue          ?? null,
      reference_symbol:         manual.reference_symbol         ?? null,
      ohlcv_source:             manual.ohlcv_source             ?? null,
      price_source_for_entry:   manual.price_source_for_entry   ?? null,
      price_source_for_levels:  manual.price_source_for_levels  ?? null,
      price_source_for_outcome: manual.price_source_for_outcome ?? null,
      // v1.2: reference sanity fields
      reference_price_status:   manual.reference_price_status   ?? null,
      basis_valid:              manual.basis_valid              ?? null,
      reference_price_source:   manual.reference_price_source   ?? null,
      reference_candidate_count: manual.reference_candidate_count ?? null,
      // Outcome/replay execution fields
      entry_execution_price:    manual.entry_execution_price    ?? null,
      mfe_r_execution:          manual.mfe_r_execution          ?? null,
      mae_r_execution:          manual.mae_r_execution          ?? null,
    });
  }

  _logHealth(errorMsg = null, mode = 'skipped', errorSource = null, endpointName = null, httpStatus = null, fetchDurationMs = null, recovered = 0) {
    const now = Date.now();
    postManualHealth({
      ts: new Date().toISOString(),
      frontend_session_id: this.frontendSessionId,
      frontend_tab_visible: this._tabVisible,
      chart_status: this._lastChartStatus || 'LIVE',
      fetch_status: errorMsg ? 'ERROR' : 'OK',
      last_successful_fetch_ts: this.klinesLastSuccess ? new Date(this.klinesLastSuccess).toISOString() : null,
      latest_candle_ts: this._lastCandleIso || null,
      latest_candle_close: this._lastCandleClose ?? null,
      candles_count: this._lastKlines ? this._lastKlines.length : 0,
      seconds_since_last_fetch: this.klinesLastSuccess ? (now - this.klinesLastSuccess) / 1000 : null,
      seconds_since_latest_candle: this._lastCandleTs ? (now / 1000 - this._lastCandleTs) : null,
      error_message: errorMsg,
      update_mode: mode,
      error_source: errorSource,
      endpoint_name: endpointName,
      http_status: httpStatus,
      fetch_duration_ms: fetchDurationMs,
      recovered_on_next_fetch: recovered
    });
    this._lastHealthLogTs = now;
  }
}
