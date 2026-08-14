/**
 * ETH Options Dashboard — Main Entry Point (Modular SPA Version).
 * Инициализирует глобальный store, роутер и динамически обновляет страницы.
 */
import './style.css';
import { store } from './store.js';
import { router } from './router.js';

// Импорт страниц
import { DashboardPage } from './pages/dashboard.js';
import { HeatmapPage } from './pages/heatmap.js';
import { ProbabilityPage } from './pages/probability.js';
import { TermStructurePage } from './pages/term_structure.js';
import { GexPage } from './pages/gex.js';
import { SkewPage } from './pages/skew.js';
import { SignalsPage } from './pages/signals.js';
import { DebugPage } from './pages/debug.js';
import { HelpPage } from './pages/help.js';
import { ResearchPage } from './pages/research.js';
import { ManualTradingPage } from './pages/manual_trading.js';

// ------------------------------------------------------------------ //
//  DOM references (Глобальная шапка)
// ------------------------------------------------------------------ //
const spotEl = document.getElementById('spot-price');
const changeEl = document.getElementById('spot-change');
const statusDot = document.querySelector('.status-dot');
const statusText = document.getElementById('status-text');
const expirySelect = document.getElementById('expiry-select');
const lastUpdateEl = document.getElementById('last-update');

// ------------------------------------------------------------------ //
//  Инициализация страниц
// ------------------------------------------------------------------ //
const pages = {
  dashboard: new DashboardPage(),
  heatmap: new HeatmapPage(),
  probability: new ProbabilityPage(),
  term_structure: new TermStructurePage(),
  gex: new GexPage(),
  skew: new SkewPage(),
  signals: new SignalsPage(),
  research: new ResearchPage(),
  manual_trading: new ManualTradingPage(),
  debug: new DebugPage(),
  help: new HelpPage()
};

function initPages() {
  for (const [name, instance] of Object.entries(pages)) {
    const container = document.getElementById(`page-${name}`);
    if (container) {
      instance.init(container);
      router.registerPage(name, instance);
    }
  }
}

// ------------------------------------------------------------------ //
//  Обновление глобальных элементов шапки
// ------------------------------------------------------------------ //
function updateGlobalHeader(state) {
  // 1. Спот цена
  if (state.spot > 0) {
    spotEl.textContent = '$' + state.spot.toLocaleString('en-US', { maximumFractionDigits: 0 });
    const pct = state.spot_24h_change * 100;
    changeEl.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    changeEl.className = 'spot-change ' + (pct >= 0 ? 'positive' : 'negative');
  }

  // 2. Статус соединения (WebSocket)
  if (state.status === 'connected') {
    statusDot.className = 'status-dot connected';
    statusText.textContent = 'Live';
  } else if (state.status === 'error') {
    statusDot.className = 'status-dot';
    statusText.textContent = 'Error';
  } else {
    statusDot.className = 'status-dot';
    statusText.textContent = 'Reconnecting...';
  }

  // 3. Селектор экспираций
  if (state.expiries && state.expiries.length) {
    const current = expirySelect.value;
    if (expirySelect.options.length !== state.expiries.length) {
      expirySelect.innerHTML = '';
      state.expiries.forEach(exp => {
        const opt = document.createElement('option');
        opt.value = exp;
        opt.textContent = exp;
        expirySelect.appendChild(opt);
      });
      if (current && state.expiries.includes(current)) {
        expirySelect.value = current;
      }
    }
  }

  // 4. Таймстамп обновления
  if (state.last_update) {
    const dt = new Date(state.last_update * 1000);
    lastUpdateEl.textContent = dt.toLocaleTimeString('ru-RU');
  }
}

// ------------------------------------------------------------------ //
//  Точка входа
// ------------------------------------------------------------------ //
function init() {
  // 1. Создаем HTML структуры страниц
  initPages();

  // 2. Инициализируем роутер
  router.init();

  // 3. Подписываемся на центральный store данных
  store.subscribe((state) => {
    // Обновляем шапку
    updateGlobalHeader(state);

    // Обновляем каждую страницу
    for (const page of Object.values(pages)) {
      if (typeof page.update === 'function') {
        page.update(state);
      }
    }
  });

  // 4. Запускаем store (поллинг и WebSocket)
  store.init();
}

// Запуск приложения при загрузке DOM
document.addEventListener('DOMContentLoaded', init);
