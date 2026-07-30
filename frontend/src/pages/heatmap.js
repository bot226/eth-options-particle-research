/**
 * Страница 2: OPTIONS HEATMAP & POSITIONING
 * Отображает тепловую карту позиций и распределение OI по экспирациям.
 */
import { renderHeatmap } from '../widgets/heatmap.js';
import { renderOiTable } from '../widgets/oi_table.js';

export class HeatmapPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="heatmap-page-layout">
        <!-- Левая секция: Тепловая карта (Heatmap) -->
        <div class="heatmap-section panel">
          <div class="panel-header">
            <span class="panel-icon">🗺️</span> OPTIONS POSITIONING HEATMAP (NEAR EXPIRY)
          </div>
          <div class="panel-body" id="page-heatmap-body">
            <div class="loading-overlay"><div class="loading-spinner"></div></div>
          </div>
        </div>

        <!-- Правая секция: Таблица OI по экспирациям -->
        <div class="oi-section panel">
          <div class="panel-header">
            <span class="panel-icon">📋</span> OPEN INTEREST BY EXPIRY DATES
          </div>
          <div class="panel-body" id="page-oi-body">
            <div class="loading-overlay"><div class="loading-spinner"></div></div>
          </div>
        </div>
      </div>
    `;
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;

    const heatmapData = state.heatmap;
    const oiData = state.oi_by_expiry;

    // 1. Отрендерим тепловую карту
    const heatmapBody = document.getElementById('page-heatmap-body');
    if (heatmapBody && heatmapData) {
      renderHeatmap(heatmapBody, heatmapData);
    }

    // 2. Отрендерим таблицу OI
    const oiBody = document.getElementById('page-oi-body');
    if (oiBody && oiData) {
      renderOiTable(oiBody, oiData);
    }
  }
}
