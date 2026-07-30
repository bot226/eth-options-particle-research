/**
 * Panel 4: IV Term Structure — ATM IV vs DTE.
 */
import Chart from 'chart.js/auto';

let chart = null;

export function renderTermStructure(canvasId, data) {
  const container = document.getElementById(canvasId)?.parentElement;
  if (!container) return;
  const canvas = document.getElementById(canvasId);

  if (!data || !data.current || !data.current.length) {
    if (chart) { chart.destroy(); chart = null; }
    return;
  }

  const current = data.current;
  const metrics = data.metrics || {};
  const h24 = data.h24_ago && data.h24_ago.length ? data.h24_ago : null;

  const labels = current.map(d => d.dte + 'D');
  const expiries = current.map(d => d.expiry);
  const atmIv = current.map(d => d.atm_iv * 100);
  const callIv = current.map(d => d.call_25d_iv);
  const putIv = current.map(d => d.put_25d_iv);

  renderAlerts(container, metrics);

  if (chart) {
    chart.data.labels = labels;
    chart.options.plugins.tooltip.expiries = expiries;
    chart.data.datasets[0].data = atmIv;
    chart.data.datasets[1].data = callIv;
    chart.data.datasets[2].data = putIv;
    
    if (h24) {
      if (chart.data.datasets.length < 4) addH24Dataset(chart);
      chart.data.datasets[3].data = interpolateH24(h24, current);
    }
    
    chart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');

  const datasets = [
    {
      label: 'ATM IV',
      data: atmIv,
      borderColor: '#00e5ff',
      borderWidth: 2.5,
      pointRadius: 3,
      pointBackgroundColor: '#00e5ff',
      pointHoverRadius: 5,
      tension: 0.3,
    },
    {
      label: '25D Call IV',
      data: callIv,
      borderColor: '#00e676',
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.3,
    },
    {
      label: '25D Put IV',
      data: putIv,
      borderColor: '#ff3c3c',
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.3,
    }
  ];

  if (h24) {
    datasets.push({
      label: 'ATM 24h Ago',
      data: interpolateH24(h24, current),
      borderColor: 'rgba(255, 255, 255, 0.4)',
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.3,
    });
  }

  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400, easing: 'easeOutQuart' },
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'end',
          labels: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 9 }, boxWidth: 12 }
        },
        tooltip: {
          expiries: expiries,
          backgroundColor: 'rgba(10,10,24,0.95)',
          borderColor: 'rgba(99,102,241,0.3)',
          borderWidth: 1,
          titleFont: { family: 'JetBrains Mono', size: 11 },
          bodyFont: { family: 'JetBrains Mono', size: 10 },
          callbacks: {
            title: function(ctx) {
              const idx = ctx[0].dataIndex;
              const dte = ctx[0].label;
              const exp = this.options.plugins.tooltip.expiries[idx];
              return `DTE: ${dte} | Exp: ${exp}`;
            },
            label: (ctx) => {
              const val = ctx.raw;
              if (val === null || val === undefined) return null;
              return `${ctx.dataset.label}: ${val.toFixed(1)}%`;
            }
          }
        },
      },
      scales: {
        x: {
          ticks: {
            color: '#7a7a9e',
            font: { family: 'JetBrains Mono', size: 9 },
          },
          grid: { color: 'rgba(40,40,80,0.3)' },
        },
        y: {
          ticks: {
            color: '#7a7a9e',
            font: { family: 'JetBrains Mono', size: 9 },
            callback: v => v.toFixed(0) + '%',
          },
          grid: { color: 'rgba(40,40,80,0.3)' },
        },
      },
    },
  });
}

function addH24Dataset(chart) {
  chart.data.datasets.push({
    label: 'ATM 24h Ago',
    data: [],
    borderColor: 'rgba(255, 255, 255, 0.4)',
    borderWidth: 1.5,
    borderDash: [4, 4],
    pointRadius: 0,
    tension: 0.3,
  });
}

function interpolateH24(h24, current) {
  const res = [];
  for (let point of current) {
    const targetDte = point.dte;
    // Find closest dte in h24
    let closestVal = null;
    let minDiff = Infinity;
    for (let h of h24) {
      const diff = Math.abs(h.dte - targetDte);
      if (diff < minDiff) {
        minDiff = diff;
        closestVal = h.atm_iv * 100;
      }
    }
    // If it's too far (e.g. > 3 days diff for short term), we might want to drop it, but keeping it simple
    res.push(closestVal);
  }
  return res;
}

function renderAlerts(container, metrics) {
  if (!metrics) return;
  
  let wrapper = container.querySelector('.term-alerts');
  if (!wrapper) {
    wrapper = document.createElement('div');
    wrapper.className = 'term-alerts';
    container.insertBefore(wrapper, container.firstChild);
  }

  let html = '';
  
  if (metrics.regime === 'PANIC') {
    html += `<span class="term-badge term-panic">REGIME: PANIC</span>`;
  } else if (metrics.regime === 'EXPANSION') {
    html += `<span class="term-badge term-exp">REGIME: EXPANSION</span>`;
  } else if (metrics.regime === 'COMPRESSION') {
    html += `<span class="term-badge term-comp">REGIME: COMPRESSION</span>`;
  } else {
    html += `<span class="term-badge term-norm">REGIME: NORMAL</span>`;
  }
  
  if (metrics.event_risk === 'High' || metrics.event_risk === 'Elevated') {
    html += `<span class="term-badge term-event">EVENT RISK: ${metrics.event_risk.toUpperCase()}</span>`;
  }
  
  wrapper.innerHTML = html;
}

export function destroyTermStructure() {
  if (chart) { chart.destroy(); chart = null; }
}
