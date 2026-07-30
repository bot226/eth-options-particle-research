/**
 * Страница 4: IV TERM STRUCTURE
 * Отображает временную структуру имплицированной волатильности (ATM IV, Call IV, Put IV).
 */
import { renderTermStructure, destroyTermStructure } from '../widgets/term_structure.js';

export class TermStructurePage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.lastState = null;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="term-structure-page-layout panel">
        <div class="panel-header">
          <span class="panel-icon">📈</span> IMPLIED VOLATILITY TERM STRUCTURE (ATM & Skewed IV vs DTE)
        </div>
        <div class="panel-body relative" style="min-height: 400px; display: flex; flex-direction: column;">
          
          <!-- Легенда / Метрики структуры в шапке страницы -->
          <div class="term-metrics-header" id="term-metrics-header">
            <div class="term-stat">
              <span class="term-stat-label">Volatility Regime:</span>
              <span class="term-stat-val text-cyan" id="term-stat-regime">Analyzing...</span>
            </div>
            <div class="term-stat">
              <span class="term-stat-label">Event Pricing Risk:</span>
              <span class="term-stat-val text-cyan" id="term-stat-event">Analyzing...</span>
            </div>
            <div class="term-stat">
              <span class="term-stat-label">Term Skew (Front/Back):</span>
              <span class="term-stat-val text-bright" id="term-stat-skew">—</span>
            </div>
          </div>

          <!-- Контейнер для Canvas -->
          <div class="chart-container" style="flex: 1; position: relative; min-height: 300px;">
            <canvas id="page-term-canvas"></canvas>
          </div>

          <div class="term-disclaimer">
            * Временная структура (Term Structure) отражает ожидания рынка относительно волатильности на разные сроки (Days to Expiration - DTE). Режим инверсии (когда ближняя волатильность выше дальней) указывает на рыночную панику и экстремальный спрос на страховку (хеджирование).
          </div>
        </div>
      </div>
    `;
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;
    this.lastState = state;

    const termData = state.term_structure;

    // 1. Отрендерим график временной структуры
    const canvas = document.getElementById('page-term-canvas');
    if (canvas && termData) {
      renderTermStructure('page-term-canvas', termData);
    }

    // 2. Обновим текстовые метрики в шапке
    const regimeEl = document.getElementById('term-stat-regime');
    const eventEl = document.getElementById('term-stat-event');
    const skewEl = document.getElementById('term-stat-skew');

    if (termData && termData.metrics) {
      const m = termData.metrics;

      if (regimeEl) {
        const reg = m.regime || 'Normal';
        regimeEl.textContent = reg.toUpperCase();
        regimeEl.className = 'term-stat-val ' + (
          reg === 'PANIC' ? 'text-red' : reg === 'EXPANSION' ? 'text-orange' : 'text-green'
        );
      }

      if (eventEl) {
        const risk = m.event_risk || 'Low';
        eventEl.textContent = risk.toUpperCase();
        eventEl.className = 'term-stat-val ' + (
          risk === 'High' || risk === 'Elevated' ? 'text-orange' : 'text-green'
        );
      }

      // Рассчитаем перекос Front/Back волатильности, если есть данные
      if (skewEl && termData.current && termData.current.length >= 2) {
        const cur = termData.current;
        const front = cur[0].atm_iv * 100;
        const back = cur[cur.length - 1].atm_iv * 100;
        const diff = front - back;
        skewEl.textContent = `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}% (${cur[0].dte}D vs ${cur[cur.length - 1].dte}D)`;
        skewEl.className = 'term-stat-val ' + (diff > 5 ? 'text-red' : diff < -5 ? 'text-green' : 'text-bright');
      }
    }
  }

  onActivate() {
    if (this.lastState) {
      this.update(this.lastState);
    }
  }

  destroy() {
    destroyTermStructure();
    this.isInitialized = false;
  }
}
