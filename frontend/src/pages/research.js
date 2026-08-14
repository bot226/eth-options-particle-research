import { createChart, LineSeries, HistogramSeries, createSeriesMarkers } from 'lightweight-charts';

export class ResearchPage {
  constructor() {
    this.chartsInitialized = false;
    this.chartSpot = null;
    this.chartIntel = null;
    this.chartFlow = null;
    this.spotSeries = null;
    this.expSeries = null;
    this.failSeries = null;
    this.flowSeries = null;
    this.spotMarkers = null;
  }

  init(container) {
    if (this.chartsInitialized) return;
    this.chartsInitialized = true;

    container.innerHTML = `
      <div class="research-container">
        <div class="research-header">
          <h2 class="research-title">🔬 Institutional Replay Laboratory</h2>
          <div class="research-controls">
            <select id="time-range" class="research-select">
              <option value="1h">Last 1h</option>
              <option value="6h">Last 6h</option>
              <option value="24h" selected>Last 24h</option>
              <option value="3d">Last 3d</option>
              <option value="custom">Custom Range</option>
            </select>
            
            <div id="custom-range-container" style="display: none; align-items: center; gap: 4px;">
              <input type="datetime-local" id="custom-from" class="research-input">
              <span>-</span>
              <input type="datetime-local" id="custom-to" class="research-input">
            </div>
            
            <select id="resolution" class="research-select">
              <option value="">Res: Auto</option>
              <option value="15s">15s</option>
              <option value="1m">1m</option>
              <option value="5m">5m</option>
              <option value="15m">15m</option>
              <option value="1h">1h</option>
            </select>

            <button id="btn-refresh-research" class="research-btn">⟳ Load</button>
            <button id="btn-clear-research" class="research-btn research-btn-danger">🗑 Очистить историю</button>
            <button id="btn-export-research" class="research-btn research-btn-secondary">⬇ Export CSV</button>
          </div>
        </div>
        <div class="research-charts-wrap">
          <div class="research-chart-block">
            <div class="research-chart-label">SPOT PRICE & EVENTS (ETH/USDT)</div>
            <div id="chart-spot" class="research-chart"></div>
          </div>
          <div class="research-chart-block">
            <div class="research-chart-label">EXPANSION PROBABILITY & FAILURE RISK</div>
            <div id="chart-intelligence" class="research-chart"></div>
          </div>
          <div class="research-chart-block">
            <div class="research-chart-label">SYNTHETIC FLOW PRESSURE</div>
            <div id="chart-flow" class="research-chart"></div>
          </div>
        </div>
        <div id="research-status" class="research-status">Ожидание команды...</div>
      </div>
    `;

    // Inject styles
    if (!document.getElementById('research-styles')) {
      const style = document.createElement('style');
      style.id = 'research-styles';
      style.textContent = `
        .research-container {
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
          padding: 1rem 1.5rem;
          height: calc(100vh - 120px);
        }
        .research-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-shrink: 0;
        }
        .research-title {
          font-size: 1.2rem;
          font-weight: 700;
          color: var(--text-primary, #e0e6ed);
          margin: 0;
        }
        .research-controls {
          display: flex;
          gap: 0.5rem;
          align-items: center;
        }
        .research-select, .research-input {
          background: #111419;
          border: 1px solid var(--border, #2d3340);
          color: var(--text-primary, #e0e6ed);
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 0.8rem;
          font-family: inherit;
        }
        .research-btn {
          padding: 6px 16px;
          border-radius: 6px;
          border: 1px solid var(--accent, #2962FF);
          background: transparent;
          color: var(--accent, #2962FF);
          cursor: pointer;
          font-family: inherit;
          font-size: 0.8rem;
          font-weight: 600;
          transition: all 0.2s;
        }
        .research-btn:hover {
          background: var(--accent, #2962FF);
          color: #fff;
        }
        .research-btn-secondary {
          border-color: var(--border, #2d3340);
          color: var(--text-secondary, #8a92a0);
        }
        .research-btn-secondary:hover {
          background: var(--border, #2d3340);
          color: #fff;
        }
        .research-btn-danger {
          border-color: #ef5350;
          color: #ef5350;
        }
        .research-btn-danger:hover {
          background: #ef5350;
          color: #fff;
        }
        .research-charts-wrap {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          flex: 1;
          min-height: 0;
        }
        .research-chart-block {
          display: flex;
          flex-direction: column;
          flex: 1;
          min-height: 0;
        }
        .research-chart-block:first-child {
          flex: 1.5;
        }
        .research-chart-label {
          font-size: 0.65rem;
          font-weight: 700;
          letter-spacing: 1px;
          color: var(--text-secondary, #8a92a0);
          padding: 4px 8px;
          text-transform: uppercase;
        }
        .research-chart {
          flex: 1;
          min-height: 0;
          border: 1px solid var(--border, #2d3340);
          border-radius: 6px;
          overflow: hidden;
        }
        .research-status {
          font-size: 0.75rem;
          color: var(--text-secondary, #8a92a0);
          text-align: center;
          flex-shrink: 0;
        }
      `;
      document.head.appendChild(style);
    }

    const timeRangeSelect = document.getElementById('time-range');
    const customContainer = document.getElementById('custom-range-container');
    
    timeRangeSelect.addEventListener('change', (e) => {
      if (e.target.value === 'custom') {
        customContainer.style.display = 'flex';
      } else {
        customContainer.style.display = 'none';
      }
    });

    document.getElementById('btn-refresh-research').addEventListener('click', () => this.loadResearchData());
    document.getElementById('btn-export-research').addEventListener('click', () => this.exportCsv());
    document.getElementById('btn-clear-research').addEventListener('click', () => this.clearHistory());
    
    this.loadResearchData();
  }

