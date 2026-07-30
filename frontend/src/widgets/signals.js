/**
 * Panel 6: Signals — эвристические торговые сигналы.
 */

export function renderSignals(container, signals) {
  if (!signals || !signals.length) {
    container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div></div>';
    return;
  }

  let html = '<div class="signals-list">';
  for (const s of signals) {
    html += `
      <div class="signal-card ${s.bias}">
        <span class="signal-icon">${s.icon}</span>
        <div class="signal-info">
          <div class="signal-name">${s.name}</div>
          <div class="signal-desc">${s.description}</div>
        </div>
      </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}
