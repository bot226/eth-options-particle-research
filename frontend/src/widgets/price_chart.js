/**
 * Panel 7: BTC Price Chart + GEX Sidebar + OI Levels.
 * Uses Lightweight Charts for candlestick.
 */
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';

let lwChart = null;
let candleSeries = null;

export function renderPriceChart(containerId, klines, oiLevels, spot) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!klines || !klines.length) return;

  const candleData = klines.map(k => ({
    time: Math.floor(k.ts / 1000),
    open: k.o,
    high: k.h,
    low: k.l,
    close: k.c,
  }));

  if (!lwChart) {
    lwChart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 300,
      layout: {
        background: { color: 'transparent' },
        textColor: '#7a7a9e',
        fontFamily: 'JetBrains Mono',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(40,40,80,0.2)' },
        horzLines: { color: 'rgba(40,40,80,0.2)' },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(99,102,241,0.4)', width: 1 },
        horzLine: { color: 'rgba(99,102,241,0.4)', width: 1 },
      },
      rightPriceScale: {
        borderColor: 'rgba(40,40,80,0.3)',
      },
      timeScale: {
        borderColor: 'rgba(40,40,80,0.3)',
        timeVisible: false,
      },
    });

    candleSeries = lwChart.addSeries(CandlestickSeries, {
      upColor: '#00e676',
      downColor: '#ff5252',
      borderUpColor: '#00e676',
      borderDownColor: '#ff5252',
      wickUpColor: '#00e676',
      wickDownColor: '#ff5252',
    });

    // Resize observer
    const ro = new ResizeObserver(() => {
      lwChart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight,
      });
    });
    ro.observe(container);
  }

  candleSeries.setData(candleData);

  // OI level lines
  if (oiLevels && oiLevels.length) {
    const markers = oiLevels.slice(0, 5).map(lvl => ({
      price: lvl.strike,
      color: lvl.call_oi > lvl.put_oi ? 'rgba(0,230,118,0.5)' : 'rgba(255,82,82,0.5)',
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: `OI ${(lvl.total_oi / 1000).toFixed(1)}K`,
    }));
    candleSeries.createPriceLine && markers.forEach(m => {
      try { candleSeries.createPriceLine(m); } catch(e) { /* ignore */ }
    });
  }

  lwChart.timeScale().fitContent();
}

export function renderGexSidebar(containerId, gexData) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!gexData || !gexData.length) {
    container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
    return;
  }

  // Фильтруем значимые GEX
  const significant = gexData
    .filter(g => Math.abs(g.net_gex) > 0)
    .sort((a, b) => Math.abs(b.net_gex) - Math.abs(a.net_gex))
    .slice(0, 15);

  if (!significant.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:11px;padding:10px;">No GEX data</div>';
    return;
  }

  const maxGex = Math.max(...significant.map(g => Math.abs(g.net_gex)));

  let html = '<div class="gex-bar-wrap"><div style="font-size:9px;color:var(--text-dim);font-weight:600;letter-spacing:1px;margin-bottom:4px;">GAMMA EXPOSURE</div>';
  for (const g of significant) {
    const pct = maxGex > 0 ? Math.abs(g.net_gex) / maxGex * 100 : 0;
    const cls = g.net_gex >= 0 ? 'positive' : 'negative';
    const strikeLabel = g.strike >= 1000 ? (g.strike / 1000).toFixed(0) + 'K' : g.strike;
    html += `<div class="gex-row">
      <span class="gex-label">${strikeLabel}</span>
      <div class="gex-bar-container">
        <div class="gex-bar ${cls}" style="width:${pct.toFixed(1)}%"></div>
      </div>
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

export function destroyPriceChart() {
  if (lwChart) {
    lwChart.remove();
    lwChart = null;
    candleSeries = null;
  }
}