  update(state) {
    // Research is offline replay — no live updates needed
  }

  getTimeWindow() {
    const range = document.getElementById('time-range').value;
    let toTs = Math.floor(Date.now() / 1000);
    let fromTs = toTs;

    if (range === 'custom') {
      const fromVal = document.getElementById('custom-from').value;
      const toVal = document.getElementById('custom-to').value;
      if (fromVal) fromTs = Math.floor(new Date(fromVal).getTime() / 1000);
      if (toVal) toTs = Math.floor(new Date(toVal).getTime() / 1000);
    } else {
      const map = { '1h': 3600, '6h': 21600, '24h': 86400, '3d': 259200 };
      fromTs = toTs - (map[range] || 86400);
    }
    return { fromTs, toTs };
  }

  async exportCsv() {
    const { fromTs, toTs } = this.getTimeWindow();
    const resolution = document.getElementById('resolution').value;
    
    let url = `/api/research/export?from=${fromTs}&to=${toTs}`;
    if (resolution) url += `&resolution=${resolution}`;
    
    window.open(url, '_blank');
  }

  async clearHistory() {
    const confirmed = confirm(
      '⚠️ Вы уверены?\n\nВсе данные MOS Research будут безвозвратно удалены:\n• Snapshots\n• Events\n• Future Labels\n• Bookmarks\n\nЭто действие нельзя отменить.'
    );
    if (!confirmed) return;

    const statusEl = document.getElementById('research-status');
    statusEl.textContent = 'Очистка базы данных...';

    try {
      const res = await fetch('/api/research/clear', { method: 'DELETE' });
      const data = await res.json();
      if (data.status === 'ok') {
        statusEl.textContent = `✅ ${data.message}`;
        // Clear charts
        if (this.spotSeries) this.spotSeries.setData([]);
        if (this.expSeries) this.expSeries.setData([]);
        if (this.failSeries) this.failSeries.setData([]);
        if (this.flowSeries) this.flowSeries.setData([]);
        if (this.spotMarkers) this.spotMarkers.setMarkers([]);
      } else {
        statusEl.textContent = `❌ Ошибка: ${data.detail || 'Unknown'}`;
      }
    } catch (err) {
      console.error('Failed to clear history:', err);
      statusEl.textContent = '❌ Ошибка очистки. Проверьте бэкенд.';
    }
  }

