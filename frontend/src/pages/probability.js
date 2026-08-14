/**
 * Страница 3: PROBABILITY CURVE
 * Показывает кривую распределения вероятностей (PDF), хвостовые риски и смещения.
 */
import { renderProbability, destroyProbability } from '../widgets/probability.js';

export class ProbabilityPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.lastState = null;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="probability-page-layout panel">
        <div class="panel-header">
          <span class="panel-icon">📊</span> IMPLIED PROBABILITY DISTRIBUTIONS (PDF)
        </div>
        <div class="panel-body relative" style="min-height: 400px; display: flex; flex-direction: column;">
          
          <!-- Легенда / Метрики хвостового риска в шапке страницы -->
          <div class="probability-metrics-header" id="prob-metrics-header">
            <div class="prob-stat">
              <span class="prob-stat-label">Tail Risk Status:</span>
              <span class="prob-stat-val text-cyan" id="prob-stat-tail">Analyzing...</span>
            </div>
            <div class="prob-stat">
              <span class="prob-stat-label">Market Bias Shift:</span>
              <span class="prob-stat-val text-cyan" id="prob-stat-bias">Analyzing...</span>
            </div>
            <div class="prob-stat">
              <span class="prob-stat-label">Expected Price Range (1SD):</span>
              <span class="prob-stat-val text-bright" id="prob-stat-range">—</span>
            </div>
          </div>

          <!-- Контейнер для Canvas -->
          <div class="chart-container" style="flex: 1; position: relative; min-height: 300px;">
            <canvas id="page-probability-canvas"></canvas>
          </div>

          <div class="prob-disclaimer">
            * График PDF (Probability Density Function) показывает распределение вероятностей будущей цены ETH на основе опционов с ближайшим сроком экспирации. Разрыв между текущей ценой и пиком распределения сигнализирует о перекосе рыночных ожиданий (Skewed Risk).
          </div>
        </div>
      </div>
    `;
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;
    this.lastState = state;

    const probData = state.probability;
    const spot = state.spot;

    // 1. Отрендерим график PDF
    const canvas = document.getElementById('page-probability-canvas');
    if (canvas && probData) {
      renderProbability('page-probability-canvas', probData, spot);
    }

    // 2. Обновим текстовые метрики в шапке
    const tailEl = document.getElementById('prob-stat-tail');
    const biasEl = document.getElementById('prob-stat-bias');
    const rangeEl = document.getElementById('prob-stat-range');

    if (probData && probData.current && probData.current.metrics) {
      const m = probData.current.metrics;
      
      if (tailEl) {
        const risk = m.tail_risk || 'Normal';
        tailEl.textContent = risk.toUpperCase();
        tailEl.className = 'prob-stat-val ' + (
          risk === 'High' || risk === 'Elevated' ? 'text-red' : 'text-green'
        );
      }

      if (biasEl) {
        const bias = m.bias || 'Neutral';
        biasEl.textContent = bias.toUpperCase();
        biasEl.className = 'prob-stat-val ' + (
          bias === 'Bullish' ? 'text-green' : bias === 'Bearish' ? 'text-red' : 'text-cyan'
        );
      }

      if (rangeEl && m.expected_min && m.expected_max) {
        rangeEl.textContent = `$${Math.round(m.expected_min).toLocaleString()} - $${Math.round(m.expected_max).toLocaleString()}`;
      }
    }
  }

  onActivate() {
    // При переключении вкладки перерисовываем график, чтобы он корректно растянулся под новый размер контейнера
    if (this.lastState) {
      this.update(this.lastState);
    }
  }

  destroy() {
    destroyProbability();
    this.isInitialized = false;
  }
}
