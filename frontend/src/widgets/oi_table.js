/**
 * Panel 5: Top OI by Expiry — таблица агрегированного OI.
 */

export function renderOiTable(container, data) {
  if (!data || !data.length) {
    container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
    return;
  }

  let html = `<table class="data-table">
    <thead><tr>
      <th>Expiry</th><th>DTE</th><th>Calls OI</th><th>Puts OI</th>
      <th>Total OI</th><th>P/C Ratio</th>
    </tr></thead><tbody>`;

  const maxOi = Math.max(...data.map(d => d.total_oi));

  for (const row of data) {
    const isMax = row.total_oi === maxOi;
    const rowClass = isMax ? ' class="atm-row"' : '';
    const ratioClass = row.put_call_ratio > 1 ? 'cell-high' : row.put_call_ratio < 0.7 ? 'cell-medium' : '';
    html += `<tr${rowClass}>
      <td class="strike-col">${row.expiry}</td>
      <td>${row.dte}d</td>
      <td class="cell-medium">${fmtOi(row.calls_oi)}</td>
      <td class="cell-high">${fmtOi(row.puts_oi)}</td>
      <td>${fmtOi(row.total_oi)}</td>
      <td class="${ratioClass}">${row.put_call_ratio.toFixed(2)}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  container.innerHTML = html;
}

function fmtOi(n) {
  if (!n) return '—';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toFixed(0);
}
