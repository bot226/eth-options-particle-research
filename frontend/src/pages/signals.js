/**
 * Страница 7: SIGNALS & ALERTS PAGE
 * Полноэкранный лог торговых сигналов, алертов и скоринга позиционирования с фильтрацией.
 */
export class SignalsPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.currentFilter = 'all'; // 'all', 'bullish', 'bearish', 'neutral'
    this.signalsData = [];
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="signals-page-layout">
        <!-- Сводная карточка сигналов (Header Summary) -->
        <div class="signals-summary-panel panel">
          <div class="panel-header">
            <span class="panel-icon">🔔</span> TACTICAL SYSTEM OVERVIEW
          </div>
          <div class="panel-body" style="display: flex; gap: 20px; align-items: center; justify-content: space-between;">
            <div class="signals-stats">
              <div class="stat-box">
                <span class="stat-count text-bright" id="signals-count-total">0</span>
                <span class="stat-lbl">Active Signals</span>
              </div>
              <div class="stat-box">
                <span class="stat-count text-green" id="signals-count-bull">0</span>
                <span class="stat-lbl">Bullish</span>
              </div>
              <div class="stat-box">
                <span class="stat-count text-red" id="signals-count-bear">0</span>
                <span class="stat-lbl">Bearish</span>
              </div>
              <div class="stat-box">
                <span class="stat-count text-cyan" id="signals-count-neutral">0</span>
                <span class="stat-lbl">Neutral</span>
              </div>
            </div>

            <!-- Фильтры -->
            <div class="signals-filters">
              <span class="filter-label">Filter:</span>
              <div class="filter-buttons">
                <button class="filter-btn active" data-filter="all">ALL</button>
                <button class="filter-btn" data-filter="bullish">BULLISH</button>
                <button class="filter-btn" data-filter="bearish">BEARISH</button>
                <button class="filter-btn" data-filter="neutral">NEUTRAL</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Основная панель со списком сигналов -->
        <div class="signals-list-panel panel">
          <div class="panel-header">
            <span class="panel-icon">📋</span> DETAILED SIGNAL METRICS LOG
          </div>
          <div class="panel-body" id="page-signals-list-container" style="min-height: 400px;">
            <div class="loading-overlay"><div class="loading-spinner"></div></div>
          </div>
        </div>
      </div>
    `;

    // Добавляем обработчики для кнопок фильтрации
    this.container.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.container.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        this.currentFilter = e.target.getAttribute('data-filter');
        this.renderFilteredList();
      });
    });

    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;

    this.signalsData = state.signals || [];

    // 1. Обновляем статистику по сигналам
    const totalCount = this.signalsData.length;
    const bullCount = this.signalsData.filter(s => s.bias === 'bullish').length;
    const bearCount = this.signalsData.filter(s => s.bias === 'bearish').length;
    const neutralCount = this.signalsData.filter(s => s.bias === 'neutral').length;

    const totalEl = document.getElementById('signals-count-total');
    const bullEl = document.getElementById('signals-count-bull');
    const bearEl = document.getElementById('signals-count-bear');
    const neutEl = document.getElementById('signals-count-neutral');

    if (totalEl) totalEl.textContent = totalCount;
    if (bullEl) bullEl.textContent = bullCount;
    if (bearEl) bearEl.textContent = bearCount;
    if (neutEl) neutEl.textContent = neutralCount;

    // 2. Отрендерим отфильтрованный список
    this.renderFilteredList();
  }

  renderFilteredList() {
    const listContainer = document.getElementById('page-signals-list-container');
    if (!listContainer) return;

    if (!this.signalsData || !this.signalsData.length) {
      listContainer.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
      return;
    }

    // Фильтруем сигналы
    const filtered = this.signalsData.filter(s => {
      if (this.currentFilter === 'all') return true;
      return s.bias === this.currentFilter;
    });

    if (filtered.length === 0) {
      listContainer.innerHTML = `<div class="no-signals-message">No active ${this.currentFilter} signals detected.</div>`;
      return;
    }

    // Рендерим карточки сигналов
    let html = '<div class="signals-grid-container">';
    for (const s of filtered) {
      const scoreColor = s.score >= 8 ? 'text-green' : s.score >= 5 ? 'text-cyan' : 'text-dim';
      html += `
        <div class="signal-card-large ${s.bias}">
          <div class="sig-card-header">
            <div class="sig-card-title-wrap">
              <span class="sig-card-icon">${s.icon || '🎯'}</span>
              <span class="sig-card-name">${s.name}</span>
            </div>
            <div class="sig-card-score">
              <span class="score-label">SIGNAL SCORE:</span>
              <span class="score-val ${scoreColor}">${s.score != null ? s.score.toFixed(1) + '/10' : '—'}</span>
            </div>
          </div>
          <div class="sig-card-body">
            <p class="sig-card-desc">${s.description}</p>
          </div>
          <div class="sig-card-footer">
            <span class="sig-card-badge ${s.bias}">${s.bias.toUpperCase()}</span>
            <span class="sig-card-meta">SYSTEM TRIGGER · AUTOMATED</span>
          </div>
        </div>
      `;
    }
    html += '</div>';

    listContainer.innerHTML = html;
  }
}
