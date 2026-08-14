/**
 * ManualChart — read-only 1m ETH price chart for MOS Manual Trading tab.
 * lightweight-charts v5 (addSeries API).
 *
 * Layers:
 *  1. CandlestickSeries — 1m OHLCV
 *  2. LineSeries (price) — current spot price
 *  3. LineSeries (near)  — nearest_level (gold dashed)
 *  4. LineSeries (inval) — invalidation_level (red dotted)
 *  5. HTML overlay canvas — markers drawn as DOM <div> badges
 *     (avoids LW Charts v5 marker API inconsistencies)
 */

import {
  createChart,
  CandlestickSeries,
  LineSeries,
} from 'lightweight-charts';

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
  bg:        '#08080f',
  grid:      '#12121e',
  text:      '#a0a8c0',
  border:    '#1e1e30',
  bullBody:  '#00d4aa',
  bearBody:  '#ff4560',
  wick:      '#607080',
  priceLine: '#e0e0ff',
  nearLvl:   '#f4c15a',
  invalLvl:  '#ff4560',
};

const STATUS_COLOR = {
  ENTRY_CANDIDATE: { bg: '#00d4aa22', border: '#00d4aa', text: '#00d4aa' },
  WATCH:           { bg: '#f4c15a22', border: '#f4c15a', text: '#f4c15a' },
  AVOID:           { bg: '#ff456022', border: '#ff4560', text: '#ff4560' },
};

// Short abbreviations for setup_type
const SETUP_SHORT = {
  MID_RANGE_EXPANSION_SETUP:   'MRX',
  FLOW_EXHAUSTION_REVERSAL_SETUP: 'FER',
  BREAKOUT_CONTINUATION_SETUP: 'BC',
  NO_ACTIONABLE_SETUP:         'NO SETUP',
  NO_TRADE_CHOP:               'CHOP',
  UNSTABLE_STRUCTURE_AVOID:    'UNSTBL',
};

// Short abbreviations for event_type
const EVENT_SHORT = {
  BREAKOUT_ALERT:         'BA',
  VOLATILITY_EXPANSION:   'VE',
  EXECUTION_WINDOW_OPEN:  'EWO',
  GAMMA_COLLAPSE:         'GC',
  REGIME_CHANGE:          'RC',
  MEAN_REVERSION_ALERT:   'MRA',
  COMPRESSION_DETECTED:   'CD',
};

// Short abbreviations for level_result
const LEVEL_SHORT = {
  LEVEL_BREAK:            'LB',
  SUPPORT_DEFENSE:        'SD',
  FALSE_BREAK:            'FB',
  MID_RANGE_EXPANSION:    'MRX',
  RESISTANCE_REJECTION:   'RR',
  NO_REACTION:            '',   // hidden
};

function _abbrev(map, key) {
  if (!key) return '';
  return map[key] ?? key.slice(0, 4);
}

export class ManualChart {
  constructor(onHealthError) {
    this._onHealthError = onHealthError || (() => {});
    this._chart      = null;
    this._candles    = null;
    this._priceLine  = null;
    this._nearLine   = null;
    this._invalLine  = null;
    this._container  = null;
    this._overlay    = null;  // DOM div for HTML markers
    this._lastCandles = [];   // stored for coordinate mapping

    this._isInitialLoad = true;
    this._userHasCustomRange = false;
    this._ignoreRangeChange = false;
    this._debug = true; // Temporary debug logs
  }

