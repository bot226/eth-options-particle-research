/**
 * Panel 8: Summary — итоговые метрики.
 */

export function renderSummary(container, summary, spot, change) {
  if (!summary) {
    container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
    return;
  }

  const pcRatio = summary.put_call_ratio || 0;
  const totalOi = summary.total_oi || 0;
  const atmIv = summary.atm_iv?.avg_iv || 0;
  const callOi = summary.total_call_oi || 0;
  const putOi = summary.total_put_oi || 0;

  // Market Bias
  let bias = 'NEUTRAL';
  let biasClass = 'cyan';
  if (pcRatio < 0.8) { bias = 'BULLISH'; biasClass = 'green'; }
  else if (pcRatio > 1.2) { bias = 'BEARISH'; biasClass = 'red'; }

  const metrics = [
    { label: 'BTC Price', value: spot ? '$' + fmtK(spot) : '—', cls: '' },
    { label: '24H Change', value: change ? (change * 100).toFixed(2) + '%' : '—', cls: change >= 0 ? 'green' : 'red' },
    { label: 'Total OI', value: fmtK(totalOi), cls: '' },
    { label: 'Calls OI', value: fmtK(callOi), cls: 'green' },
    { label: 'Puts OI', value: fmtK(putOi), cls: 'red' },
    { label: 'Put/Call', value: pcRatio.toFixed(3), cls: pcRatio > 1 ? 'red' : 'green' },
    { label: 'ATM IV', value: (atmIv * 100).toFixed(1) + '%', cls: 'cyan' },
    { label: 'Market Bias', value: bias, cls: biasClass },
  ];

  let html = '';
  for (const m of metrics) {
    html += `<div class="summary-metric">
      <div class="label">${m.label}</div>
      <div class="value ${m.cls}">${m.value}</div>
    </div>`;
  }
  container.innerHTML = html;
}

function fmtK(n) {
  if (!n) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toFixed(0);
}
