import re

def main():
    with open('frontend/src/pages/manual_trading.js', 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace _updateDecisionCard body lines
    old_card_fields = '''        <div class="mt-dm-row">
          <span class="mt-dm-label">nearest_level</span>
          <span class="mt-dm-val">${liveNearest ? this._fmtETH(liveNearest) : '—'} ${ctxBadge}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">level_result</span>
          <span class="mt-dm-val">${this._esc(liveLevelResult || '—')} <span style="color:var(--text-dim);font-size:8px">(${this._esc(liveLevelSide || '—')})</span></span>
        </div>'''

    new_card_fields = '''        <div class="mt-dm-row">
          <span class="mt-dm-label">support</span>
          <span class="mt-dm-val">${liveCtx.live_support_level ? this._fmtETH(liveCtx.live_support_level) + ' Δ' + liveCtx.live_support_distance_pct.toFixed(2) + '%' : '—'} <span style="color:var(--text-dim);font-size:8px">(${this._esc(liveCtx.live_support_source || '')})</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">resistance</span>
          <span class="mt-dm-val">${liveCtx.live_resistance_level ? this._fmtETH(liveCtx.live_resistance_level) + ' Δ' + liveCtx.live_resistance_distance_pct.toFixed(2) + '%' : '—'} <span style="color:var(--text-dim);font-size:8px">(${this._esc(liveCtx.live_resistance_source || '')})</span></span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">primary_live</span>
          <span class="mt-dm-val">${liveCtx.primary_live_level ? this._esc(liveCtx.primary_live_side) + ' ' + this._fmtETH(liveCtx.primary_live_level) + ' Δ' + liveCtx.primary_live_distance_pct.toFixed(2) + '%' : '—'}</span>
        </div>
        <div class="mt-dm-row">
          <span class="mt-dm-label">selected_for_setup</span>
          <span class="mt-dm-val">${manual.selected_setup_level ? this._esc(manual.selected_setup_side) + ' ' + this._fmtETH(manual.selected_setup_level) : '—'}</span>
        </div>'''

    content = content.replace(old_card_fields, new_card_fields)

    old_log = '''      source_snapshot_id: levelCtx.live_source_snapshot_id,
      source_event_id: levelCtx.live_source_event_id,
      live_source_snapshot_sequence_id: levelCtx.live_source_snapshot_sequence_id
    });'''

    new_log = '''      source_snapshot_id: levelCtx.live_source_snapshot_id,
      source_event_id: levelCtx.live_source_event_id,
      live_source_snapshot_sequence_id: levelCtx.live_source_snapshot_sequence_id,
      live_support_level: levelCtx.live_support_level,
      live_support_distance_pct: levelCtx.live_support_distance_pct,
      live_support_source: levelCtx.live_support_source,
      live_support_result: levelCtx.live_support_result,
      live_support_type: levelCtx.live_support_type,
      live_resistance_level: levelCtx.live_resistance_level,
      live_resistance_distance_pct: levelCtx.live_resistance_distance_pct,
      live_resistance_source: levelCtx.live_resistance_source,
      live_resistance_result: levelCtx.live_resistance_result,
      live_resistance_type: levelCtx.live_resistance_type,
      primary_live_level: levelCtx.primary_live_level,
      primary_live_side: levelCtx.primary_live_side,
      primary_live_distance_pct: levelCtx.primary_live_distance_pct,
      primary_live_source: levelCtx.primary_live_source,
      primary_live_result: levelCtx.primary_live_result,
      selected_setup_level: manual.selected_setup_level,
      selected_setup_side: manual.selected_setup_side,
      selected_setup_level_source: manual.selected_setup_level_source,
      selected_setup_level_distance_pct: manual.selected_setup_level_distance_pct,
      selected_setup_level_result: manual.selected_setup_level_result,
      selected_setup_level_type: manual.selected_setup_level_type,
      setup_side_required: manual.setup_side_required
    });'''

    content = content.replace(old_log, new_log)

    old_watch_head = '''                <th class="th-price">ЦЕНА / УРОВЕНЬ</th>
                <th class="th-conf">ПОДТВЕРЖДЕНИЕ</th>
                <th>ОТМЕНА</th>
              </tr>'''

    new_watch_head = '''                <th class="th-price">ЦЕНА</th>
                <th class="th-ctx">SUPPORT</th>
                <th class="th-ctx">RESIST</th>
                <th class="th-ctx">SELECTED</th>
                <th class="th-conf">ПОДТВЕРЖДЕНИЕ</th>
                <th>ОТМЕНА</th>
              </tr>'''

    content = content.replace(old_watch_head, new_watch_head)

    # Now let's patch the watchlist body
    old_watch_body = '''            const lvlFmt = r.nearest_level ? `${fmt(r.nearest_level)}<br/><span style="color:var(--text-dim);font-size:10px">${r.level_side||''}</span>` : '—';
            const priceHtml = `
              <div style="font-weight:600;font-size:13px">${fmt(r.price)}</div>
              <div style="font-size:11px;color:var(--text-dim);margin-top:2px">${lvlFmt}</div>
            `;
            html += `
              <tr class="${statusCls}">
                <td class="td-time">${hhmm}</td>
                <td class="td-sym"><div class="badge-setup">${this._esc(r.setup_type || 'NONE')}</div></td>
                <td class="td-price">${priceHtml}</td>
                <td class="td-bias"><div class="${biasCls}">${this._esc(r.manual_bias || '—')}</div></td>
                <td class="td-conf">${shortConf}</td>
                <td class="td-inval">${r.invalidation_level ? fmt(r.invalidation_level) : '—'}</td>
              </tr>
            `;'''

    new_watch_body = '''            const supportFmt = r.live_support_level ? `${fmt(r.live_support_level)}<br/><span style="color:var(--text-dim);font-size:10px">Δ${r.live_support_distance_pct?.toFixed(2)}%</span>` : '—';
            const resistFmt = r.live_resistance_level ? `${fmt(r.live_resistance_level)}<br/><span style="color:var(--text-dim);font-size:10px">Δ${r.live_resistance_distance_pct?.toFixed(2)}%</span>` : '—';
            const selSide = String(r.selected_setup_side || '').substring(0, 3).toUpperCase();
            const selFmt = r.selected_setup_level ? `${selSide} ${fmt(r.selected_setup_level)}` : '—';

            const priceHtml = `<div style="font-weight:600;font-size:13px">${fmt(r.price)}</div>`;
            
            html += `
              <tr class="${statusCls}">
                <td class="td-time">${hhmm}</td>
                <td class="td-sym">
                  <div class="badge-setup">${this._esc(r.setup_type || 'NONE')}</div>
                  <div class="${biasCls}" style="margin-top:4px">${this._esc(r.manual_bias || '—')}</div>
                </td>
                <td class="td-price">${priceHtml}</td>
                <td class="td-ctx" style="text-align:right; font-size:11px; line-height:1.2;">${supportFmt}</td>
                <td class="td-ctx" style="text-align:right; font-size:11px; line-height:1.2;">${resistFmt}</td>
                <td class="td-ctx" style="text-align:right; font-size:11px; line-height:1.2;">${selFmt}</td>
                <td class="td-conf">${shortConf}</td>
                <td class="td-inval">${r.invalidation_level ? fmt(r.invalidation_level) : '—'}</td>
              </tr>
            `;'''

    # Watchlist columns were rearranged a bit differently, let's look at the old body
    # old columns:
    # time, sym(setup_type), price+nearest_level, bias, conf, inval
    # new columns:
    # time, sym(setup_type + bias below it to save space?), price, support, resist, selected, conf, inval
    
    # Wait, let's see how headers actually were.
    # old headers: 
    # <th class="th-time">TIME</th>
    # <th class="th-sym">СЕТАП</th>
    # <th class="th-price">ЦЕНА / УРОВЕНЬ</th>
    # <th class="th-bias">ПОТОК</th> -> bias
    # <th class="th-conf">ПОДТВЕРЖДЕНИЕ</th>
    # <th>ОТМЕНА</th>
    
    # Wait, I'll regex replace to make it safer
    content = re.sub(
        r'<th class="th-price">ЦЕНА / УРОВЕНЬ</th>\s*<th class="th-bias">.*?</th>\s*<th class="th-conf">ПОДТВЕРЖДЕНИЕ</th>',
        '<th class="th-price">ЦЕНА</th>\n                <th class="th-ctx">SUPPORT</th>\n                <th class="th-ctx">RESIST</th>\n                <th class="th-ctx">SELECTED</th>\n                <th class="th-conf">ПОДТВЕРЖДЕНИЕ</th>',
        content,
        flags=re.DOTALL
    )

    # Let's see how body was.
    body_pattern = r'const lvlFmt = r\.nearest_level \?.*?</tr>\s*`;'
    
    content = re.sub(
        body_pattern,
        new_watch_body,
        content,
        flags=re.DOTALL
    )

    with open('frontend/src/pages/manual_trading.js', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
