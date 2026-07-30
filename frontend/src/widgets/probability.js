/**
 * Panel 2: Probability Curve — implied probability distribution.
 */
import Chart from 'chart.js/auto';

let chart = null;

const customSpotLinePlugin = {
  id: 'customSpotLine',
  afterDraw(chart, args, options) {
    if (!options.spot) return;
    const ctx = chart.ctx;
    const xAxis = chart.scales.x;
    const yAxis = chart.scales.y;
    
    // Find x position for the spot value (which is formatted like '80K')
    const spotStr = (options.spot / 1000).toFixed(0) + 'K';
    let xPos = null;
    
    // Simple interpolation if we don't have exact match
    for (let i = 0; i < chart.data.labels.length - 1; i++) {
      const val1 = parseFloat(chart.data.labels[i]);
      const val2 = parseFloat(chart.data.labels[i+1]);
      const spotVal = options.spot / 1000;
      
      if (spotVal >= val1 && spotVal <= val2) {
        const px1 = xAxis.getPixelForTick(i);
        const px2 = xAxis.getPixelForTick(i+1);
        const ratio = (spotVal - val1) / (val2 - val1);
        xPos = px1 + ratio * (px2 - px1);
        break;
      }
    }

    if (xPos !== null) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(xPos, yAxis.top);
      ctx.lineTo(xPos, yAxis.bottom);
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255, 167, 38, 0.8)';
      ctx.setLineDash([3, 3]);
      ctx.stroke();

      // Label
      ctx.fillStyle = 'rgba(255, 167, 38, 0.8)';
      ctx.fillRect(xPos - 15, yAxis.top, 30, 14);
      ctx.fillStyle = '#000';
      ctx.font = 'bold 9px "JetBrains Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('SPOT', xPos, yAxis.top + 7);
      
      ctx.restore();
    }
  }
};

export function renderProbability(canvasId, data, spot) {
  const container = document.getElementById(canvasId)?.parentElement;
  if (!container) return;

  const canvas = document.getElementById(canvasId);
  if (!data || !data.current || !data.current.x || !data.current.x.length) {
    if (chart) { chart.destroy(); chart = null; }
    return;
  }

  const current = data.current;
  const h1 = data.h1_ago && data.h1_ago.x && data.h1_ago.x.length ? data.h1_ago : null;
  const h24 = data.h24_ago && data.h24_ago.x && data.h24_ago.x.length ? data.h24_ago : null;

  const labels = current.x.map(v => (v / 1000).toFixed(0) + 'K');

  // Render Alert Overlays
  renderAlerts(container, current.metrics, spot);

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = current.pdf;
    
    if (h1) {
      if (chart.data.datasets.length < 2) addDataset(chart, '1h Ago', [5, 5], 0.4);
      chart.data.datasets[1].data = interpolateData(h1, current.x);
    }
    if (h24) {
      if (chart.data.datasets.length < 3) addDataset(chart, '24h Ago', [], 0.2);
      const idx = h1 ? 2 : 1;
      chart.data.datasets[idx].data = interpolateData(h24, current.x);
    }
    
    chart.options.plugins.customSpotLine.spot = spot;
    chart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 200);
  gradient.addColorStop(0, 'rgba(0, 229, 255, 0.4)');
  gradient.addColorStop(1, 'rgba(0, 229, 255, 0.0)');

  const datasets = [{
    label: 'Current PDF',
    data: current.pdf,
    borderColor: '#00e5ff',
    borderWidth: 2,
    backgroundColor: gradient,
    fill: true,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.4,
  }];

  if (h1) {
    datasets.push({
      label: '1h Ago',
      data: interpolateData(h1, current.x),
      borderColor: 'rgba(255, 255, 255, 0.4)',
      borderWidth: 1.5,
      borderDash: [5, 5],
      fill: false,
      pointRadius: 0,
      tension: 0.4,
    });
  }
  
  if (h24) {
    datasets.push({
      label: '24h Ago',
      data: interpolateData(h24, current.x),
      borderColor: 'rgba(255, 255, 255, 0.2)',
      borderWidth: 1,
      fill: false,
      pointRadius: 0,
      tension: 0.4,
    });
  }

  chart = new Chart(ctx, {
    type: 'line',
    plugins: [customSpotLinePlugin],
    data: {
      labels,
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500, easing: 'easeOutQuart' },
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        customSpotLine: {
          spot: spot
        },
        legend: { 
          display: true, 
          position: 'top', 
          align: 'end',
          labels: { color: '#7a7a9e', font: { family: 'JetBrains Mono', size: 9 }, boxWidth: 12 }
        },
        tooltip: {
          backgroundColor: 'rgba(10,10,24,0.95)',
          borderColor: 'rgba(0,229,255,0.4)',
          borderWidth: 1,
          titleFont: { family: 'JetBrains Mono', size: 11 },
          bodyFont: { family: 'JetBrains Mono', size: 10 },
          callbacks: {
            title: (ctx) => `Price: ${ctx[0].label}`,
            label: (ctx) => {
              const val = ctx.raw;
              const density = (val * 1e6).toFixed(2); 
              return `${ctx.dataset.label}: ${density}`;
            }
          }
        }
      },
      scales: {
        x: {
          display: true,
          ticks: {
            color: '#7a7a9e',
            font: { family: 'JetBrains Mono', size: 9 },
            maxTicksLimit: 12,
          },
          grid: { color: 'rgba(40,40,80,0.3)' },
        },
        y: {
          display: false,
          min: 0,
        },
      },
    },
  });
}

export function destroyProbability() {
  if (chart) { chart.destroy(); chart = null; }
}

function addDataset(chart, label, dash, opacity) {
  chart.data.datasets.push({
    label: label,
    data: [],
    borderColor: `rgba(255, 255, 255, ${opacity})`,
    borderWidth: dash.length ? 1.5 : 1,
    borderDash: dash,
    fill: false,
    pointRadius: 0,
    tension: 0.4,
  });
}

function interpolateData(oldData, newX) {
  const res = [];
  for (let nx of newX) {
    let closestI = 0;
    let minD = Infinity;
    for (let i = 0; i < oldData.x.length; i++) {
      const d = Math.abs(oldData.x[i] - nx);
      if (d < minD) { minD = d; closestI = i; }
    }
    res.push(oldData.pdf[closestI]);
  }
  return res;
}

function renderAlerts(container, metrics, spot) {
  if (!metrics) return;
  
  let wrapper = container.querySelector('.prob-alerts');
  if (!wrapper) {
    wrapper = document.createElement('div');
    wrapper.className = 'prob-alerts';
    container.insertBefore(wrapper, container.firstChild);
  }

  let html = '';
  
  if (metrics.bias === 'Bullish') {
    html += `<span class="prob-badge prob-bull">BULLISH SHIFT</span>`;
  } else if (metrics.bias === 'Bearish') {
    html += `<span class="prob-badge prob-bear">BEARISH SHIFT</span>`;
  }
  
  if (metrics.tail_risk === 'Elevated' || metrics.tail_risk === 'High') {
    html += `<span class="prob-badge prob-tail">TAIL RISK ${metrics.tail_risk.toUpperCase()}</span>`;
  }
  
  wrapper.innerHTML = html;
}
