/* Glucose page — Dexcom Clarity export data (window.GLUCOSE_DATA).
   Sections: window stats pills, Time in Range, AGP chart, day explorer
   with insulin / meal markers, and an hour-by-day heatmap. */
(function (root) {
  const D = root.GLUCOSE_DATA || null;
  let curWin = '30';
  let curDay = null;

  const $ = id => document.getElementById(id);
  const fmtD = s => new Date(s + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const fmtDs = s => new Date(s + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

  function statsHTML(w) {
    const s = D.windows[w];
    const pill = (cls, tag, val, unit, meta) =>
      `<div class="bp-pill ${cls}"><div class="bp-pill-tag">${tag}</div>`
      + `<div class="bp-pill-value">${val} <span class="bp-pill-unit">${unit}</span></div>`
      + `<div class="bp-pill-meta">${meta}</div></div>`;
    return `<div class="g-pills">`
      + pill('g-p-avg', 'Average glucose', s.avg, 'mg/dL', `${fmtDs(D.range.end)} window · ${s.days} days`)
      + pill('g-p-gmi', 'GMI · est. A1c', s.gmi + '%', '', 'from average glucose')
      + pill('g-p-tir', 'Time in range', s.tir.in_range + '%', '', '70–180 mg/dL')
      + pill('g-p-cv', 'Variability · CV', s.cv + '%', '', 'target ≤ 36%')
      + pill('g-p-wear', 'Sensor wear', s.wear_pct + '%', '', `${s.readings.toLocaleString('en-US')} readings`)
      + `</div>`;
  }

  function tirHTML(w) {
    const t = D.windows[w].tir;
    const bands = [
      ['very_low', 'Very low', '<54', '#2563eb'],
      ['low', 'Low', '54–69', '#38bdf8'],
      ['in_range', 'In range', '70–180', '#22c55e'],
      ['high', 'High', '181–250', '#f59e0b'],
      ['very_high', 'Very high', '>250', '#ef4444']
    ];
    const bar = bands.map(([k, , , c]) =>
      `<div class="tir-seg" style="width:${t[k]}%;background:${c};" title="${t[k]}%"></div>`).join('');
    const pills = bands.map(([k, label, range, c]) =>
      `<div class="tir-pill" style="background:${c};"><div class="tir-pill-tag">${label} · ${range}</div>`
      + `<div class="tir-pill-val">${t[k]}%</div>`
      + `<div class="tir-pill-meta">${(t[k] * 24 / 100).toFixed(1)} h/day</div></div>`).join('');
    return `<div class="g-card"><div class="g-head">🎯 Time in Range · last ${w} days</div>`
      + `<div class="tir-bar">${bar}</div><div class="tir-pills">${pills}</div>`
      + `<div class="bp-method">Share of readings in each band. The usual goal is more than 70% of the day in range, with under 4% below 70.</div></div>`;
  }

  /* ---- shared SVG helpers ---- */
  const W = 1000, H = 340, PL = 44, PR = 10, PT = 14, PB = 30;
  const xOf = mins => PL + (mins / 1440) * (W - PL - PR);
  const yOf = (v, max) => PT + (1 - (v - 40) / (max - 40)) * (H - PT - PB);
  function frame(max, label) {
    let g = `<rect x="${PL}" y="${yOf(180, max)}" width="${W - PL - PR}" height="${yOf(70, max) - yOf(180, max)}" fill="rgba(34,197,94,.10)"/>`;
    [40, 70, 120, 180, 250, max].filter((v, i, a) => a.indexOf(v) === i && v <= max).forEach(v => {
      g += `<line x1="${PL}" y1="${yOf(v, max)}" x2="${W - PR}" y2="${yOf(v, max)}" stroke="rgba(149,177,199,${v === 70 || v === 180 ? '.55' : '.22'})" stroke-width="1"/>`
        + `<text x="${PL - 6}" y="${yOf(v, max) + 4}" text-anchor="end" class="svg-lab">${v}</text>`;
    });
    [0, 360, 720, 1080, 1440].forEach(m => {
      const lab = m === 0 || m === 1440 ? '12a' : m === 360 ? '6a' : m === 720 ? '12p' : '6p';
      g += `<text x="${xOf(m)}" y="${H - 8}" text-anchor="middle" class="svg-lab">${lab}</text>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" class="g-svg" role="img" aria-label="${label}">${g}`;
  }
  const pts = arr => arr.map(p => p.join(',')).join(' ');

  function agpSVG(w) {
    const bins = (D.agp[w] || []).filter(b => b);
    if (!bins.length) return '';
    const max = 300;
    const X = b => { const [h, m] = b.t.split(':').map(Number); return xOf(h * 60 + m); };
    const band = (lo, hi, fill) => {
      const top = bins.map(b => [X(b), yOf(b[hi], max)]);
      const bot = bins.slice().reverse().map(b => [X(b), yOf(b[lo], max)]);
      return `<polygon points="${pts(top.concat(bot))}" fill="${fill}"/>`;
    };
    let s = frame(max, 'Ambulatory glucose profile');
    s += band('p10', 'p90', 'rgba(56,189,248,.16)');
    s += band('p25', 'p75', 'rgba(56,189,248,.32)');
    s += `<polyline points="${pts(bins.map(b => [X(b), yOf(b.med, max)]))}" fill="none" stroke="#38bdf8" stroke-width="2.6"/>`;
    s += `<text x="${W - PR}" y="${PT + 6}" text-anchor="end" class="svg-note">median line · inner band 25–75% · outer band 10–90% · green = target 70–180</text>`;
    return s + '</svg>';
  }

  function daySVG(date) {
    const tr = D.traces[date];
    if (!tr) return '';
    const max = 350;
    let s = frame(max, 'Glucose trace for ' + date);
    const line = tr.g.map(([t, v]) => { const [h, m] = t.split(':').map(Number); return [xOf(h * 60 + m), yOf(v, max)]; });
    s += `<polyline points="${pts(line)}" fill="none" stroke="#38bdf8" stroke-width="2"/>`;
    (tr.ins || []).forEach(([t, u, sub]) => {
      const [h, m] = t.split(':').map(Number); const x = xOf(h * 60 + m);
      s += `<path d="M ${x} ${PT + 2} l 5 8 h -10 z" fill="#f0a43a"><title>Insulin ${u}u ${sub} at ${t}</title></path>`;
    });
    (tr.meal || []).forEach(([t, g]) => {
      const [h, m] = t.split(':').map(Number); const x = xOf(h * 60 + m);
      s += `<path d="M ${x} ${PT + 14} l 5 5 l -5 5 l -5 -5 z" fill="#22c55e"><title>Meal ${g} g carbs at ${t}</title></path>`;
    });
    s += `<text x="${W - PR}" y="${PT + 6}" text-anchor="end" class="svg-note">▼ insulin (amber) · ◆ meal (green) — hover a marker for detail</text>`;
    return s + '</svg>';
  }

  function dayStats(date) {
    const sum = D.days.find(d => d.date === date);
    const tr = D.traces[date] || {};
    const ins = (tr.ins || []).reduce((s, e) => s + e[1], 0);
    const carbs = (tr.meal || []).reduce((s, e) => s + e[1], 0);
    if (!sum) return '';
    return `<div class="bp-pill-meta day-meta">Average <b>${sum.avg}</b> mg/dL · in range <b>${sum.tir}%</b> · low–high <b>${sum.min}–${sum.max}</b>`
      + (ins ? ` · insulin <b>${ins.toFixed(1)} u</b>` : '') + (carbs ? ` · carbs logged <b>${Math.round(carbs)} g</b>` : '')
      + ` · ${sum.n} readings</div>`;
  }

  function heatHTML() {
    const color = v => v == null ? 'rgba(148,177,199,.07)' : v < 54 ? '#1d4ed8' : v < 70 ? '#38bdf8'
      : v <= 140 ? '#22c55e' : v <= 180 ? '#84cc16' : v <= 250 ? '#f59e0b' : '#ef4444';
    const rows = D.days.slice().reverse().map(d => {
      const cells = d.heat.map((v, h) =>
        `<div class="hm-cell" style="background:${color(v)};" title="${fmtDs(d.date)}, ${h}:00 — ${v == null ? 'no data' : 'avg ' + v + ' mg/dL'}"></div>`).join('');
      return `<div class="hm-row"><span class="hm-date">${fmtDs(d.date)}</span>${cells}</div>`;
    }).join('');
    return `<div class="g-card"><div class="g-head">🌡️ Every Day × Hour · average glucose</div>`
      + `<div class="hm-wrap">${rows}</div>`
      + `<div class="bp-method">Each cell is one hour's average. <span class="hm-key" style="background:#1d4ed8"></span> &lt;54 `
      + `<span class="hm-key" style="background:#38bdf8"></span> 54–69 <span class="hm-key" style="background:#22c55e"></span> 70–140 `
      + `<span class="hm-key" style="background:#84cc16"></span> 141–180 <span class="hm-key" style="background:#f59e0b"></span> 181–250 `
      + `<span class="hm-key" style="background:#ef4444"></span> &gt;250. Hover any cell for its value.</div></div>`;
  }

  function renderWindow() {
    $('g-stats').innerHTML = statsHTML(curWin);
    $('g-tir').innerHTML = tirHTML(curWin);
    $('g-agp').innerHTML = `<div class="g-card"><div class="g-head">📈 Daily Pattern (AGP) · last ${curWin} days</div>${agpSVG(curWin)}`
      + `<div class="bp-method">Your typical day, all readings folded into 24 hours: the median line, the middle band where half your readings fall, and the wider band covering 80%.</div></div>`;
    document.querySelectorAll('.g-toggle button').forEach(b =>
      b.classList.toggle('active', b.getAttribute('data-win') === curWin));
  }
  function renderDay() {
    const dates = Object.keys(D.traces).sort();
    const sel = $('day-select');
    sel.innerHTML = dates.slice().reverse().map(d => `<option value="${d}">${fmtD(d)}</option>`).join('');
    sel.value = curDay;
    $('g-day-chart').innerHTML = daySVG(curDay);
    $('g-day-stats').innerHTML = dayStats(curDay);
    const i = dates.indexOf(curDay);
    $('day-prev').disabled = i <= 0;
    $('day-next').disabled = i >= dates.length - 1;
  }
  function init() {
    if (!D) {
      $('g-missing').style.display = 'block';
      return;
    }
    curDay = Object.keys(D.traces).sort().slice(-1)[0];
    document.querySelectorAll('.g-toggle button').forEach(b =>
      b.addEventListener('click', () => { curWin = b.getAttribute('data-win'); renderWindow(); }));
    $('day-select').addEventListener('change', e => { curDay = e.target.value; renderDay(); });
    $('day-prev').addEventListener('click', () => stepDay(-1));
    $('day-next').addEventListener('click', () => stepDay(1));
    renderWindow();
    renderDay();
    $('g-heat').innerHTML = heatHTML();
    $('g-source').textContent = `Source: ${D.source} (${fmtD(D.range.start)} – ${fmtD(D.range.end)}) · ${D.total_readings.toLocaleString('en-US')} readings · generated ${D.generated}. Refresh: export a fresh CSV from Clarity and ask Muse to rebuild.`;
  }
  function stepDay(d) {
    const dates = Object.keys(D.traces).sort();
    const i = dates.indexOf(curDay);
    const n = dates[i + d];
    if (n) { curDay = n; renderDay(); }
  }
  root.GlucoseView = { init, statsHTML, tirHTML, agpSVG, daySVG, heatHTML };
  if (typeof document !== 'undefined' && document.getElementById('g-stats')) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
