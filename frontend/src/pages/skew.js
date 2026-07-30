/**
 * Страница 6: SKEW ANALYTICS
 * Отображает перекос волатильности 25D Risk Reversal Skew и связанные метрики.
 */
import { renderSkew, destroySkew } from '../widgets/skew.js';

export class SkewPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.lastState = null;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="skew-page-layout panel">
        <div class="panel-header">
          <span class="panel-icon">📐</span> 25D RISK REVERSAL SKEW (HISTORY)
        </div>
        <div class="panel-body relative" style="min-height: 400px; display: flex; flex-direction: column;">
          
          <!-- Легенда / Метрики skew в шапке страницы -->
          <div class="skew-metrics-header" id="skew-metrics-header">
            <div class="skew-stat">
              <span class="skew-stat-label">Current 25D Skew:</span>
              <span class="skew-stat-val text-green" id="skew-stat-current">—</span>
            </div>
            <div class="skew-stat">
              <span class="skew-stat-label">Risk Premium:</span>
              <span class="skew-stat-val text-cyan" id="skew-stat-premium">Analyzing...</span>
            </div>
            <div class="skew-stat">
              <span class="skew-stat-label">Sentiment Direction:</span>
              <span class="skew-stat-val text-bright" id="skew-stat-sentiment">—</span>
            </div>
          </div>

          <!-- Контейнер для Canvas -->
          <div class="chart-container" style="flex: 1; position: relative; min-height: 300px;">
            <canvas id="page-skew-canvas"></canvas>
          </div>

          <div class="skew-disclaimer">
            * 25D Risk Reversal Skew измеряет разницу между имплицированной волатильностью 25-дельтовых опционов Call и Put (Call IV - Put IV). Положительный Skew (>0) означает, что рынок готов платить больше за восходящие опционы (Bullish Premium), отрицательный (<0) — за нисходящие (Bearish Premium / Fear).
          </div>
        </div>
      </div>
    `;
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;
    this.lastState = state;

    const skewData = state.skew;

    // 1. Отрендерим график skew
    const canvas = document.getElementById('page-skew-canvas');
    if (canvas && skewData) {
      renderSkew('page-skew-canvas', skewData);
    }

    // 2. Обновим текстовые метрики в шапке
    const currentEl = document.getElementById('skew-stat-current');
    const premiumEl = document.getElementById('skew-stat-premium');
    const sentimentEl = document.getElementById('skew-stat-sentiment');

    if (skewData) {
      const curSkew = skewData.skew || 0;

      if (currentEl) {
        currentEl.textContent = `${curSkew >= 0 ? '+' : ''}${curSkew.toFixed(2)}%`;
        currentEl.className = 'skew-stat-val ' + (
          curSkew > 1 ? 'text-green' : curSkew < -1 ? 'text-red' : 'text-cyan'
        );
      }

      if (sentimentEl) {
        if (curSkew > 1.5) {
          sentimentEl.textContent = 'BULLISH EXPECTATIONS';
          sentimentEl.className = 'skew-stat-val text-green';
        } else if (curSkew < -1.5) {
          sentimentEl.textContent = 'BEARISH / HEDGING SPURT';
          sentimentEl.className = 'skew-stat-val text-red';
        } else {
          sentimentEl.textContent = 'NEUTRAL STRUCTURE';
          sentimentEl.className = 'skew-stat-val text-cyan';
        }
      }

      if (premiumEl) {
        const callIv = skewData.call_25d_iv || 0;
        const putIv = skewData.put_25d_iv || 0;
        premiumEl.textContent = `Call IV: ${(callIv * 100).toFixed(1)}% | Put IV: ${(putIv * 100).toFixed(1)}%`;
      }
    }
  }

  onActivate() {
    if (this.lastState) {
      this.update(this.lastState);
    }
  }

  destroy() {
    destroySkew();
    this.isInitialized = false;
  }
}
