import { fetchManualSetupWatchlist } from '../api/client.js';

/**
 * Phase 2: Institutional Market Operating System Dashboard
 * Read-only layout mapping 6 layers of state.market_state.
 * Bloomberg-style dense rendering.
 */

export class DashboardPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.manualWatchlistRows = [];
    this.manualWatchlistLoading = false;
    this.manualWatchlistLastFetch = 0;
    this.manualWatchlistFilters = {
      statuses: new Set(['ENTRY_CANDIDATE', 'WATCH']),
      biases: new Set(),
    };
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="mos-dashboard">
        <!-- INTELLIGENCE SUMMARY BANNER -->
        <div class="intel-banner" id="mos-intel-banner">
          <div class="intel-banner-header">
            <span class="mos-icon">🧠</span> РЫНОЧНЫЙ АНАЛИЗ
          </div>
          <div class="intel-banner-body" id="mos-intel-body">
            <div class="loading-text">АНАЛИЗ И СИНТЕЗ РЫНОЧНЫХ ДАННЫХ...</div>
          </div>
        </div>

        <!-- TOP ROW: State & Execution -->
        <div class="mos-row">
          <div class="mos-panel flex-2">
            <div class="mos-panel-header">
              <span class="mos-icon">⚙️</span> МОДЕЛЬ СОСТОЯНИЙ
            </div>
            <div class="mos-panel-body" id="mos-state-body">
              <div class="loading-text">СИНХРОНИЗАЦИЯ МОДУЛЕЙ...</div>
            </div>
          </div>
          <div class="mos-panel flex-1">
            <div class="mos-panel-header">
              <span class="mos-icon">⚡</span> ИСПОЛНЕНИЕ (EXECUTION)
            </div>
            <div class="mos-panel-body" id="mos-execution-body"></div>
          </div>
        </div>

        <!-- MID ROW: Gamma, Volatility, Skew -->
        <div class="mos-row">
          <div class="mos-panel flex-1 border-accent-gamma">
            <div class="mos-panel-header">
              <span class="mos-icon">🧲</span> МОДУЛЬ GAMMA
            </div>
            <div class="mos-panel-body" id="mos-gamma-body"></div>
          </div>
          <div class="mos-panel flex-1 border-accent-vol vol-regime-panel">
            <div class="mos-panel-header">
              <span class="mos-icon">📈</span> РЕЖИМ ВОЛАТИЛЬНОСТИ
            </div>
            <div class="mos-panel-body" id="mos-vol-body"></div>
          </div>
          <div class="mos-panel flex-1 border-accent-skew">
            <div class="mos-panel-header">
              <span class="mos-icon">⚖️</span> МОДУЛЬ SKEW
            </div>
            <div class="mos-panel-body" id="mos-skew-body"></div>
          </div>
        </div>

        <!-- BOTTOM ROW: Liquidity, Meta, Scenario -->
        <div class="mos-row">
          <div class="mos-panel flex-1">
            <div class="mos-panel-header">
              <span class="mos-icon">💧</span> ПОТОКИ И ЛИКВИДНОСТЬ
            </div>
            <div class="mos-panel-body" id="mos-liq-body"></div>
          </div>
          <div class="mos-panel flex-1">
            <div class="mos-panel-header">
              <span class="mos-icon">🧠</span> МЕТА-СОСТОЯНИЕ
            </div>
            <div class="mos-panel-body" id="mos-meta-body"></div>
          </div>
          <div class="mos-panel flex-1 border-accent-scenario">
            <div class="mos-panel-header">
              <span class="mos-icon">🎯</span> МОДУЛЬ СЦЕНАРИЕВ
            </div>
            <div class="mos-panel-body" id="mos-scenario-body"></div>
          </div>
        </div>

        <!-- MANUAL TRADING DECISION -->
        <div class="mos-row manual-trading-row">
          <div class="mos-panel flex-1 border-accent-manual">
            <div class="mos-panel-header">
              <span class="mos-icon">MT</span> MANUAL TRADING DECISION
            </div>
            <div class="mos-panel-body manual-decision-body" id="mos-manual-body">
              <div class="loading-text">WAITING FOR MANUAL CONTEXT...</div>
            </div>
          </div>
        </div>

        <!-- MANUAL SETUP WATCHLIST -->
        <div class="mos-row manual-watchlist-row">
          <div class="mos-panel flex-1 border-accent-manual">
            <div class="mos-panel-header">
              <span class="mos-icon">WL</span> MANUAL SETUP WATCHLIST
            </div>
            <div class="mos-panel-body manual-watchlist-body">
              <div class="manual-watchlist-controls" id="manual-watchlist-controls">
                <button type="button" class="manual-filter-btn is-active" data-manual-filter="status" data-manual-value="ENTRY_CANDIDATE">ENTRY_CANDIDATE</button>
                <button type="button" class="manual-filter-btn is-active" data-manual-filter="status" data-manual-value="WATCH">WATCH</button>
                <button type="button" class="manual-filter-btn" data-manual-filter="status" data-manual-value="AVOID">AVOID</button>
                <button type="button" class="manual-filter-btn" data-manual-filter="bias" data-manual-value="LONG">LONG</button>
                <button type="button" class="manual-filter-btn" data-manual-filter="bias" data-manual-value="SHORT">SHORT</button>
              </div>
              <div class="manual-watchlist-table-wrap">
                <table class="manual-watchlist-table">
                  <thead>
                    <tr>
                      <th>time</th>
                      <th>setup_type</th>
                      <th>manual_status</th>
                      <th>manual_bias</th>
                      <th>setup_quality</th>
                      <th>missing</th>
                      <th>price</th>
                      <th>nearest_level</th>
                      <th>current_state</th>
                      <th>execution_timing_state</th>
                      <th>event_type</th>
                      <th>level_result</th>
                      <th>flow</th>
                      <th>confirmation_needed</th>
                      <th>inval_level</th>
                    </tr>
                  </thead>
                  <tbody id="manual-watchlist-body">
                    <tr><td colspan="15" class="manual-watchlist-empty">LOADING...</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
    this.container.addEventListener('click', (event) => {
      const button = event.target.closest('[data-manual-filter]');
      if (!button || !this.container.contains(button)) return;
      this.toggleManualWatchlistFilter(button.dataset.manualFilter, button.dataset.manualValue);
    });
    this.isInitialized = true;
  }

  update(state) {
    if (!this.isInitialized || !this.container) return;
    
    const ms = state.market_state;
    if (!ms) return;

    const n = ms.narrative || {};
    const labels = n.labels || {};

    // 0. INTELLIGENCE SUMMARY BANNER
    const intelBody = document.getElementById('mos-intel-body');
    const intelHeader = document.querySelector('.intel-banner-header');
    
    if (intelHeader && ms.system_confidence && ms.exchange_health) {
      const h = ms.exchange_health;
      const c = ms.system_confidence;
      const modeText = h.mode === 'multi_exchange' ? `GLOBAL AGGREGATION (${h.active_sources}/${h.total_sources})` : `SINGLE EXCHANGE (${h.primary_source})`;
      const confText = `CONFIDENCE: ${c.system_confidence}%`;
      intelHeader.innerHTML = `
        <span><span class="mos-icon">🧠</span> РЫНОЧНЫЙ АНАЛИЗ</span>
        <span style="float:right; font-size: 0.8rem; opacity: 0.8;">[${modeText} | ${confText}]</span>
      `;
    }

    if (intelBody && n.intelligence_summary) {
      const lines = (n.intelligence_summary || []).map(l => `<div class="intel-line">${l}</div>`).join('');
      const qa = n.five_answers || {};
      const integrityColor = qa.data_integrity === 'Дивергенция' ? 'color: var(--color-red)' : 'color: var(--color-green)';
      
      intelBody.innerHTML = `
        <div class="intel-narrative">${lines}</div>
        <div class="intel-answers">
          <div class="intel-qa"><span class="intel-q">КОНТРОЛЬ:</span> <span class="intel-a">${qa.who_controls || '—'}</span></div>
          <div class="intel-qa"><span class="intel-q">ВОЛАТИЛЬНОСТЬ:</span> <span class="intel-a">${qa.volatility_state || '—'}</span></div>
          <div class="intel-qa"><span class="intel-q">ВЫХОД:</span> <span class="intel-a">${qa.can_escape || '—'}</span></div>
          <div class="intel-qa"><span class="intel-q">СТАБИЛЬНОСТЬ:</span> <span class="intel-a">${qa.stability || '—'}</span></div>
          <div class="intel-qa"><span class="intel-q">ДАННЫЕ:</span> <span class="intel-a" style="${integrityColor}">${qa.data_integrity || '—'}</span></div>
        </div>
      `;
    }

    // 1. STATE MACHINE (МОДЕЛЬ СОСТОЯНИЙ)
    const stateBody = document.getElementById('mos-state-body');
    if (stateBody && ms.state_machine) {
      const sm = ms.state_machine;
      const stateColor = sm.current_state === 'PINNING' ? 'text-cyan' : 
                         sm.current_state === 'EXPANSION' ? 'text-green' : 'text-orange';
      
      const stateLabels = {
        'PINNING': 'ПИННИНГ',
        'COMPRESSION': 'КОМПРЕССИЯ',
        'TRANSITION': 'ТРАНЗИТ',
        'BREAKOUT_SETUP': 'ПОДГОТОВКА ПРОРЫВА',
        'HEDGE_CHASE': 'HEDGE CHASE',
        'EXPANSION': 'ЭКСПАНСИЯ',
        'SHORT_SQUEEZE': 'ШОРТ-СКВИЗ',
        'LONG_LIQUIDATION': 'ЛОНГ-ЛИКВИДАЦИЯ',
        'EXHAUSTION': 'ИСТОЩЕНИЕ',
        'REBALANCE': 'РЕБАЛАНСИРОВКА',
        'UNKNOWN': 'НЕИЗВЕСТНО'
      };

      const currentStateLabel = stateLabels[sm.current_state] || sm.current_state;
      const previousStateLabel = stateLabels[sm.previous_state] || (sm.previous_state === 'UNKNOWN' ? 'Начальное состояние' : sm.previous_state);
      const candidateStateLabel = stateLabels[sm.candidate_state] || (sm.candidate_state === 'UNKNOWN' ? 'Оценка...' : sm.candidate_state);
      const transLabel = sm.transition_state === 'STABLE' ? 'Подтвержден' : 'Ожидает подтверждения';

      stateBody.innerHTML = `
        <div class="mos-metric-group">
          <div class="mos-metric"><span class="label">ТЕКУЩИЙ РЕЖИМ:</span> <span class="val highlight ${stateColor}">${currentStateLabel}</span></div>
          <div class="mos-metric"><span class="label">ПРЕДЫДУЩИЙ:</span> <span class="val text-dim">${previousStateLabel}</span></div>
          <div class="mos-metric"><span class="label">КАНДИДАТ:</span> <span class="val text-muted">${candidateStateLabel}</span></div>
        </div>
        <div class="mos-metric-group">
          <div class="mos-metric"><span class="label">ПЕРЕХОД:</span> <span class="val">${transLabel}</span></div>
          <div class="mos-metric"><span class="label">СТАБИЛЬНОСТЬ:</span> <span class="val ${sm.regime_stability > 80 ? 'text-green' : 'text-orange'}">${this.fmtPct(sm.regime_stability)}</span></div>
          <div class="mos-metric"><span class="label">УДЕРЖАНИЕ СЕК:</span> <span class="val">${sm.state_persistence_sec}с</span></div>
        </div>
      `;
    }

    // 2. EXECUTION (ИСПОЛНЕНИЕ)
    const execBody = document.getElementById('mos-execution-body');
    if (execBody && ms.execution) {
      const ex = ms.execution;
      const biasLabel = labels.directional_bias || ex.signals.directional_bias;
      const qualLabel = labels.execution_quality || ex.signals.execution_quality;
      const biasColor = ex.signals.directional_bias === 'BULLISH' ? 'text-green' : 
                        ex.signals.directional_bias === 'BEARISH' ? 'text-red' : 'text-cyan';
      execBody.innerHTML = `
        <div class="mos-metric"><span class="label">СМЕЩЕНИЕ (BIAS):</span> <span class="val highlight ${biasColor}">${biasLabel}</span></div>
        <div class="mos-metric"><span class="label">КАЧЕСТВО:</span> <span class="val">${qualLabel}</span></div>
        <div class="mos-metric"><span class="label">УРОВЕНЬ ОТМЕНЫ:</span> <span class="val">$${this.fmtNum(ex.metrics.invalidation_level)}</span></div>
        <div class="mos-metric"><span class="label">КОЭФФИЦИЕНТ R/R:</span> <span class="val">${ex.metrics.rr_score}</span></div>
      `;
    }

    // 3. GAMMA
    const gammaBody = document.getElementById('mos-gamma-body');
    if (gammaBody && ms.gamma) {
      const g = ms.gamma;
      const netGexColor = g.metrics.net_gex > 0 ? 'text-green' : 'text-red';
      gammaBody.innerHTML = `
        <div class="mos-metric"><span class="label">ЧИСТЫЙ GEX:</span> <span class="val ${netGexColor}">${this.fmtNum(g.metrics.net_gex)} ETH</span></div>
        <div class="mos-metric"><span class="label">GEX ОКОЛО СПОТА:</span> <span class="val">${this.fmtNum(g.metrics.near_spot_gex)} ETH</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">GAMMA FLIP:</span> <span class="val highlight text-orange">$${this.fmtNum(g.metrics.gamma_flip)}</span></div>
        <div class="mos-metric"><span class="label">CALL WALL:</span> <span class="val text-green">$${this.fmtNum(g.metrics.call_wall)}</span></div>
        <div class="mos-metric"><span class="label">PUT WALL:</span> <span class="val text-red">$${this.fmtNum(g.metrics.put_wall)}</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">ЭФФЕКТ ДИЛЕРА:</span> <span class="val">${labels.gamma_regime || g.signals.gamma_regime}</span></div>
        <div class="mos-metric"><span class="label">ПИННИНГ:</span> <span class="val">${labels.pinning_bias || g.signals.pinning_bias}</span></div>
      `;
    }

    // 4. VOLATILITY REGIME (РЕЖИМ ВОЛАТИЛЬНОСТИ)
    const volBody = document.getElementById('mos-vol-body');
    if (volBody && ms.volatility && ms.volatility.metrics) {
      const v = ms.volatility;
      const m = v.metrics || {};
      const s = v.signals || {};
      const regimeColor = this._volRegimeColor(s.iv_regime_color || s.iv_regime || 'NORMAL');
      const eventColor = this._eventRiskColor(s.event_risk || 'LOW');
      const ivVel = m.iv_velocity ?? 0;
      const velSign = ivVel > 0 ? '+' : '';
      const slope = m.term_structure_slope ?? 0;

      const expansionRiskLabels = {
        'HIGH': 'ВЫСОКИЙ',
        'ELEVATED': 'ПОВЫШЕННЫЙ',
        'CRUSHED': 'РЕЗКОЕ СНИЖЕНИЕ',
        'LOW': 'НИЗКИЙ',
        'STABLE': 'СТАБИЛЬНЫЙ'
      };
      const riskLabels = {
        'LOW': 'НИЗКИЙ',
        'MEDIUM': 'СРЕДНИЙ',
        'HIGH': 'ВЫСОКИЙ',
        'EXTREME': 'ЭКСТРЕМАЛЬНЫЙ'
      };
      const ivVelocityLabels = {
        'STABLE': 'СТАБИЛЬНАЯ',
        'EXPANDING RAPIDLY': 'БЫСТРЫЙ РОСТ',
        'EXPANDING': 'РОСТ',
        'COMPRESSING RAPIDLY': 'БЫСТРОЕ СЖАТИЕ',
        'COMPRESSING': 'СЖАТИЕ'
      };

      const ivRegimeLabel = labels.iv_regime || s.iv_regime || 'НОРМАЛЬНЫЙ';
      const termStructureLabel = labels.term_structure || s.term_structure_label || 'ФЛЭТ';
      const expRiskLabel = expansionRiskLabels[s.volatility_expansion] || s.volatility_expansion || 'НИЗКИЙ';
      const evtRiskLabel = riskLabels[s.event_risk] || s.event_risk || 'НИЗКИЙ';
      const ivVelLabel = ivVelocityLabels[m.iv_velocity_label] || m.iv_velocity_label || 'СТАБИЛЬНАЯ';

      volBody.innerHTML = `
        <div class="vol-regime-state ${regimeColor}">${ivRegimeLabel}</div>
        <div class="vol-regime-context">${s.regime_context || 'Режим стабилен'}</div>
        <div class="vol-grid">
          <div class="vol-cell">
            <span class="vol-cell-label">ATM IV</span>
            <span class="vol-cell-value text-bright">${this.fmtPct(m.atm_iv)}</span>
          </div>
          <div class="vol-cell">
            <span class="vol-cell-label">ВРЕМЕННАЯ СТРУКТУРА</span>
            <span class="vol-cell-value">${termStructureLabel}</span>
            <span class="vol-cell-sub">наклон: ${slope >= 0 ? '+' : ''}${slope.toFixed(4)}</span>
          </div>
          <div class="vol-cell">
            <span class="vol-cell-label">СКОРОСТЬ IV</span>
            <span class="vol-cell-value ${ivVel > 0.5 ? 'text-orange' : ivVel < -0.5 ? 'text-cyan' : ''}">${velSign}${ivVel.toFixed(2)}/ч</span>
            <span class="vol-cell-sub">${ivVelLabel}</span>
          </div>
          <div class="vol-cell">
            <span class="vol-cell-label">РИСК РАСШИРЕНИЯ</span>
            <span class="vol-cell-value ${s.volatility_expansion === 'HIGH' ? 'text-red' : s.volatility_expansion === 'ELEVATED' ? 'text-orange' : 'text-dim'}">${expRiskLabel}</span>
          </div>
          <div class="vol-cell">
            <span class="vol-cell-label">РИСК СОБЫТИЯ</span>
            <span class="vol-cell-value ${eventColor}">${evtRiskLabel}</span>
          </div>
        </div>
      `;
    }

    // 5. SKEW
    const skewBody = document.getElementById('mos-skew-body');
    if (skewBody && ms.skew) {
      const s = ms.skew;
      skewBody.innerHTML = `
        <div class="mos-metric"><span class="label">25D SKEW:</span> <span class="val">${(s.metrics.skew_25d ?? 0).toFixed(2)}</span></div>
        <div class="mos-metric"><span class="label">CALL 25D IV:</span> <span class="val">${this.fmtPct(s.metrics.call_25d_iv)}</span></div>
        <div class="mos-metric"><span class="label">PUT 25D IV:</span> <span class="val">${this.fmtPct(s.metrics.put_25d_iv)}</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">СОСТОЯНИЕ SKEW:</span> <span class="val highlight">${labels.skew_regime || s.signals.skew_regime}</span></div>
        <div class="mos-metric"><span class="label">ДАВЛЕНИЕ ПОТОКОВ:</span> <span class="val">${labels.skew_dominance || s.signals.skew_dominance}</span></div>
      `;
    }

    // 6. LIQUIDITY & FLOW
    const liqBody = document.getElementById('mos-liq-body');
    if (liqBody && ms.liquidity && ms.flow) {
      const l = ms.liquidity;
      const f = ms.flow;
      const flowColor = f.signals.flow_bias === 'BULLISH' ? 'text-green' : f.signals.flow_bias === 'BEARISH' ? 'text-red' : 'text-cyan';
      
      const liquidityStateLabels = {
        'THIN': 'НИЗКАЯ (ТОНКИЙ РЫНОК)',
        'ADEQUATE': 'ДОСТАТОЧНАЯ',
        'NORMAL': 'НОРМАЛЬНАЯ'
      };
      const liqLabel = liquidityStateLabels[l.signals.liquidity_state] || l.signals.liquidity_state;

      liqBody.innerHTML = `
        <div class="mos-metric"><span class="label">ОБЩИЙ OI:</span> <span class="val text-bright">${this.fmtNum(l.metrics.total_oi)} ETH</span></div>
        <div class="mos-metric"><span class="label">P/C RATIO:</span> <span class="val">${this.fmtNum(l.metrics.put_call_ratio)}</span></div>
        <div class="mos-metric"><span class="label">ЛИКВИДНОСТЬ:</span> <span class="val">${liqLabel}</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">ДАВЛЕНИЕ FLOW:</span> <span class="val">${(f.metrics.flow_pressure ?? 50).toFixed(1)}</span></div>
        <div class="mos-metric"><span class="label">СОСТОЯНИЕ ПОТОКОВ:</span> <span class="val highlight ${flowColor}">${labels.flow_bias || f.signals.flow_bias}</span></div>
      `;
    }

    // 7. META
    const metaBody = document.getElementById('mos-meta-body');
    if (metaBody && ms.meta) {
      const m = ms.meta;
      metaBody.innerHTML = `
        <div class="mos-metric"><span class="label">ДОМИНИРОВАНИЕ ДИЛЕРОВ:</span> <span class="val text-orange">${this.fmtPct(m.metrics.dealer_dominance_score)}</span></div>
        <div class="mos-metric"><span class="label">ХРУПКОСТЬ РЫНКА:</span> <span class="val">${labels.market_fragility || m.signals.market_fragility}</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">КОНТРОЛЬ НАД РЫНКОМ:</span> <span class="val highlight">${labels.market_control || m.signals.market_control}</span></div>
        <div class="mos-metric"><span class="label">ТРЕНД:</span> <span class="val">${labels.trend_maturity || m.signals.trend_maturity}</span></div>
      `;
    }

    // 8. SCENARIO
    const scenarioBody = document.getElementById('mos-scenario-body');
    if (scenarioBody && ms.scenario) {
      const sc = ms.scenario;
      const scenarioLabel = labels.current_scenario || sc.signals.current_scenario;
      const scenarioColor = sc.signals.current_scenario.includes('SQUEEZE') ? 'text-red' : 'text-cyan';
      
      const riskLevelLabels = {
        'LOW': 'НИЗКИЙ',
        'MEDIUM': 'СРЕДНИЙ',
        'HIGH': 'ВЫСОКИЙ',
        'EXTREME': 'ЭКСТРЕМАЛЬНЫЙ'
      };
      const sqRiskLabel = riskLevelLabels[sc.signals.squeeze_risk] || sc.signals.squeeze_risk;
      const boRiskLabel = riskLevelLabels[sc.signals.breakout_risk] || sc.signals.breakout_risk;

      scenarioBody.innerHTML = `
        <div class="mos-metric"><span class="label">АКТИВНЫЙ СЦЕНАРИЙ:</span> <span class="val highlight ${scenarioColor}">${scenarioLabel}</span></div>
        <div class="mos-metric"><span class="label">ВЕРОЯТНОСТЬ:</span> <span class="val text-bright">${this.fmtPct(sc.signals.scenario_probability)}</span></div>
        <div class="mos-metric-separator"></div>
        <div class="mos-metric"><span class="label">РИСК СКВИЗА:</span> <span class="val ${sc.signals.squeeze_risk === 'HIGH' ? 'text-red' : 'text-green'}">${sqRiskLabel}</span></div>
        <div class="mos-metric"><span class="label">РИСК ПРОРЫВА:</span> <span class="val">${boRiskLabel}</span></div>
      `;
    }

    // 9. MANUAL TRADING DECISION
    const manualBody = document.getElementById('mos-manual-body');
    if (manualBody) {
      manualBody.innerHTML = this.renderManualDecision(ms.manual_setup || {});
    }
    this.refreshManualWatchlist();
  }

  renderManualDecision(manual) {
    if (!manual || !manual.manual_status) {
      return '<div class="loading-text">WAITING FOR MANUAL CONTEXT...</div>';
    }

    const statusClass = this._manualStatusClass(manual.manual_status);
    const biasClass = this._manualBiasClass(manual.manual_bias);
    const qualityClass = this._manualQualityClass(manual.setup_quality);

    return `
      <div class="manual-current-card">
        <div class="manual-decision-primary">
          <div class="manual-chip ${statusClass}"><span>CURRENT DECISION</span>${this.esc(manual.manual_status)}</div>
          <div class="manual-chip ${this._manualSetupTypeClass(manual.manual_setup_type)}"><span>SETUP</span>${this.esc(manual.manual_setup_type || 'NONE')}</div>
          <div class="manual-chip ${biasClass}"><span>BIAS</span>${this.esc(manual.manual_bias || 'NEUTRAL')}</div>
          <div class="manual-chip ${qualityClass}"><span>QUALITY</span>${this.esc(manual.setup_quality || 'INCOMPLETE')}</div>
        </div>
      </div>
      <div class="manual-decision-grid">
        <div class="manual-decision-copy">
          <div class="manual-copy-block">
            <span class="manual-label">WHY</span>
            <span class="manual-text">${this.esc(manual.manual_reason || '—')}</span>
          </div>
          <div class="manual-copy-block">
            <span class="manual-label">CONFIRMATION</span>
            <span class="manual-text">${this.esc(manual.confirmation_needed || '—')}</span>
          </div>
        </div>
        <div class="manual-decision-copy">
          <div class="manual-copy-block">
            <span class="manual-label">INVALIDATION</span>
            <span class="manual-text">${this.esc(manual.invalidation_condition || '—')}</span>
          </div>
        </div>
      </div>
      <div class="manual-decision-details">
        <div class="mos-metric"><span class="label">invalidation_level:</span> <span class="val">${this.fmtETH(manual.invalidation_level)}</span></div>
        <div class="mos-metric"><span class="label">avoid_reason:</span> <span class="val">${this.esc(manual.avoid_reason || '—')}</span></div>
      </div>
      ${this._renderMissingConditionsBlock(manual.missing_conditions)}
      <div class="manual-ladder">
        <div class="manual-ladder-title">decision_ladder</div>
        ${this.renderDecisionLadder(manual.decision_ladder)}
      </div>
    `;
  }

  renderDecisionLadder(ladder) {
    if (!ladder || typeof ladder !== 'object') {
      return '<div class="manual-ladder-empty">—</div>';
    }

    return Object.entries(ladder).map(([key, value]) => {
      const renderedValue = value && typeof value === 'object'
        ? JSON.stringify(value, null, 2)
        : String(value ?? '—');
      return `
        <div class="manual-ladder-row">
          <span class="manual-ladder-key">${this.esc(key)}</span>
          <pre class="manual-ladder-value">${this.esc(renderedValue)}</pre>
        </div>
      `;
    }).join('');
  }

  async refreshManualWatchlist(force = false) {
    const now = Date.now();
    if (this.manualWatchlistLoading) return;
    if (!force && now - this.manualWatchlistLastFetch < 5000) return;

    this.manualWatchlistLoading = true;
    this.manualWatchlistLastFetch = now;
    try {
      const payload = await fetchManualSetupWatchlist();
      this.manualWatchlistRows = Array.isArray(payload.rows) ? payload.rows : [];
      this.renderManualWatchlist();
    } catch (error) {
      this.renderManualWatchlistError();
    } finally {
      this.manualWatchlistLoading = false;
    }
  }

  toggleManualWatchlistFilter(kind, value) {
    const targetSet = kind === 'bias'
      ? this.manualWatchlistFilters.biases
      : this.manualWatchlistFilters.statuses;
    if (targetSet.has(value)) {
      targetSet.delete(value);
    } else {
      targetSet.add(value);
    }
    this.syncManualFilterButtons();
    this.renderManualWatchlist();
  }

  syncManualFilterButtons() {
    if (!this.container) return;
    this.container.querySelectorAll('[data-manual-filter]').forEach((button) => {
      const set = button.dataset.manualFilter === 'bias'
        ? this.manualWatchlistFilters.biases
        : this.manualWatchlistFilters.statuses;
      button.classList.toggle('is-active', set.has(button.dataset.manualValue));
    });
  }

  renderManualWatchlist() {
    const tbody = document.getElementById('manual-watchlist-body');
    if (!tbody) return;
    this.syncManualFilterButtons();
    const rows = this.filteredManualWatchlistRows();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="15" class="manual-watchlist-empty">NO RECENT MANUAL SETUPS</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map((row) => {
      const missingCompact = this.fmtMissingConditionsCompact(row.missing_conditions);
      const missingFull = this.fmtMissingConditionsFull(row.missing_conditions);
      return `
        <tr>
          <td>${this.esc(this.fmtTime(row.time))}</td>
          <td>${this.esc(row.setup_type || '—')}</td>
          <td><span class="manual-table-pill ${this._manualStatusClass(row.manual_status)}">${this.esc(row.manual_status || '—')}</span></td>
          <td><span class="manual-table-pill ${this._manualBiasClass(row.manual_bias)}">${this.esc(row.manual_bias || '—')}</span></td>
          <td><span class="manual-table-pill ${this._manualQualityClass(row.setup_quality)}">${this.esc(row.setup_quality || '—')}</span></td>
          <td class="manual-watchlist-missing" title="${this.esc(missingFull)}">${this.esc(missingCompact)}</td>
          <td class="manual-watchlist-price">${this.fmtETH(row.price)}</td>
          <td class="manual-watchlist-price">${this.fmtETH(row.nearest_level)}</td>
          <td>${this.esc(row.current_state || '—')}</td>
          <td>${this.esc(row.execution_timing_state || '—')}</td>
          <td>${this.esc(row.event_type || '—')}</td>
          <td>${this.esc(row.level_result || '—')}</td>
          <td>${this.esc(row.short_term_flow_direction || '—')}</td>
          <td class="manual-watchlist-long">${this.esc(row.confirmation_needed || '—')}</td>
          <td class="manual-watchlist-price">${this.fmtETH(row.invalidation_level)}</td>
        </tr>
      `;
    }).join('');
  }

  renderManualWatchlistError() {
    const tbody = document.getElementById('manual-watchlist-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="15" class="manual-watchlist-empty">WATCHLIST UNAVAILABLE</td></tr>';
  }

  filteredManualWatchlistRows() {
    const statuses = this.manualWatchlistFilters.statuses;
    const biases = this.manualWatchlistFilters.biases;
    return this.manualWatchlistRows.filter((row) => {
      if (statuses.size && !statuses.has(row.manual_status)) return false;
      if (biases.size && !biases.has(row.manual_bias)) return false;
      return true;
    });
  }

  fmtTime(value) {
    if (!value) return '—';
    const numeric = Number(value);
    const date = Number.isFinite(numeric)
      ? new Date(numeric < 1000000000000 ? numeric * 1000 : numeric)
      : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  fmtMissingConditions(value) {
    if (!Array.isArray(value) || !value.length) return '—';
    return value.join('; ');
  }

  fmtMissingConditionsCompact(value) {
    if (!Array.isArray(value) || !value.length) return '—';
    if (value.length === 1) return value[0];
    return `${value.length} missing`;
  }

  fmtMissingConditionsFull(value) {
    if (!Array.isArray(value) || !value.length) return '';
    return value.join('\n');
  }

  _renderMissingConditionsBlock(value) {
    if (!Array.isArray(value) || !value.length) return '';
    const items = value.map(v => `<li class="manual-missing-item">${this.esc(v)}</li>`).join('');
    return `
      <div class="manual-missing-block">
        <div class="manual-label">MISSING</div>
        <ul class="manual-missing-list">${items}</ul>
      </div>
    `;
  }

  fmtETH(val) {
    if (val == null || val === '') return '—';
    const num = Number(val);
    if (Number.isNaN(num)) return this.esc(String(val));
    return num.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  esc(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  fmtNum(val) {
    if (val == null) return '—';
    return Number(val).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }

  fmtLevel(val) {
    if (val == null || val === '') return '—';
    const num = Number(val);
    if (Number.isNaN(num)) return this.esc(val);
    return `$${num.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
  }

  fmtPct(val) {
    if (val == null) return '—';
    return Number(val).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + '%';
  }

  _manualStatusClass(status) {
    const map = {
      ENTRY_CANDIDATE: 'manual-status-entry',
      WATCH: 'manual-status-watch',
      AVOID: 'manual-status-avoid',
    };
    return map[status] || 'manual-status-avoid';
  }

  _manualBiasClass(bias) {
    const map = {
      LONG: 'manual-bias-long',
      SHORT: 'manual-bias-short',
      NEUTRAL: 'manual-bias-neutral',
    };
    return map[bias] || 'manual-bias-neutral';
  }

  _manualQualityClass(quality) {
    const map = {
      ACTIONABLE: 'manual-quality-actionable',
      FORMING: 'manual-quality-forming',
      INCOMPLETE: 'manual-quality-incomplete',
      INVALIDATED: 'manual-quality-invalidated',
    };
    return map[quality] || 'manual-quality-incomplete';
  }

  _manualConfidenceClass(confidence) {
    const map = {
      HIGH: 'manual-confidence-high',
      MEDIUM: 'manual-confidence-medium',
      LOW: 'manual-confidence-low',
    };
    return map[confidence] || 'manual-confidence-low';
  }

  _manualSetupTypeClass(setupType) {
    const chop = ['NO_TRADE_CHOP', 'UNSTABLE_STRUCTURE_AVOID'];
    const noActionable = ['NO_ACTIONABLE_SETUP', 'NONE'];
    const active = [
      'BREAKOUT_CONTINUATION_SETUP',
      'MID_RANGE_EXPANSION_SETUP',
      'SUPPORT_DEFENSE_REVERSAL_SETUP',
      'RESISTANCE_REJECTION_REVERSAL_SETUP',
      'FLOW_EXHAUSTION_REVERSAL_SETUP',
    ];
    if (chop.includes(setupType)) return 'manual-status-avoid';
    if (noActionable.includes(setupType)) return 'manual-quality-incomplete';
    if (active.includes(setupType)) return 'manual-status-watch';
    return '';
  }

  _volRegimeColor(regime) {
    const map = {
      'COMPRESSION': 'vol-state-cyan',
      'cyan': 'vol-state-cyan',
      'NORMAL': 'vol-state-neutral',
      'neutral': 'vol-state-neutral',
      'ELEVATED': 'vol-state-orange',
      'VOL EXPANSION': 'vol-state-orange',
      'orange': 'vol-state-orange',
      'PANIC': 'vol-state-red',
      'red': 'vol-state-red',
    };
    return map[regime] || 'vol-state-neutral';
  }

  _eventRiskColor(risk) {
    const map = {
      'LOW': 'text-dim',
      'MEDIUM': 'text-yellow',
      'HIGH': 'text-orange',
      'EXTREME': 'text-red-glow',
    };
    return map[risk] || 'text-dim';
  }
}
