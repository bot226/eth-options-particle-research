/**
 * API Client — REST + WebSocket для связи с backend.
 */

const API_BASE = '';  // Proxy через Vite

export async function fetchSnapshot() {
  const resp = await fetchWithTimeout(`${API_BASE}/api/snapshot`, 5000);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function fetchKline() {
  const resp = await fetch(`${API_BASE}/api/kline`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function fetchWithTimeout(url, timeoutMs = 8000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    clearTimeout(id);
    return resp;
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

export async function fetchManualSetupWatchlist() {
  const resp = await fetchWithTimeout(`${API_BASE}/api/manual-trading/watchlist`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function fetchManualTradingChartOverlay() {
  const resp = await fetch(`${API_BASE}/api/manual-trading/chart-overlay`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ── Manual Logging (Non-blocking wrappers) ──────────────────────────────────
export function postManualSnapshot(payload) {
  fetch(`${API_BASE}/api/manual-trading/log-snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  .then(res => res.json())
  .then(data => {
    if (data && data.status === 'error') {
      console.warn("postManualSnapshot error:", data.message);
      postManualHealth({
        error_source: 'snapshot_logging',
        endpoint_name: '/api/manual-trading/log-snapshot',
        error_message: String(data.message)
      });
    }
  })
  .catch((err) => {
    console.warn("postManualSnapshot network error:", err);
    postManualHealth({
      error_source: 'snapshot_logging',
      endpoint_name: '/api/manual-trading/log-snapshot',
      error_message: String(err)
    });
  });
}

export function postManualEvent(payload) {
  fetch(`${API_BASE}/api/manual-trading/log-event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(() => {});
}

export function postManualHealth(payload) {
  fetch(`${API_BASE}/api/manual-trading/log-health`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(() => {});
}

export async function fetchManualLoggingStatus() {
  const resp = await fetch(`${API_BASE}/api/manual-trading/logging-status`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}


/**
 * WebSocket клиент — подключается к /ws/stream для real-time обновлений.
 * Надёжный reconnect: exponential backoff, ping/pong heartbeat, tab focus reconnect.
 */
export class DashboardWS {
  constructor(onUpdate, onStatus) {
    this._onUpdate  = onUpdate;
    this._onStatus  = onStatus;
    this._ws        = null;
    this._reconnectTimer = null;
    this._pingTimer = null;
    this._pongTimer = null;
    this._attempt   = 0;           // reconnect attempt counter (for backoff)
    this._destroyed = false;
    this._connecting = false;

    // Reconnect when tab becomes visible again
    this._onVisibility = () => {
      if (document.visibilityState === 'visible' && this._isDown()) {
        this._scheduleReconnect(0);
      }
    };
    document.addEventListener('visibilitychange', this._onVisibility);
  }

  // ── Public ────────────────────────────────────────────────────────────────

  connect() {
    if (this._destroyed || this._connecting) return;
    this._cancelTimers();
    this._connecting = true;

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws/stream`;

    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      this._connecting = false;
      this._onStatus('error');
      this._scheduleReconnect();
      return;
    }

    this._ws = ws;

    ws.onopen = () => {
      if (ws !== this._ws) return;   // stale socket
      this._connecting = false;
      this._attempt = 0;             // reset backoff on successful connect
      this._onStatus('connected');
      this._startHeartbeat();
    };

    ws.onmessage = (evt) => {
      if (ws !== this._ws) return;
      // pong response — heartbeat alive
      if (evt.data === 'pong' || evt.data === '{"type":"pong"}') {
        this._clearPongTimeout();
        return;
      }
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'update') {
          this._onUpdate(msg);
        }
      } catch (e) { /* ignore malformed frames */ }
    };

    ws.onclose = (evt) => {
      if (ws !== this._ws) return;
      this._connecting = false;
      this._stopHeartbeat();
      this._onStatus('disconnected');
      if (!this._destroyed) this._scheduleReconnect();
    };

    ws.onerror = () => {
      if (ws !== this._ws) return;
      this._connecting = false;
      this._stopHeartbeat();
      this._onStatus('error');
      // close triggers onclose → reconnect; if not, schedule here
      try { ws.close(); } catch (_) {}
      if (!this._destroyed) this._scheduleReconnect();
    };
  }

  disconnect() {
    this._destroyed = true;
    document.removeEventListener('visibilitychange', this._onVisibility);
    this._cancelTimers();
    this._stopHeartbeat();
    const ws = this._ws;
    this._ws = null;
    if (ws) try { ws.close(); } catch (_) {}
  }

  // ── Private ───────────────────────────────────────────────────────────────

  _isDown() {
    return !this._ws ||
      this._ws.readyState === WebSocket.CLOSED ||
      this._ws.readyState === WebSocket.CLOSING;
  }

  /** Exponential backoff: 1 2 4 8 16 30 30 30 … seconds */
  _backoffMs() {
    const base = Math.min(30, Math.pow(2, this._attempt)) * 1000;
    // ±10% jitter to avoid thundering herd
    return base + Math.random() * base * 0.1;
  }

  _scheduleReconnect(overrideMs) {
    if (this._destroyed) return;
    if (this._reconnectTimer) return;   // already scheduled
    const delay = overrideMs !== undefined ? overrideMs : this._backoffMs();
    this._attempt++;
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      if (!this._destroyed) this.connect();
    }, delay);
  }

  _cancelTimers() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }

  /** Send ping every 20s; if no pong within 8s — force reconnect */
  _startHeartbeat() {
    this._stopHeartbeat();
    this._pingTimer = setInterval(() => {
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;
      try { this._ws.send('ping'); } catch (_) {}
      // Expect pong within 8 seconds
      this._pongTimer = setTimeout(() => {
        console.warn('[WS] Pong timeout — forcing reconnect');
        try { this._ws.close(1001, 'pong timeout'); } catch (_) {}
      }, 8000);
    }, 20000);
  }

  _stopHeartbeat() {
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null; }
    this._clearPongTimeout();
  }

  _clearPongTimeout() {
    if (this._pongTimer) { clearTimeout(this._pongTimer); this._pongTimer = null; }
  }
}
