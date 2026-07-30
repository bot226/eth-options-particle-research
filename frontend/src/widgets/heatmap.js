/**
 * Panel 1: Options Heatmap — Institutional Positioning Map
 */

export function renderHeatmap(container, data) {
  if (!data || !data.length) {
    container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
    return;
  }

  // Get metadata (we assume the backend has attached heatmap_metadata to the top-level response, 
  // but to keep heatmap.js decoupled, we'll extract it from data if we can or just calculate it here)
  const maxCallOi = Math.max(...data.map(r => r.calls_oi));
  const maxPutOi = Math.max(...data.map(r => r.puts_oi));
  
  let callWallStrike = null;
  let putWallStrike = null;
  data.forEach(r => {
    if (r.calls_oi === maxCallOi && maxCallOi > 0) callWallStrike = r.strike;
    if (r.puts_oi === maxPutOi && maxPutOi > 0) putWallStrike = r.strike;
  });

  // Calculate Bias
  const totalCallVol = data.reduce((sum, r) => sum + r.calls_vol, 0);
  const totalPutVol = data.reduce((sum, r) => sum + r.puts_vol, 0);
  let bias = "NEUTRAL";
  let biasColor = "var(--text-bright)";
  if (totalCallVol > totalPutVol * 1.5) { bias = "BULLISH"; biasColor = "var(--green)"; }
  else if (totalPutVol > totalCallVol * 1.5) { bias = "BEARISH"; biasColor = "var(--red)"; }

  let html = `
    <div class="heatmap-header-summary">
      <div class="hm-badge bias-badge" style="color: ${biasColor}; border-color: ${biasColor};">BIAS: ${bias}</div>
      <div class="hm-badge">CALL WALL: ${fmtStrike(callWallStrike)}</div>
      <div class="hm-badge">PUT WALL: ${fmtStrike(putWallStrike)}</div>
    </div>
    <div class="heatmap-table-wrap">
      <table class="heatmap-table">
        <thead>
          <tr>
            <th>Call IV</th>
            <th>Call Vol</th>
            <th>Call Δ24h</th>
            <th>Call OI</th>
            <th class="strike-col">Strike</th>
            <th>Dist %</th>
            <th>Put OI</th>
            <th>Put Δ24h</th>
            <th>Put Vol</th>
            <th>Put IV</th>
          </tr>
        </thead>
        <tbody>
  `;

  for (const row of data) {
    const isAtm = row.is_atm;
    const isCallWall = row.strike === callWallStrike;
    const isPutWall = row.strike === putWallStrike;
    
    // Intensity (0 to 1)
    const callInt = maxCallOi > 0 ? (row.calls_oi / maxCallOi) : 0;
    const putInt = maxPutOi > 0 ? (row.puts_oi / maxPutOi) : 0;
    
    const callBg = `rgba(0, 230, 118, ${callInt * 0.4})`;
    const putBg = `rgba(255, 82, 82, ${putInt * 0.4})`;
    
    const atmClass = isAtm ? 'atm-highlight' : '';
    const distClass = row.distance_from_spot_pct > 0 ? 'dist-pos' : (row.distance_from_spot_pct < 0 ? 'dist-neg' : '');
    
    // Delta highlighting
    const cDeltaClass = getDeltaClass(row.calls_oi_delta);
    const pDeltaClass = getDeltaClass(row.puts_oi_delta);

    // Tooltip data string
    const tooltipData = encodeURIComponent(JSON.stringify(row));

    html += `
      <tr class="hm-row ${atmClass}" data-tt="${tooltipData}">
        <td class="iv-cell">${fmtIv(row.calls_iv)}</td>
        <td class="vol-cell">${fmtNum(row.calls_vol)}</td>
        <td class="delta-cell ${cDeltaClass}">${fmtDelta(row.calls_oi_delta)}</td>
        <td class="oi-cell" style="background: ${callBg}; box-shadow: inset 2px 0 0 ${callInt > 0.8 ? 'var(--green)' : 'transparent'};">
          ${fmtNum(row.calls_oi)}
          ${isCallWall ? '<span class="wall-badge call-wall">WALL</span>' : ''}
        </td>
        
        <td class="strike-col ${isAtm ? 'atm-strike' : ''}">${fmtStrike(row.strike)}</td>
        <td class="dist-col ${distClass}">${fmtDist(row.distance_from_spot_pct)}</td>
        
        <td class="oi-cell" style="background: ${putBg}; box-shadow: inset -2px 0 0 ${putInt > 0.8 ? 'var(--red)' : 'transparent'};">
          ${isPutWall ? '<span class="wall-badge put-wall">WALL</span>' : ''}
          ${fmtNum(row.puts_oi)}
        </td>
        <td class="delta-cell ${pDeltaClass}">${fmtDelta(row.puts_oi_delta)}</td>
        <td class="vol-cell">${fmtNum(row.puts_vol)}</td>
        <td class="iv-cell">${fmtIv(row.puts_iv)}</td>
      </tr>
    `;
  }

  html += `</tbody></table></div>`;
  
  // Create or update tooltip container
  let tt = document.getElementById('heatmap-tooltip');
  if (!tt) {
    tt = document.createElement('div');
    tt.id = 'heatmap-tooltip';
    tt.className = 'hm-tooltip';
    document.body.appendChild(tt);
  }

  container.innerHTML = html;

  // Add hover events
  const rows = container.querySelectorAll('.hm-row');
  rows.forEach(r => {
    r.addEventListener('mousemove', (e) => {
      const dataStr = r.getAttribute('data-tt');
      if (!dataStr) return;
      const d = JSON.parse(decodeURIComponent(dataStr));
      
      tt.innerHTML = `
        <div class="tt-header">STRIKE: ${fmtStrike(d.strike)} <span style="float:right">${fmtDist(d.distance_from_spot_pct)}</span></div>
        <div class="tt-body">
          <div class="tt-col call">
            <div class="tt-title">CALL SIDE</div>
            <div>OI: ${fmtNum(d.calls_oi)}</div>
            <div>Δ24h: ${fmtDelta(d.calls_oi_delta)}</div>
            <div>Vol: ${fmtNum(d.calls_vol)}</div>
            <div>IV: ${fmtIv(d.calls_iv)}</div>
            <div>Gamma: ${d.calls_gamma ? d.calls_gamma.toExponential(2) : 0}</div>
          </div>
          <div class="tt-col put">
            <div class="tt-title">PUT SIDE</div>
            <div>OI: ${fmtNum(d.puts_oi)}</div>
            <div>Δ24h: ${fmtDelta(d.puts_oi_delta)}</div>
            <div>Vol: ${fmtNum(d.puts_vol)}</div>
            <div>IV: ${fmtIv(d.puts_iv)}</div>
            <div>Gamma: ${d.puts_gamma ? d.puts_gamma.toExponential(2) : 0}</div>
          </div>
        </div>
      `;
      tt.style.display = 'block';
      // Adjust position
      let left = e.pageX + 15;
      let top = e.pageY + 15;
      // boundary check
      if (left + 250 > window.innerWidth) left = e.pageX - 260;
      if (top + 150 > window.innerHeight) top = e.pageY - 160;
      tt.style.left = left + 'px';
      tt.style.top = top + 'px';
    });
    r.addEventListener('mouseleave', () => {
      tt.style.display = 'none';
    });
  });
}

function getDeltaClass(val) {
  if (val > 50) return 'delta-extreme';
  if (val > 15) return 'delta-high';
  if (val < -15) return 'delta-neg';
  return '';
}

function fmtNum(n) {
  if (!n || n === 0) return '—';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toFixed(1);
}

function fmtIv(iv) {
  if (!iv) return '—';
  return (iv * 100).toFixed(1) + '%';
}

function fmtStrike(s) {
  if (!s) return '—';
  if (s >= 1000) return (s / 1000).toFixed(0) + 'K';
  return s;
}

function fmtDelta(d) {
  if (!d) return '—';
  const sign = d > 0 ? '+' : '';
  return sign + d.toFixed(1) + '%';
}

function fmtDist(d) {
  if (!d) return '0%';
  const sign = d > 0 ? '+' : '';
  return sign + d.toFixed(2) + '%';
}
