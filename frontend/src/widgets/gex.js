/**
 * GAMMA EXPOSURE (GEX) — Institutional Market Maker Pressure Map
 *
 * 3-column layout:
 *   1. Per-Strike Net GEX Histogram (importance-weighted opacity)
 *   2. Cumulative GEX Curve (gradient fill + 24h comparison)
 *   3. Pressure Panel (HTML metrics + regime + alerts)
 */
import Chart from 'chart.js/auto';

let histoChart = null;
let curveChart = null;

// ─── helpers ───────────────────────────────────────────────────────
function fmtGex(v) {
  if (v == null) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

function fmtK(n) {
  return (n / 1000).toFixed(0) + 'K';
}

// ─── Chart.js plugin: Spot line + Flip zone + Wall markers ────────
const gexOverlayPlugin = {
  id: 'gexOverlay',
  afterDraw(chart, _args, options) {
    if (!options || !options.spot) return;
    const ctx = chart.ctx;
    const yAxis = chart.scales.y;
    const xAxis = chart.scales.x;
    const labels = chart.data.labels;
    if (!labels || !labels.length) return;

    // Find pixel Y for a given strike value (in K label form)
    function getPixelY(strikeVal) {
      const kVal = strikeVal / 1000;
      for (let i = 0; i < labels.length - 1; i++) {
        const v1 = parseFloat(labels[i]);
        const v2 = parseFloat(labels[i + 1]);
        if (kVal >= Math.min(v1, v2) && kVal <= Math.max(v1, v2)) {
          const py1 = yAxis.getPixelForTick(i);
          const py2 = yAxis.getPixelForTick(i + 1);
          const ratio = Math.abs(kVal - v1) / Math.abs(v2 - v1 || 1);
          return py1 + ratio * (py2 - py1);
        }
      }
      return null;
    }

    // ── Spot zone overlay (±1.5%) ──
    const spotHigh = options.spot * 1.015;
    const spotLow = options.spot * 0.985;
    const pyHigh = getPixelY(spotHigh);
    const pyLow = getPixelY(spotLow);
    if (pyHigh != null && pyLow != null) {
      ctx.save();
      ctx.fillStyle = 'rgba(255, 167, 38, 0.06)';
      ctx.fillRect(xAxis.left, Math.min(pyHigh, pyLow), xAxis.right - xAxis.left, Math.abs(pyLow - pyHigh));
      ctx.restore();
    }

    // ── Spot line ──
    const spotY = getPixelY(options.spot);
    if (spotY != null) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(xAxis.left, spotY);
      ctx.lineTo(xAxis.right, spotY);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = 'rgba(255, 167, 38, 0.9)';
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      // label
      ctx.fillStyle = 'rgba(255, 167, 38, 0.9)';
      ctx.fillRect(xAxis.right - 40, spotY - 8, 40, 16);
      ctx.fillStyle = '#000';
      ctx.font = 'bold 9px "JetBrains Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('SPOT', xAxis.right - 20, spotY);
      ctx.restore();
    }

    // ── Flip zone ──
    if (options.flipZone) {
      const flipY = getPixelY(options.flipZone);
      if (flipY != null) {
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(xAxis.left, flipY);
        ctx.lineTo(xAxis.right, flipY);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = 'rgba(186, 104, 200, 0.8)';
        ctx.setLineDash([6, 4]);
        ctx.stroke();
        // shaded zone ±0.5%
        const fzHigh = getPixelY(options.flipZone * 1.005);
        const fzLow = getPixelY(options.flipZone * 0.995);
        if (fzHigh != null && fzLow != null) {
          ctx.fillStyle = 'rgba(186, 104, 200, 0.06)';
          ctx.fillRect(xAxis.left, Math.min(fzHigh, fzLow), xAxis.right - xAxis.left, Math.abs(fzLow - fzHigh));
        }
        // label
        ctx.fillStyle = 'rgba(186, 104, 200, 0.85)';
        ctx.fillRect(xAxis.left, flipY - 8, 52, 16);
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 8px "JetBrains Mono"';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('FLIP ZONE', xAxis.left + 26, flipY);
        ctx.restore();
      }
    }

    // ── Wall markers ──
    function drawWall(strike, label, color) {
      const wy = getPixelY(strike);
      if (wy == null) return;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(xAxis.left, wy);
      ctx.lineTo(xAxis.right, wy);
      ctx.lineWidth = 2;
      ctx.strokeStyle = color;
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.5;
      ctx.stroke();
      // glow
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
      ctx.fillStyle = color;
      const tw = ctx.measureText(label).width + 10;
      ctx.fillRect(xAxis.right - tw - 2, wy - 8, tw, 16);
      ctx.shadowBlur = 0;
      ctx.fillStyle = '#000';
      ctx.font = 'bold 7px "JetBrains Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, xAxis.right - tw / 2 - 2, wy);
      ctx.restore();
    }

    if (options.wallAbove) drawWall(options.wallAbove, 'CALL WALL', '#00e676');
    if (options.wallBelow) drawWall(options.wallBelow, 'PUT WALL', '#ff3c3c');
  }
};

