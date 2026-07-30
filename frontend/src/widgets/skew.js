/**
 * Panel 3: 25D Risk Reversal Skew — история skew за сессию.
 */
import Chart from 'chart.js/auto';

let chart = null;
const history = [];  // {ts, skew}

export function renderSkew(canvasId, skewData) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (!skewData) return;

  history.push({ ts: Date.now(), skew: skewData.skew });
  // Храним последние 200 точек
  if (history.length > 200) history.shift();

  const labels = history.map((_, i) => i);
  const values = history.map(h => h.skew);

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = values;
    chart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  const gradientPos = ctx.createLinearGradient(0, 0, 0, canvas.height || 200);
  gradientPos.addColorStop(0, 'rgba(0, 230, 118, 0.25)');
  gradientPos.addColorStop(1, 'rgba(0, 230, 118, 0.01)');

  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: '25D Skew (%)',
        data: values,
        borderColor: '#00e676',
        borderWidth: 2,
        backgroundColor: gradientPos,
        fill: { target: 'origin', above: gradientPos, below: 'rgba(255,82,82,0.1)' },
        pointRadius: 0,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 200 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10,10,24,0.9)',
          borderColor: 'rgba(99,102,241,0.3)',
          borderWidth: 1,
          callbacks: {
            label: ctx => `Skew: ${ctx.parsed.y.toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: {
          display: false,
        },
        y: {
          ticks: {
            color: '#7a7a9e',
            font: { family: 'JetBrains Mono', size: 9 },
            callback: v => v.toFixed(1) + '%',
          },
          grid: { color: 'rgba(40,40,80,0.3)' },
        },
      },
    },
  });
}

export function destroySkew() {
  if (chart) { chart.destroy(); chart = null; }
  history.length = 0;
}
