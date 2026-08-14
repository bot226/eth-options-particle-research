/**
 * Страница 5: GAMMA EXPOSURE (GEX) — MARKET MAKER PRESSURE MAP
 * Полный GEX-анализатор: гистограмма, кумулятивная кривая, панель давления и ценовой график с уровнями.
 */
import { renderGex, destroyGex } from '../widgets/gex.js';
import { renderPriceChart, destroyPriceChart } from '../widgets/price_chart.js';

export class GexPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.lastState = null;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="gex-page-layout">
        <!-- Верхняя секция: Графики GEX (гистограмма и кумулятивная кривая) -->
        <div class="gex-charts-panel panel">
          <div class="panel-header">
            <span class="panel-icon">🧲</span> GAMMA EXPOSURE (GEX) — NET & CUMULATIVE STRUCTURE
          </div>
          <div class="panel-body panel-body-row" id="page-gex-body" style="min-height: 380px;">
            <!-- Гистограмма GEX -->
            <div id="page-gex-histogram-wrap" style="flex: 1.2; position: relative; min-height: 340px;">
              <canvas id="page-gex-histogram-canvas"></canvas>
            </div>
            
            <!-- Кумулятивная кривая GEX -->
            <div id="page-gex-curve-wrap" style="flex: 1.2; position: relative; min-height: 340px;">
              <canvas id="page-gex-curve-canvas"></canvas>
            </div>

            <!-- Панель давления маркет-мейкеров -->
            <div id="gex-pressure-panel" class="gex-sidebar" style="flex: 0.6; min-width: 220px;">
              <div class="loading-overlay"><div class="loading-spinner"></div></div>
            </div>
          </div>
        </div>

        <!-- Нижняя секция: График цены ETH с уровнями открытого интереса (OI / GEX Zones) -->
        <div class="gex-price-panel panel">
          <div class="panel-header">
            <span class="panel-icon">💹</span> ETH/USDT PRICE CHART & MAJOR OPEN INTEREST LEVELS
          </div>
          <div class="panel-body" style="height: 300px; position: relative;">
            <div id="page-price-chart-wrap" style="width: 100%; height: 100%;">
              <div class="loading-overlay"><div class="loading-spinner"></div></div>
            </div>
          </div>
        </div>
      </div>
    `;
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;
    this.lastState = state;

    const gexData = state.gex;
    const spot = state.spot;
    const klines = state.klines;
    const oiLevels = state.oi_levels;

    // 1. Рендерим GEX Histogram & GEX Curve + MM Pressure Panel
    if (gexData) {
      renderGex(
        'page-gex-histogram-canvas',
        'page-gex-curve-canvas',
        gexData,
        spot
      );
    }

    // 2. Рендерим график цены ETH с уровнями OI
    const priceWrap = document.getElementById('page-price-chart-wrap');
    if (priceWrap && klines && klines.length) {
      // Очищаем лоадер перед отрисовкой в первый раз
      const loader = priceWrap.querySelector('.loading-overlay');
      if (loader) {
        priceWrap.innerHTML = '';
      }
      renderPriceChart('page-price-chart-wrap', klines, oiLevels, spot);
    }
  }

  onActivate() {
    // При переключении на вкладку вызываем перерисовку/ресайз графиков
    if (this.lastState) {
      this.update(this.lastState);
    }
  }

  destroy() {
    destroyGex();
    destroyPriceChart();
    this.isInitialized = false;
  }
}