// ─── MAIN RENDER ──────────────────────────────────────────────────
export function renderGex(histCanvasId, curveCanvasId, data, spot) {
  const histCanvas = document.getElementById(histCanvasId);
  const curveCanvas = document.getElementById(curveCanvasId);
  const pressurePanel = document.getElementById('gex-pressure-panel');

  if (!data || !data.current || !data.current.length) {
    destroyGex();
    if (pressurePanel) pressurePanel.innerHTML = '';
    return;
  }

  const current = data.current;
  const metrics = data.metrics || {};
  const h24 = data.h24_ago && data.h24_ago.length ? data.h24_ago : null;

  // Find max importance for normalization
  const maxImp = Math.max(...current.map(d => d.importance), 0.001);

  // Labels (only strikes within reasonable range around spot for readability)
  const filtered = current.filter(d => d.dist_pct <= 30);
  const labels = filtered.map(d => fmtK(d.strike));
  const netGex = filtered.map(d => d.net_gex);
  const cumGex = filtered.map(d => d.cumulative_gex);
  const importances = filtered.map(d => d.importance);

  // Per-bar colors: green for positive, red for negative; alpha based on importance
  const barBg = filtered.map(d => {
    const alpha = Math.max(0.2, Math.min(1, d.importance / maxImp));
    return d.net_gex >= 0
      ? `rgba(0, 230, 118, ${alpha})`
      : `rgba(255, 60, 60, ${(alpha * 1.2).toFixed(2)})`;  // negative brighter
  });
  const barBorder = filtered.map(d => d.net_gex >= 0 ? '#00e676' : '#ff3c3c');
  // Negative gamma gets thicker bars
  const barWidth = filtered.map(d => d.net_gex < 0 ? 0.95 : 0.75);

  const overlayOpts = {
    spot,
    flipZone: metrics.gamma_flip || 0,
    wallAbove: metrics.gamma_wall_above || 0,
    wallBelow: metrics.gamma_wall_below || 0
  };

  // ─── HISTOGRAM ──────────────────────────────────────────────────
  if (histoChart) {
    histoChart.data.labels = labels;
    histoChart.data.datasets[0].data = netGex;
    histoChart.data.datasets[0].backgroundColor = barBg;
    histoChart.data.datasets[0].borderColor = barBorder;
    histoChart.options.plugins.gexOverlay = overlayOpts;
    histoChart.update('none');
  } else {
    histoChart = new Chart(histCanvas.getContext('2d'), {
      type: 'bar',
      plugins: [gexOverlayPlugin],
      data: {
        labels,
        datasets: [{
          label: 'Net GEX per Strike',
          data: netGex,
          backgroundColor: barBg,
          borderColor: barBorder,
          borderWidth: 1,
          barPercentage: 0.85,
          categoryPercentage: 0.92
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          gexOverlay: overlayOpts,
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(8,8,20,0.96)',
            borderColor: 'rgba(99,102,241,0.4)',
            borderWidth: 1,
            titleFont: { family: 'JetBrains Mono', size: 11, weight: 'bold' },
            bodyFont: { family: 'JetBrains Mono', size: 10 },
            padding: 10,
            callbacks: {
              title: (items) => {
                const idx = items[0].dataIndex;
                const d = filtered[idx];
                return `Strike: ${fmtK(d.strike)}`;
              },
              label: (ctx) => {
                const d = filtered[ctx.dataIndex];
                const lines = [
                  `Net GEX: ${fmtGex(d.net_gex)}`,
                  `Call GEX: ${fmtGex(d.call_gex)}`,
                  `Put GEX: ${fmtGex(d.put_gex)}`,
                  `Distance: ${d.dist_pct.toFixed(1)}%`,
                  `Importance: ${d.importance > maxImp * 0.7 ? 'Very High' : d.importance > maxImp * 0.4 ? 'High' : d.importance > maxImp * 0.15 ? 'Medium' : 'Low'}`,
                ];
                return lines;
              }
            }
          }
        },
        scales: {
          x: {
            title: { display: true, text: 'Net GEX (ETH)', color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 9 } },
            ticks: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 8 } },
            grid: { color: 'rgba(40,40,80,0.25)' }
          },
          y: {
            ticks: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 8 } },
            grid: { color: 'rgba(40,40,80,0.1)' }
          }
        }
      }
    });
  }

  // ─── CUMULATIVE CURVE ───────────────────────────────────────────
  if (curveChart) {
    curveChart.data.labels = labels;
    curveChart.data.datasets[0].data = cumGex;
    if (h24) {
      if (curveChart.data.datasets.length < 2) {
        curveChart.data.datasets.push(makeH24Dataset([]));
      }
      curveChart.data.datasets[1].data = interpolateH24(h24, filtered, 'cumulative_gex');
    }
    curveChart.options.plugins.gexOverlay = overlayOpts;
    curveChart.update('none');
  } else {
    const ctxC = curveCanvas.getContext('2d');
    // Gradient fill: green above 0, red below 0
    const gradFill = ctxC.createLinearGradient(ctxC.canvas.width / 2, 0, ctxC.canvas.width / 2, ctxC.canvas.height);
    gradFill.addColorStop(0, 'rgba(0, 230, 118, 0.15)');
    gradFill.addColorStop(0.5, 'rgba(0, 230, 118, 0.02)');
    gradFill.addColorStop(0.5, 'rgba(255, 60, 60, 0.02)');
    gradFill.addColorStop(1, 'rgba(255, 60, 60, 0.15)');

    const datasets = [{
      label: 'Cumulative Net GEX',
      data: cumGex,
      borderColor: '#00e5ff',
      borderWidth: 2,
      pointRadius: 1.5,
      pointBackgroundColor: cumGex.map(v => v >= 0 ? '#00e676' : '#ff3c3c'),
      fill: true,
      backgroundColor: gradFill,
      tension: 0.35
    }];

    if (h24) {
      datasets.push(makeH24Dataset(interpolateH24(h24, filtered, 'cumulative_gex')));
    }

    curveChart = new Chart(ctxC, {
      type: 'line',
      plugins: [gexOverlayPlugin],
      data: { labels, datasets },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          gexOverlay: overlayOpts,
          legend: {
            display: true, position: 'top',
            labels: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 8 }, boxWidth: 10, padding: 8 }
          },
          tooltip: {
            backgroundColor: 'rgba(8,8,20,0.96)',
            borderColor: 'rgba(99,102,241,0.4)',
            borderWidth: 1,
            titleFont: { family: 'JetBrains Mono', size: 11 },
            bodyFont: { family: 'JetBrains Mono', size: 10 },
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${fmtGex(ctx.raw)}`
            }
          }
        },
        scales: {
          x: {
            title: { display: true, text: 'Cumulative GEX', color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 9 } },
            ticks: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 8 } },
            grid: { color: 'rgba(40,40,80,0.25)' }
          },
          y: { display: false }
        }
      }
    });
  }

  // ─── PRESSURE PANEL ─────────────────────────────────────────────
  renderPressurePanel(pressurePanel, metrics, h24, current);
}

// ─── Pressure Panel (HTML) ────────────────────────────────────────
function renderPressurePanel(el, m, h24, current) {
  if (!el) return;

  const regimeClass = {
    'POSITIVE GAMMA': 'gex-regime-positive',
    'NEGATIVE GAMMA': 'gex-regime-negative',
    'LOW GAMMA': 'gex-regime-low',
    'PINNING': 'gex-regime-pinning',
  }[m.regime] || 'gex-regime-low';

  const squeezeClass = m.squeeze_risk === 'High' ? 'gex-val-danger'
    : m.squeeze_risk === 'Medium' ? 'gex-val-warn' : 'gex-val-dim';

  const pinClass = m.pinning_prob === 'High' ? 'gex-val-stable'
    : m.pinning_prob === 'Medium' ? 'gex-val-warn' : 'gex-val-dim';

  const netColor = m.total_net_gex >= 0 ? 'gex-val-green' : 'gex-val-red';
  const nsColor = m.near_spot_gex >= 0 ? 'gex-val-green' : 'gex-val-red';

  // GEX Δ24h
  let deltaHtml = '';
  if (h24 && h24.length) {
    const oldNet = h24.reduce((s, p) => s + (p.net_gex || 0), 0);
    const delta = m.total_net_gex - oldNet;
    const deltaSign = delta >= 0 ? '+' : '';
    deltaHtml = `<div class="gex-pp-row">
      <span class="gex-pp-label">GEX Δ24h</span>
      <span class="gex-pp-value ${delta >= 0 ? 'gex-val-green' : 'gex-val-red'}">${deltaSign}${fmtGex(delta)}</span>
    </div>`;
  }

  el.innerHTML = `
    <div class="gex-pp-regime ${regimeClass}">${m.regime || 'LOW GAMMA'}</div>

    <div class="gex-pp-row">
      <span class="gex-pp-label">Total Net GEX</span>
      <span class="gex-pp-value gex-pp-big ${netColor}">${fmtGex(m.total_net_gex)}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Near-Spot GEX</span>
      <span class="gex-pp-value ${nsColor}">${fmtGex(m.near_spot_gex)}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Positive GEX</span>
      <span class="gex-pp-value gex-val-green">${fmtGex(m.total_positive_gex)}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Negative GEX</span>
      <span class="gex-pp-value gex-val-red">${fmtGex(m.total_negative_gex)}</span>
    </div>
    ${deltaHtml}

    <div class="gex-pp-divider"></div>

    <div class="gex-pp-row">
      <span class="gex-pp-label">Squeeze Risk</span>
      <span class="gex-pp-value ${squeezeClass}">${m.squeeze_risk || 'Low'}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Pinning Prob</span>
      <span class="gex-pp-value ${pinClass}">${m.pinning_prob || 'Low'}</span>
    </div>

    <div class="gex-pp-divider"></div>

    <div class="gex-pp-row">
      <span class="gex-pp-label">Wall Up</span>
      <span class="gex-pp-value gex-val-cyan">${m.gamma_wall_above ? fmtK(m.gamma_wall_above) : '—'}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Wall Dn</span>
      <span class="gex-pp-value gex-val-cyan">${m.gamma_wall_below ? fmtK(m.gamma_wall_below) : '—'}</span>
    </div>
    <div class="gex-pp-row">
      <span class="gex-pp-label">Flip Zone</span>
      <span class="gex-pp-value gex-val-purple">${m.gamma_flip ? fmtK(m.gamma_flip) : '—'}</span>
    </div>
  `;
}

// ─── Utilities ────────────────────────────────────────────────────
function makeH24Dataset(data) {
  return {
    label: 'Cumul. GEX 24h Ago',
    data,
    borderColor: 'rgba(255,255,255,0.25)',
    borderWidth: 1.5,
    borderDash: [4, 4],
    pointRadius: 0,
    fill: false,
    tension: 0.35
  };
}

function interpolateH24(h24, current, field) {
  return current.map(pt => {
    let best = 0, minD = Infinity;
    for (const h of h24) {
      const d = Math.abs(h.strike - pt.strike);
      if (d < minD) { minD = d; best = h[field] || 0; }
    }
    return best;
  });
}

export function destroyGex() {
  if (histoChart) { histoChart.destroy(); histoChart = null; }
  if (curveChart) { curveChart.destroy(); curveChart = null; }
}