  /** Mount into visible DOM element (call in onActivate via rAF) */
  mount(container) {
    if (this._chart) return;
    this._container = container;

    this._chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: C.bg },
        textColor:  C.text,
        fontSize:   10,
      },
      grid: {
        vertLines: { color: C.grid },
        horzLines: { color: C.grid },
      },
      crosshair: { mode: 1 },
      rightPriceScale: {
        borderColor:  C.border,
        scaleMargins: { top: 0.1, bottom: 0.08 },
      },
      timeScale: {
        borderColor:    C.border,
        timeVisible:    true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale:  true,
    });

    this._candles = this._chart.addSeries(CandlestickSeries, {
      upColor:          C.bullBody,
      downColor:        C.bearBody,
      borderUpColor:    C.bullBody,
      borderDownColor:  C.bearBody,
      wickUpColor:      C.wick,
      wickDownColor:    C.wick,
      priceLineVisible: false,
    });

    this._overlayLines = {
      price: null,
      nearest_level: null,
      invalidation_level: null,
      selected_setup_level: null,
      live_support_level: null,
      live_resistance_level: null,
      primary_live_level: null,
      tp_1r: null,
      tp_2r: null,
      tp_3r: null
    };

    // HTML overlay for markers (sits on top of canvas)
    this._overlay = document.createElement('div');
    this._overlay.className = 'mc-overlay';
    this._overlay.style.cssText =
      'position:absolute;inset:0;pointer-events:none;overflow:hidden;z-index:2;';
    container.style.position = 'relative';
    container.appendChild(this._overlay);

    // Redraw overlay markers on scroll/scale
    this._chart.timeScale().subscribeVisibleTimeRangeChange(() => {
      this._redrawOverlay();
    });

    // Track if user scrolled or zoomed away from live view
    this._chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
      if (this._ignoreRangeChange || !logicalRange) return;
      this._userHasCustomRange = true;
    });
  }

  /**
   * Feed fresh data.
   * @param {Array}  klines   — [{ts,o,h,l,c,v}] from /api/manual-trading/kline
   * @param {Object} overlay  — payload from /api/manual-trading/current
   * @param {Array}  watchlist — rows from /api/manual-trading/watchlist (optional)
   */
  update(klines, overlay, watchlist) {
    let mode = 'skipped';
    if (!this._chart) return { mode };
    if (Array.isArray(klines) && klines.length) {
      mode = this._setCandles(klines);
    }
    this._setOverlay(overlay, watchlist || []);
    return { mode };
  }

  destroy() {
    if (this._chart) { this._chart.remove(); this._chart = null; }
    this._candles   = null;
    this._overlayLines = {};
    this._overlay   = null;
    this._lastCandles = [];
  }

  _onResize() {
    if (this._chart) {
      try { this._chart.timeScale().fitContent(); } catch { /* ignore */ }
    }
  }

  resetView() {
    if (!this._chart) return;
    if (this._debug) console.log('[ManualChart] reset view clicked');
    this._ignoreRangeChange = true;
    this._chart.timeScale().fitContent();
    this._userHasCustomRange = false;
    setTimeout(() => { this._ignoreRangeChange = false; }, 50);
  }

  // ── Private ──────────────────────────────────────────────────────────────────

  _setCandles(raw) {
    const candles = raw.map(k => {
      if (Array.isArray(k)) {
        const ms = Number(k[0]);
        return { time: ms > 1e12 ? Math.floor(ms / 1000) : ms,
                 open: +k[1], high: +k[2], low: +k[3], close: +k[4] };
      }
      const ms = Number(k.ts ?? k.time ?? k.timestamp ?? 0);
      return {
        time:  ms > 1e12 ? Math.floor(ms / 1000) : ms,
        open:  Number(k.o ?? k.open  ?? 0),
        high:  Number(k.h ?? k.high  ?? 0),
        low:   Number(k.l ?? k.low   ?? 0),
        close: Number(k.c ?? k.close ?? 0),
      };
    })
    .filter(c => c.time > 0 && c.high > 0)
    .sort((a, b) => a.time - b.time);

    const seen = new Set();
    const deduped = candles.filter(c => {
      if (seen.has(c.time)) return false;
      seen.add(c.time); return true;
    });

    if (!deduped.length) return 'skipped';

    if (!this._isInitialLoad && this._lastCandles && this._lastCandles.length > 0) {
      const lastKnown = this._lastCandles[this._lastCandles.length - 1];
      const updates = deduped.filter(c => c.time >= lastKnown.time);
      const isBackfill = deduped[0].time < this._lastCandles[0].time;
      
      // If the array just shifted by 1 or updated the last candle, use update()
      if (!isBackfill && updates.length > 0 && updates.length < deduped.length) {
        if (this._debug) console.log('[ManualChart] normal candle update');
        for (const c of updates) {
          this._candles.update(c);
        }
        
        // Merge into lastCandles so it grows
        const oldCands = this._lastCandles.filter(c => c.time < updates[0].time);
        this._lastCandles = [...oldCands, ...updates];
        return 'update';
      }
    }

    if (this._isInitialLoad) {
      if (this._debug) console.log('[ManualChart] initial fitContent');
      this._ignoreRangeChange = true;
      this._candles.setData(deduped);
      this._chart.timeScale().fitContent();
      this._isInitialLoad = false;
      setTimeout(() => { this._ignoreRangeChange = false; }, 50);
    } else {
      if (this._debug) console.log('[ManualChart] skipped fitContent during polling / full setData');
      const timeScale = this._chart.timeScale();
      const visibleRange = timeScale.getVisibleLogicalRange();
      
      let targetRange = null;
      if (this._userHasCustomRange && visibleRange && this._lastCandles && this._lastCandles.length) {
        const fromIdx = Math.max(0, Math.min(this._lastCandles.length - 1, Math.floor(visibleRange.from)));
        const toIdx = Math.max(0, Math.min(this._lastCandles.length - 1, Math.ceil(visibleRange.to)));
        const fromTime = this._lastCandles[fromIdx].time;
        const toTime = this._lastCandles[toIdx].time;
        
        const newFromIdx = deduped.findIndex(c => c.time >= fromTime);
        const newToIdx = deduped.findIndex(c => c.time >= toTime);
        
        if (newFromIdx !== -1 && newToIdx !== -1) {
          targetRange = { from: newFromIdx, to: newToIdx };
        }
      }
      
      this._ignoreRangeChange = true;
      this._candles.setData(deduped);
      
      if (targetRange) {
        if (this._debug) console.log('[ManualChart] preserved visible range based on time');
        timeScale.setVisibleLogicalRange(targetRange);
      } else if (this._userHasCustomRange && visibleRange) {
        timeScale.setVisibleLogicalRange(visibleRange);
      }
      setTimeout(() => { this._ignoreRangeChange = false; }, 50);
    }
    
    this._lastCandles = deduped;
    return 'setData';
  }

  _setOverlay(payload, watchlist) {
    if (!payload || payload.status === 'loading') return;

    const manual       = payload.manual_setup  || {};
    const sf           = payload.source_fields || {};
    const liveCtx      = payload.live_level_context || {};

    const spot         = sf.price               ?? null;
    const nearLevel    = sf.nearest_level        ?? null;
    const invalLevel   = manual.invalidation_level ?? null;
    const setupLvl     = manual.selected_setup_level ?? null;
    const suppLvl      = liveCtx.live_support_level ?? null;
    const resLvl       = liveCtx.live_resistance_level ?? null;
    const primLive     = liveCtx.primary_live_level ?? null;
    const tp1          = manual.tp_1r ?? null;
    const tp2          = manual.tp_2r ?? null;
    const tp3          = manual.tp_3r ?? null;

    const rawTs  = payload.timestamp;
    let parsedTs = Number(rawTs);
    if (Number.isNaN(parsedTs) && typeof rawTs === "string") {
      parsedTs = new Date(rawTs).getTime();
    }
    const nowSec = parsedTs && !Number.isNaN(parsedTs)
      ? (parsedTs > 1e12 ? Math.floor(parsedTs / 1000) : parsedTs)
      : Math.floor(Date.now() / 1000);

    // Ribbons data (Current state for the entire visible range as fallback)
    this._ribbonsData = {
      state: sf.current_state || '',
      exec:  sf.execution_timing_state || '',
      flow:  sf.short_term_flow_direction || ''
    };

    function safeNumberOrExtract(value) {
      if (value === null || value === undefined) return null;

      if (typeof value === "number") {
        return Number.isFinite(value) ? value : null;
      }

      if (typeof value === "string") {
        const s = value.trim();
        if (!s || ["null", "undefined", "nan", "-", "none"].includes(s.toLowerCase())) {
          return null;
        }

        // remove commas from numbers like "62,498.8"
        const direct = Number(s.replace(/,/g, ""));
        if (Number.isFinite(direct)) return direct;

        // extract first price-like number from strings like "SUPPORT 62,498.8"
        const match = s.match(/([0-9]{2,3},?[0-9]{3}(?:\.\d+)?|[0-9]+(?:\.\d+)?)/);
        if (match) {
          const extracted = Number(match[1].replace(/,/g, ""));
          if (Number.isFinite(extracted)) return extracted;
        }
      }

      return null;
    }

    function firstValidNumber(...values) {
      for (const value of values) {
        const n = safeNumberOrExtract(value);
        if (n !== null && Number.isFinite(n) && n > 0) return n;
      }
      return null;
    }

    const safeN = safeNumberOrExtract;

    const removeOverlayLineSafe = (fieldName) => {
        const line = this._overlayLines?.[fieldName];
        if (!line) return;

        try {
            if (this._candles && typeof this._candles.removePriceLine === "function") {
                this._candles.removePriceLine(line);
            }
        } catch (e) {
            console.debug(`[overlay-remove-error] ${fieldName}: ${e.message}`);
        } finally {
            this._overlayLines[fieldName] = null;
        }
    };

    const upsertOverlayLineSafe = (fieldName, rawValue, buildOptions) => {
      const price = safeN(rawValue);

      if (price === null || !Number.isFinite(price) || price <= 0) {
        removeOverlayLineSafe(fieldName);
        console.debug("[manual-overlay-skip]", fieldName, rawValue, "NULL_OR_INVALID_PRICE");
        return { ok: true, skipped: true };
      }

      try {
        if (!this._candles) return { ok: false, skipped: true, reason: "NO_CANDLE_SERIES" };

        const options = buildOptions(price);

        if (!options || !Number.isFinite(options.price) || options.price <= 0 || !options.title) {
          removeOverlayLineSafe(fieldName);
          return { ok: true, skipped: true, reason: "INVALID_OPTIONS" };
        }

        if (this._overlayLines[fieldName]) {
          this._overlayLines[fieldName].applyOptions(options);
        } else {
          this._overlayLines[fieldName] = this._candles.createPriceLine(options);
        }
        
        console.debug("[manual-overlay-render]", fieldName, price);

        return { ok: true, rendered: true };
      } catch (err) {
        console.warn("[manual-overlay-error]", fieldName, rawValue, err);
        // this._onHealthError(`overlay rendering failed: ${fieldName} (${err.message})`, fieldName);
        return { ok: false, error: true };
      }
    };

    console.debug("[manual-current-payload-levels]", {
      support: payload.support,
      resistance: payload.resistance,
      primary_live: payload.primary_live,
      selected_for_setup: payload.selected_for_setup,
      selected_setup_level: payload.selected_setup_level,
      selected_setup_side: payload.selected_setup_side,
      invalidation_level: payload.invalidation_level,
      nearest_level: payload.nearest_level,
      live_support_level: payload.live_support_level,
      live_resistance_level: payload.live_resistance_level,
      primary_live_level: payload.primary_live_level
    });

    const normalizedLevels = {
      support: firstValidNumber(
        liveCtx.live_support_level,
        manual.support_level,
        manual.support,
        payload.support
      ),
      
      resistance: firstValidNumber(
        liveCtx.live_resistance_level,
        manual.resistance_level,
        manual.resistance,
        payload.resistance
      ),
      
      nearest: firstValidNumber(
        sf.nearest_level,
        liveCtx.primary_live_level,
        manual.primary_live,
        payload.nearest_level
      ),
      
      selected: firstValidNumber(
        manual.selected_setup_level,
        manual.selected_for_setup_level,
        manual.selected_for_setup,
        manual.selected_level,
        payload.selected_setup_level
      ),
      
      invalidation: firstValidNumber(
        manual.invalidation_level,
        manual.stop_level,
        manual.stop,
        payload.invalidation_level
      )
    };

    console.debug("[manual-overlay-normalized-levels]", normalizedLevels);

    // spot price uses safeN since it's just a simple number
    upsertOverlayLineSafe('price', safeN(spot), p => ({
      price: p, title: `Current: ${p}`, color: '#c0c0c0', lineWidth: 1, lineStyle: 0, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('nearest_level', normalizedLevels.nearest, p => ({
      price: p, title: `Level: ${p}`, color: '#ffffff', lineWidth: 1, lineStyle: 2, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('invalidation_level', normalizedLevels.invalidation, p => ({
      price: p, title: `Invalid: ${p}`, color: '#ff3333', lineWidth: 1, lineStyle: 3, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('selected_setup_level', normalizedLevels.selected, p => ({
      price: p, title: `Setup Lvl: ${p}`, color: '#b0b8d0', lineWidth: 1, lineStyle: 2, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('live_support_level', normalizedLevels.support, p => ({
      price: p, title: `Support: ${p}`, color: '#00d4aa', lineWidth: 1, lineStyle: 2, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('live_resistance_level', normalizedLevels.resistance, p => ({
      price: p, title: `Resist: ${p}`, color: '#ff4560', lineWidth: 1, lineStyle: 2, axisLabelVisible: true
    }));
    
    upsertOverlayLineSafe('tp_1r', safeN(tp1), p => ({
      price: p, title: `TP1: ${p}`, color: '#00d4aa', lineWidth: 1, lineStyle: 3, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('tp_2r', safeN(tp2), p => ({
      price: p, title: `TP2: ${p}`, color: '#00d4aa', lineWidth: 1, lineStyle: 3, axisLabelVisible: true
    }));
    upsertOverlayLineSafe('tp_3r', safeN(tp3), p => ({
      price: p, title: `TP3: ${p}`, color: '#00d4aa', lineWidth: 1, lineStyle: 3, axisLabelVisible: true
    }));

    // Collect HTML marker data
    this._pendingMarkers = this._buildMarkers(payload, watchlist, nowSec);
    this._redrawOverlay();
  }

  /** Build list of marker descriptors from current + watchlist data */
  _buildMarkers(payload, watchlist, nowSec) {
    const markers = [];
    const manual = payload.manual_setup  || {};
    const sf     = payload.source_fields || {};

    const manualStatus = manual.manual_status      || '';
    const setupType    = manual.manual_setup_type  || '';
    const manualBias   = manual.manual_bias        || '';
    const eventType    = sf.event_type             || '';
    const levelResult  = sf.level_result           || '';
    const nearLevel    = sf.nearest_level          ?? null;

    const STATUS_SHORT = { ENTRY_CANDIDATE: 'E', WATCH: 'W', AVOID: 'A' };
    const getFullText = (s, t, b) => [s, t, b].filter(Boolean).join(' | ');

    this._noLevelAlert = !nearLevel;

    // 1. Current decision marker on latest candle
    if (manualStatus && nowSec) {
      const stShort = _abbrev(SETUP_SHORT, setupType);
      markers.push({
        time:     nowSec,
        position: 'current_badge',
        status:   manualStatus,
        label:    `${STATUS_SHORT[manualStatus] || manualStatus.charAt(0)} / ${stShort}`,
        fullText: getFullText(manualStatus, setupType, manualBias),
        isCurrent: true,
      });
    }

    // 2. Event marker
    if (eventType && nowSec) {
      const evShort = _abbrev(EVENT_SHORT, eventType);
      markers.push({
        time:     nowSec,
        position: 'below',
        status:   'EVENT',
        label:    evShort || eventType.slice(0, 3),
        fullText: eventType,
      });
    }

    // 3. Level reaction marker
    if (levelResult && levelResult !== 'NO_REACTION' && nowSec) {
      const lvShort = _abbrev(LEVEL_SHORT, levelResult);
      if (lvShort) {
        markers.push({
          time:     nowSec - 60,
          position: 'below',
          status:   'LEVEL',
          label:    lvShort,
          fullText: levelResult,
        });
      }
    }



    // 5. Watchlist setup markers
    const used = new Set();
    // Sort ascending by time to track state changes chronologically
    const sortedWl = [...(watchlist || [])].sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
    
    let prevStatus = null;
    let prevSetup = null;
    const finalWl = [];
    const watchIndices = [];

    for (const row of sortedWl) {
      const ts = Number(row.time);
      if (!ts || !row.manual_status) continue;

      const statusChanged = (row.manual_status !== prevStatus);
      const setupChanged = (row.setup_type !== prevSetup);
      
      prevStatus = row.manual_status;
      prevSetup = row.setup_type;

      let keep = false;

      if (row.manual_status === 'ENTRY_CANDIDATE') {
        keep = true;
      } else if (row.manual_status === 'AVOID') {
        const isUnstable = row.setup_type === 'UNSTABLE_STRUCTURE_AVOID';
        if (isUnstable || statusChanged) {
          keep = true;
        }
      } else if (row.manual_status === 'WATCH') {
        keep = true;
      }

      if (keep) {
        finalWl.push({ row, statusChanged, setupChanged });
        if (row.manual_status === 'WATCH') {
          watchIndices.push(finalWl.length - 1);
        }
      }
    }

    const last5WatchIndices = new Set(watchIndices.slice(-5));

    for (let i = 0; i < finalWl.length; i++) {
      const { row, setupChanged, statusChanged } = finalWl[i];
      
      if (row.manual_status === 'WATCH') {
        if (!setupChanged && !statusChanged && !last5WatchIndices.has(i)) {
          continue; // skip repeated watch
        }
      }

      const ts = Number(row.time);
      const rowSec = ts > 1e12 ? Math.floor(ts / 1000) : ts;
      const snap = Math.floor(rowSec / 60) * 60;
      if (used.has(snap)) continue;
      used.add(snap);

      markers.push({
        time:     snap,
        position: 'above',
        status:   row.manual_status,
        label:    STATUS_SHORT[row.manual_status] || row.manual_status.charAt(0),
        fullText: getFullText(row.manual_status, row.setup_type, row.manual_bias),
        isCurrent: false,
      });
    }

    return markers;
  }

  /** Re-draw HTML overlay markers. Called on visible range change. */
  _redrawOverlay() {
    if (!this._overlay || !this._chart || !this._candles) return;
    const markers = this._pendingMarkers || [];
    this._overlay.innerHTML = '';

    const chartEl    = this._container;
    const chartRect  = chartEl.getBoundingClientRect();
    const timeScale  = this._chart.timeScale();
    const priceScale = this._candles.priceScale();

    for (const m of markers) {
      let xPx;
      try {
        xPx = timeScale.timeToCoordinate(m.time);
      } catch { continue; }
      if (xPx === null || xPx === undefined) continue;
      if (xPx < 0 || xPx > chartRect.width + 20) continue;

      const pal = STATUS_COLOR[m.status] || { bg: '#ffffff10', border: '#606080', text: '#a0a8c0' };

      const badge = document.createElement('div');
      badge.className = 'mc-marker';
      badge.style.cssText = `
        position: absolute;
        left: ${Math.round(xPx)}px;
        transform: translateX(-50%);
        background: ${pal.bg};
        border: 1px solid ${pal.border};
        color: ${pal.text};
        font-size: 9px;
        font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
        padding: 2px 5px;
        border-radius: 3px;
        white-space: nowrap;
        letter-spacing: 0.3px;
        line-height: 1.3;
        ${m.isCurrent ? 'box-shadow: 0 0 6px ' + pal.border + '60;' : ''}
      `;

      if (m.position === 'above') {
        badge.style.top = '8px';
      } else if (m.position === 'below') {
        badge.style.bottom = '24px'; // above time axis
      } else if (m.position === 'current_badge') {
        badge.style.top = '15%'; 
        badge.style.transform = 'translateX(10px)'; 
        badge.style.fontSize = '10px';
        badge.style.padding = '4px 8px';
        badge.innerHTML = `<span style="opacity:0.6;font-size:8px;">CURRENT:</span><br>${m.label}`;
      }

      badge.textContent = m.label;
      if (m.fullText) {
        badge.title = m.fullText;
        badge.style.cursor = 'help';
      }
      this._overlay.appendChild(badge);
    }

    // No Level Annotation
    if (this._noLevelAlert) {
      const alert = document.createElement('div');
      alert.className = 'mc-marker';
      alert.style.cssText = `
        position: absolute;
        top: 40px;
        right: 64px;
        background: rgba(255, 69, 96, 0.1);
        border: 1px solid rgba(255, 69, 96, 0.4);
        color: #ff4560;
        font-size: 10px;
        font-weight: 700;
        padding: 4px 8px;
        border-radius: 4px;
        z-index: 10;
        pointer-events: auto;
      `;
      alert.textContent = 'No actionable level';
      this._overlay.appendChild(alert);
    }

    // Ribbons (State, Execution, Flow)
    if (this._ribbonsData) {
      const rbContainer = document.createElement('div');
      rbContainer.style.cssText = `
        position: absolute;
        bottom: 0;
        left: 0;
        right: 50px; /* leave space for price scale */
        height: 12px;
        display: flex;
        flex-direction: column;
        opacity: 0.8;
        z-index: 1;
      `;

      const getBg = (val) => {
        const u = String(val).toUpperCase();
        if (u.includes('BULLISH') || u.includes('UP') || u.includes('OPEN') || u.includes('ACTIVE') || u.includes('REBALANCE')) return '#00d4aa66';
        if (u.includes('BEARISH') || u.includes('DOWN') || u.includes('CLOSED') || u.includes('AVOID') || u.includes('PINNING') || u.includes('COMPRESSION') || u.includes('EXHAUSTION')) return '#ff456066';
        if (u.includes('WATCH') || u.includes('TRANSITION') || u.includes('BUILDUP') || u.includes('CAUTION')) return '#f4c15a66';
        return '#60608055';
      };

      const createRb = (val, title) => {
        const r = document.createElement('div');
        r.style.flex = '1';
        r.style.background = getBg(val);
        r.title = `${title}: ${val}`;
        r.style.display = 'flex';
        r.style.alignItems = 'center';
        r.style.paddingLeft = '4px';
        r.innerHTML = `<span style="font-family:'JetBrains Mono', monospace; font-size:7px; color:rgba(255,255,255,0.7); font-weight:900; letter-spacing:0.5px; line-height:1;">${title.toUpperCase()}</span>`;
        return r;
      };

      rbContainer.appendChild(createRb(this._ribbonsData.state, 'State'));
      rbContainer.appendChild(createRb(this._ribbonsData.exec, 'Execution'));
      rbContainer.appendChild(createRb(this._ribbonsData.flow, 'Flow'));
      this._overlay.appendChild(rbContainer);
    }
  }
}