  async loadResearchData() {
    const statusEl = document.getElementById('research-status');
    const { fromTs, toTs } = this.getTimeWindow();
    const resolution = document.getElementById('resolution').value;

    try {
      statusEl.textContent = 'Загрузка данных replay...';
      let url = `/api/research/timeline?from=${fromTs}&to=${toTs}&limit=10000&include_events=true&include_labels=true`;
      if (resolution) url += `&resolution=${resolution}`;

      const res = await fetch(url);
      const data = await res.json();
      
      if (res.status === 400) {
          statusEl.textContent = `Ошибка: ${data.detail || 'Окно слишком велико'}`;
          return;
      }
      
      if (data.status !== 'ok' || !data.data || data.data.length === 0) {
        statusEl.textContent = `Данных за выбранный период нет (${data.count || 0} снимков).`;
        return;
      }

      this.renderCharts(data.data, data.events || []);
      
      const first = new Date(data.data[0].timestamp_utc * 1000).toLocaleTimeString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
      const last = new Date(data.data[data.data.length - 1].timestamp_utc * 1000).toLocaleTimeString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
      
      statusEl.textContent = `Загружено ${data.count} снимков · Разрешение: ${resolution || 'Raw'} · Диапазон: ${first} — ${last}`;
    } catch (err) {
      console.error('Failed to load research data:', err);
      statusEl.textContent = 'Ошибка загрузки данных. Проверьте бэкенд.';
    }
  }

