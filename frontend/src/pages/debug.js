/**
 * Страница 8: DEBUG / RAW DATA PAGE
 * Панель разработчика: логи расчетов, WebSocket-подключение и просмотр сырых JSON данных бэкенда.
 */
export class DebugPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="debug-page-layout">
        <!-- Левая секция: Метрики и системный статус -->
        <div class="debug-sidebar-panel panel">
          <div class="panel-header">
            <span class="panel-icon">⚙️</span> SYSTEM TELEMETRY & LOGS
          </div>
          <div class="panel-body">
            
            <!-- Connection Status -->
            <div class="telemetry-card">
              <div class="tel-row">
                <span class="tel-label">WebSocket Status</span>
                <span class="tel-val status-badge" id="debug-ws-status">Connecting...</span>
              </div>
              <div class="tel-row">
                <span class="tel-label">Last Ping/Update</span>
                <span class="tel-val text-bright" id="debug-last-update">—</span>
              </div>
              <div class="tel-row">
                <span class="tel-label">Expiry Count</span>
                <span class="tel-val text-cyan" id="debug-expiry-count">0</span>
              </div>
              <div class="tel-row">
                <span class="tel-label">Spot Price Value</span>
                <span class="tel-val text-bright" id="debug-spot-val">—</span>
              </div>
            </div>

            <!-- Calculation Log Info -->
            <div class="debug-log-section">
              <h3 class="debug-section-title">CALCULATION & ENGINE LOGS</h3>
              <div class="debug-logs-console" id="debug-logs-console">
                [00:00:01] System Initializing...
                [00:00:02] Connecting to Bybit WebSocket Stream...
                [00:00:03] Connected to /ws/stream. Awaiting calculations trigger...
              </div>
            </div>

          </div>
        </div>

        <!-- Правая секция: Сырые JSON-данные -->
        <div class="debug-raw-panel panel">
          <div class="panel-header" style="display: flex; justify-content: space-between; align-items: center;">
            <span><span class="panel-icon">💻</span> RAW ENDPOINT SNAPSHOT JSON</span>
            <button class="copy-json-btn" id="debug-copy-btn">COPY JSON</button>
          </div>
          <div class="panel-body relative" style="padding: 0; overflow: hidden; height: 100%;">
            <pre class="json-viewer" id="debug-json-viewer">Loading raw JSON snapshot from API...</pre>
          </div>
        </div>
      </div>
    `;

    // Кнопка копирования JSON
    const copyBtn = this.container.querySelector('#debug-copy-btn');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        const text = this.container.querySelector('#debug-json-viewer').textContent;
        navigator.clipboard.writeText(text).then(() => {
          copyBtn.textContent = 'COPIED!';
          setTimeout(() => {
            copyBtn.textContent = 'COPY JSON';
          }, 2000);
        });
      });
    }

    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;

    // 1. Обновляем статус WebSocket
    const wsStatusEl = document.getElementById('debug-ws-status');
    if (wsStatusEl) {
      const status = state.status;
      wsStatusEl.textContent = status.toUpperCase();
      wsStatusEl.className = 'status-badge ' + (
        status === 'connected' ? 'status-ok' : status === 'error' ? 'status-error' : 'status-warn'
      );
    }

    // 2. Обновляем таймстампы и другие показатели
    const lastUpdateEl = document.getElementById('debug-last-update');
    if (lastUpdateEl && state.last_update) {
      const dt = new Date(state.last_update * 1000);
      lastUpdateEl.textContent = dt.toLocaleTimeString('ru-RU') + ' · ' + dt.toLocaleDateString('ru-RU');
    }

    const expiryCountEl = document.getElementById('debug-expiry-count');
    if (expiryCountEl && state.expiries) {
      expiryCountEl.textContent = state.expiries.length;
    }

    const spotValEl = document.getElementById('debug-spot-val');
    if (spotValEl && state.spot) {
      spotValEl.textContent = '$' + state.spot.toLocaleString('en-US');
    }

    // 3. Выводим сырой JSON snapshot
    const viewer = document.getElementById('debug-json-viewer');
    if (viewer && state.raw_data) {
      // Исключаем огромные klines для читаемости и производительности рендеринга
      const cleanData = JSON.parse(JSON.stringify(state.raw_data));
      if (cleanData.data && cleanData.data.klines) {
        cleanData.data.klines = `[Truncated: ${cleanData.data.klines.length} items for UI speed]`;
      }
      viewer.textContent = JSON.stringify(cleanData, null, 2);
    }

    // Добавляем строчки логов
    const consoleEl = document.getElementById('debug-logs-console');
    if (consoleEl && state.last_update) {
      const timeStr = new Date(state.last_update * 1000).toLocaleTimeString('ru-RU');
      const lines = consoleEl.innerHTML.split('\n').map(l => l.trim()).filter(Boolean);
      const newLine = `[${timeStr}] API snapshot updated. Status: ${state.status}. Spot: $${state.spot.toLocaleString()}`;
      
      // Добавляем только если это уникальное событие времени
      if (lines.length === 0 || !lines[lines.length - 1].includes(timeStr)) {
        lines.push(newLine);
        if (lines.length > 25) lines.shift(); // Ограничиваем последние 25 строчек
        consoleEl.innerHTML = lines.join('\n');
        // Скроллим консоль вниз
        consoleEl.scrollTop = consoleEl.scrollHeight;
      }
    }
  }
}