  renderCharts(timelineData, eventsData) {
    const chartOptions = {
      layout: {
        background: { type: 'solid', color: '#111419' },
        textColor: '#6b7280',
        fontFamily: "'Inter', sans-serif",
      },
      grid: {
        vertLines: { color: '#1e2430' },
        horzLines: { color: '#1e2430' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#2d3340',
      },
      rightPriceScale: {
        borderColor: '#2d3340',
      },
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(41, 98, 255, 0.5)', width: 1, style: 2, labelBackgroundColor: '#2962FF' },
        horzLine: { color: 'rgba(41, 98, 255, 0.5)', width: 1, style: 2, labelBackgroundColor: '#2962FF' },
      },
    };

    const elSpot = document.getElementById('chart-spot');
    const elIntel = document.getElementById('chart-intelligence');
    const elFlow = document.getElementById('chart-flow');
    
    elSpot.innerHTML = '';
    elIntel.innerHTML = '';
    elFlow.innerHTML = '';

    this.chartSpot = createChart(elSpot, chartOptions);
    this.spotSeries = this.chartSpot.addSeries(LineSeries, {
      color: '#2962FF',
      lineWidth: 2,
      title: 'ETH Spot',
    });

    this.chartIntel = createChart(elIntel, chartOptions);
    this.expSeries = this.chartIntel.addSeries(LineSeries, {
      color: '#ef5350',
      lineWidth: 2,
      title: 'Expansion Prob %',
    });
    this.failSeries = this.chartIntel.addSeries(LineSeries, {
      color: '#ff9800',
      lineWidth: 1,
      lineStyle: 2, // dashed
      title: 'Failure Risk %',
    });

    this.chartFlow = createChart(elFlow, chartOptions);
    this.flowSeries = this.chartFlow.addSeries(HistogramSeries, {
      color: '#26a69a',
      priceFormat: { type: 'volume' },
      title: 'Flow',
    });

    // Prepare Data
    const spotData = [];
    const expData = [];
    const failData = [];
    const flowData = [];
    const markers = [];

    // Separate map for ensuring strict time uniqueness
    let lastTime = 0;

    for (const row of timelineData) {
      const time = Math.floor(row.timestamp_utc);

      // Lightweight charts requires strictly increasing timestamps
      if (time <= lastTime) continue;
      lastTime = time;

      if (!row.spot_price || row.spot_price <= 0) continue;

      spotData.push({ time, value: row.spot_price });
      expData.push({ time, value: row.expansion_probability || 0 });
      failData.push({ time, value: row.compression_failure_risk || 0 });

      const flowVal = row.synthetic_flow_pressure || 0;
      flowData.push({
        time,
        value: Math.abs(flowVal) || 0.001,
        color: flowVal >= 0 ? 'rgba(38, 166, 154, 0.7)' : 'rgba(239, 83, 80, 0.7)',
      });

      // Regular State transitions
      if (row.execution_timing_state && row.execution_timing_state !== 'WAIT') {
        markers.push({
          time,
          position: 'aboveBar',
          color: '#ff9800',
          shape: 'arrowDown',
          text: row.execution_timing_state,
        });
      }
    }
    
    // Create a Set of valid times to prevent lightweight-charts errors
    const validTimes = new Set(spotData.map(d => d.time));

    // Additional events from API
    for (const evt of eventsData) {
        let time = Math.floor(evt.timestamp_utc);
        
        // Snap to nearest valid time if it was downsampled
        if (!validTimes.has(time)) {
            const closest = spotData.find(d => d.time >= time);
            if (!closest) continue;
            time = closest.time;
        }

        // Only add if time falls inside our rendered timeline to avoid errors
        if (time >= spotData[0]?.time && time <= spotData[spotData.length-1]?.time) {
            markers.push({
                time,
                position: evt.severity === 'HIGH' || evt.severity === 'CRITICAL' ? 'belowBar' : 'aboveBar',
                color: evt.severity === 'CRITICAL' ? '#ef5350' : '#2962FF',
                shape: evt.severity === 'CRITICAL' ? 'arrowUp' : 'circle',
                text: evt.event_type || 'EVENT'
            });
        }
    }
    
    // Merge markers with the same timestamp, as lightweight-charts requires strictly unique times
    const mergedMarkers = [];
    const markerMap = new Map();
    
    for (const m of markers) {
        if (markerMap.has(m.time)) {
            const existing = markerMap.get(m.time);
            existing.text += ` | ${m.text}`;
            // Upgrade severity visually if critical
            if (m.color === '#ef5350') {
                existing.color = '#ef5350';
                existing.shape = 'arrowUp';
                existing.position = 'belowBar';
            }
        } else {
            const mCopy = {...m};
            markerMap.set(m.time, mCopy);
            mergedMarkers.push(mCopy);
        }
    }

    // Sort markers by time as required by lightweight-charts
    mergedMarkers.sort((a, b) => a.time - b.time);

    if (spotData.length === 0) return;

    this.spotSeries.setData(spotData);
    this.expSeries.setData(expData);
    this.failSeries.setData(failData);
    this.flowSeries.setData(flowData);

    if (mergedMarkers.length > 0) {
      if (!this.spotMarkers) {
        this.spotMarkers = createSeriesMarkers(this.spotSeries, mergedMarkers);
      } else {
        this.spotMarkers.setMarkers(mergedMarkers);
      }
    } else if (this.spotMarkers) {
      this.spotMarkers.setMarkers([]);
    }

    // --- SYNCHRONIZATION ---

    const charts = [
      { chart: this.chartSpot, series: this.spotSeries },
      { chart: this.chartIntel, series: this.expSeries },
      { chart: this.chartFlow, series: this.flowSeries }
    ];

    // 1. Zoom/Pan Sync
    const getLogicalRange = (chart) => chart.timeScale().getVisibleLogicalRange();
    
    charts.forEach(({ chart }) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (!range) return;
        charts.forEach(c => {
          if (c.chart !== chart) {
            c.chart.timeScale().setVisibleLogicalRange(range);
          }
        });
      });
    });

    // 2. Crosshair Sync
    const syncCrosshair = (sourceChart, param) => {
        const time = param.time;
        if (!time || param.point.x < 0 || param.point.y < 0) {
            // clear crosshair
            charts.forEach(c => {
                if (c.chart !== sourceChart) c.chart.clearCrosshairPosition();
            });
            return;
        }

        charts.forEach(c => {
            if (c.chart !== sourceChart && typeof c.chart.setCrosshairPosition === 'function') {
                // Find matching data point value
                const dataPoint = c.series.data().find(d => d.time === time);
                if (dataPoint) {
                    c.chart.setCrosshairPosition(dataPoint.value, time, c.series);
                }
            }
        });
    };

    this.chartSpot.subscribeCrosshairMove(param => syncCrosshair(this.chartSpot, param));
    this.chartIntel.subscribeCrosshairMove(param => syncCrosshair(this.chartIntel, param));
    this.chartFlow.subscribeCrosshairMove(param => syncCrosshair(this.chartFlow, param));

    // Auto-fit all
    this.chartSpot.timeScale().fitContent();
  }
}
