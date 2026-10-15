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

  /* ---- insights (whole export) ---- */
  const bandColor = v => v == null ? '#5b7186' : v <= 140 ? '#22c55e' : v <= 180 ? '#84cc16' : v <= 250 ? '#f59e0b' : '#ef4444';

  function hourChartSVG() {
    const hs = D.insights.hourly;
    const max = 220, bw = (W - PL - PR) / 24;
    let s = `<svg viewBox="0 0 560 300" class="g-svg" role="img" aria-label="Average glucose by hour of day">`;
    const y = v => 250 - ((v - 40) / (max - 40)) * 210;
    s += `<rect x="40" y="${y(180)}" width="505" height="${y(70) - y(180)}" fill="rgba(34,197,94,.08)"/>`;
    s += `<line x1="40" y1="${y(180)}" x2="545" y2="${y(180)}" stroke="#f59e0b" stroke-width="1.4" stroke-dasharray="5 4"/>`
      + `<text x="545" y="${y(180) - 5}" text-anchor="end" class="svg-lab">180</text>`;
    hs.forEach(e => {
      if (e.mean == null) return;
      const x = 40 + e.h * bw;
      s += `<rect x="${x + 2}" y="${y(e.mean)}" width="${bw - 4}" height="${250 - y(e.mean)}" rx="3" fill="${bandColor(e.mean)}"><title>${e.h}:00 — avg ${e.mean} mg/dL, ${e.high}% above 180</title></rect>`;
      if (e.h % 3 === 0) s += `<text x="${x + bw / 2}" y="268" text-anchor="middle" class="svg-lab">${e.h === 0 ? '12a' : e.h === 12 ? '12p' : e.h < 12 ? e.h + 'a' : (e.h - 12) + 'p'}</text>`;
    });
    const peak = hs.reduce((a, b) => (b.mean > (a.mean || 0) ? b : a), {});
    s += `<text x="40" y="292" class="svg-note">Tallest bar = highest hour: ${peak.h}:00 averages ${peak.mean} mg/dL (${peak.high}% of readings above 180). Green band = target 70–180.</text>`;
    return s + '</svg>';
  }

  function blocksHTML() {
    const rows = D.insights.blocks.map(b => {
      const inW = Math.max(b.in, 0), hiW = Math.max(b.high, 0), loW = Math.max(b.low, 0);
      return `<div class="ins-row"><div class="ins-row-head"><span>${b.label}</span><b style="color:${bandColor(b.mean)};">avg ${b.mean}</b></div>`
        + `<div class="ins-track"><div style="width:${loW}%;background:#38bdf8;"></div><div style="width:${inW}%;background:#22c55e;"></div><div style="width:${hiW}%;background:#f59e0b;"></div></div>`
        + `<div class="ins-row-meta">${b.in}% in range · ${b.high}% above 180${b.low ? ` · ${b.low}% low` : ''}</div></div>`;
    }).join('');
    return `<div class="g-card"><div class="g-head g-head-ins">🕐 Where the Highs Live · parts of the day</div>${hourChartSVG()}${rows}</div>`;
  }

  function dawnHTML() {
    const d = D.insights.dawn, n = D.insights.night, l = D.insights.lows;
    return `<div class="ins-grid"><div class="g-card"><div class="g-head g-head-ins">🌅 Overnight &amp; Dawn</div>`
      + `<div class="dawn-row"><div class="bp-pill g-p-night"><div class="bp-pill-tag">Overnight low · 2–5 AM</div><div class="bp-pill-value">${d.nadir}</div><div class="bp-pill-meta">typical lowest point</div></div>`
      + `<div class="dawn-arrow">▲ +${d.rise}</div>`
      + `<div class="bp-pill g-p-morn"><div class="bp-pill-tag">Morning · 7–9 AM</div><div class="bp-pill-value">${d.morning}</div><div class="bp-pill-meta">the dawn rise, before breakfast</div></div></div>`
      + `<div class="bp-method">Overnight (12–6 AM) averages ${n.mean} with only ${n.lowPct}% low readings — nights run calm and steady.</div></div>`
      + `<div class="g-card"><div class="g-head g-head-ins">🛡️ Lows · all ${D.days.length} days</div>`
      + `<div class="dawn-row"><div class="bp-pill g-p-safe"><div class="bp-pill-tag">Below 70</div><div class="bp-pill-value">${l.n70}</div><div class="bp-pill-meta">readings · ${l.pct70}% of all</div></div>`
      + `<div class="bp-pill g-p-safe"><div class="bp-pill-tag">Below 54</div><div class="bp-pill-value">${l.n54}</div><div class="bp-pill-meta">readings</div></div>`
      + `<div class="bp-pill g-p-safe"><div class="bp-pill-tag">Lowest ever</div><div class="bp-pill-value">${l.min}</div><div class="bp-pill-meta">mg/dL</div></div></div>`
      + `<div class="bp-method">Lows are rare in this record — about ${D.insights.episodesPerDay} excursions above 180 per day happen far more often than any low.</div></div></div>`;
  }

  function mealBars(list, key, color, fmt) {
    const max = Math.max(...list.map(e => e[key] || 0), 1);
    return list.filter(e => e.n).map(e =>
      `<div class="bar-row"><span class="bar-lab">${e.label}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.round(100 * (e[key] || 0) / max)}%;background:${color};"></div></div><span class="bar-val">${fmt(e)}</span></div>`).join('');
  }
  function mealsHTML() {
    const m = D.insights.meals;
    return `<div class="g-card"><div class="g-head g-head-ins">🍽️ Meal Responses · ${m.n} logged meals</div>`
      + `<div class="bp-method" style="margin:0 0 10px;">Average meal: <b style="color:#eaf4fb;">${m.avgCarbs} g carbs → +${m.rise} rise → peak ${m.peak}</b>. ${m.over}% of meals still peak above 180 — mostly when the meal starts on top of an earlier high. Working carb ratio ≈ <b style="color:#eaf4fb;">${m.carbRatio} u per 10 g</b>.</div>`
      + `<div class="ins-sub">Rise after eating, by meal size</div>${mealBars(m.byCarbs, 'rise', '#38bdf8', e => `+${e.rise} · ${e.over}% over 180`)}`
      + `<div class="ins-sub">Rise after eating, by meal</div>${mealBars(m.bySlot, 'rise', '#f0a43a', e => `+${e.rise} · peak ${e.peak}`)}`
      + `<div class="bp-method">Bigger meals spike less often than mid-size ones (31% vs 61% over 180) — they get bigger doses. The 30–50 g meal is the one that most often outruns its insulin.</div></div>`;
  }

  function weeksSVG() {
    const ws = D.insights.weeks;
    const bw = (1000 - 50) / ws.length;
    const y = tir => 235 - (tir / 100) * 195;
    let s = `<svg viewBox="0 0 1000 300" class="g-svg" role="img" aria-label="Weekly time in range trend">`;
    s += `<line x1="40" y1="${y(70)}" x2="990" y2="${y(70)}" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="5 4"/><text x="990" y="${y(70) - 5}" text-anchor="end" class="svg-lab">70% goal</text>`;
    ws.forEach((w, i) => {
      const x = 40 + i * bw, c = w.tir >= 90 ? '#22c55e' : w.tir >= 85 ? '#4ade80' : w.tir >= 80 ? '#84cc16' : w.tir >= 75 ? '#f59e0b' : '#f97316';
      s += `<rect x="${x + 3}" y="${y(w.tir)}" width="${bw - 6}" height="${235 - y(w.tir)}" rx="4" fill="${c}"><title>Week of ${w.label}: TIR ${w.tir}%, avg ${w.mean}</title></rect>`
        + `<text x="${x + bw / 2}" y="${y(w.tir) - 6}" text-anchor="middle" class="svg-lab">${Math.round(w.tir)}</text>`
        + `<text x="${x + bw / 2}" y="255" text-anchor="middle" class="svg-lab">${w.label}</text>`;
    });
    s += `<text x="40" y="285" class="svg-note">Bar = % of the week in range (70–180). Best weeks: late Aug – early Sep (92–93%). Recent weeks hold ~84%.</text>`;
    return s + '</svg>';
  }

  function insulinHTML() {
    const i = D.insights.insulin;
    return `<div class="g-card"><div class="g-head g-head-ins">💉 Insulin · daily average, last 30 days</div>`
      + `<div class="dawn-row"><div class="bp-pill g-p-fast"><div class="bp-pill-tag">Fast-acting</div><div class="bp-pill-value">${i.fast} <span class="bp-pill-unit">u/day</span></div><div class="bp-pill-meta">meals + corrections</div></div>`
      + `<div class="bp-pill g-p-long"><div class="bp-pill-tag">Long-acting</div><div class="bp-pill-value">${i.long} <span class="bp-pill-unit">u/day</span></div><div class="bp-pill-meta">background basal</div></div>`
      + `<div class="bp-pill g-p-total"><div class="bp-pill-tag">Total</div><div class="bp-pill-value">${i.total} <span class="bp-pill-unit">u/day</span></div><div class="bp-pill-meta">from your Clarity log</div></div></div></div>`;
  }

  function insightsHTML() {
    if (!D.insights) return '';
    return blocksHTML() + dawnHTML() + mealsHTML()
      + `<div class="g-card"><div class="g-head g-head-ins">📅 Week by Week · time in range %</div>${weeksSVG()}</div>`
      + insulinHTML();
  }

  const TAKE_GREENS = [
    'linear-gradient(135deg,#4ade80,#16a34a)',
    'linear-gradient(135deg,#6ee7a0,#22c55e)',
    'linear-gradient(135deg,#86efb5,#34d399)',
    'linear-gradient(135deg,#a7f3c9,#4ade80)',
    'linear-gradient(135deg,#bbf7d0,#6ee7a7)',
    'linear-gradient(135deg,#d1fae0,#86efb5)'
  ];
  function takeHTML() {
    const o = D.opinion;
    if (!o) return '';
    const pill = (inner, detail, style) =>
      `<button type="button" class="take-pill" style="background:${style};" aria-expanded="false">`
      + `<span class="take-pill-line">${inner}<span class="take-chev">▾</span></span>`
      + `<span class="take-detail">${detail}</span></button>`;
    const pts = o.points.map((p, i) =>
      pill(`${p.icon} ${p.title}`, p.text, TAKE_GREENS[i % TAKE_GREENS.length])).join('');
    const sugs = o.suggestions.map(s =>
      pill(`💡 ${s.title}`, s.text, 'linear-gradient(135deg,#ffd081,#f0a43a)')).join('');
    return `<div class="g-card take-card">`
      + `<button type="button" class="take-toggle" id="take-toggle" aria-expanded="false"><span>🧠 Muse's Take · data through ${fmtD(o.asOf)}</span><span class="take-chev">▾</span></button>`
      + `<div class="take-body" id="take-body" hidden>`
      + `<div class="take-headline">${o.headline}</div>${pts}`
      + (sugs ? `<div class="take-sug-head">Worth trying / discussing</div>${sugs}` : '')
      + `<div class="bp-method">Pills are ordered most important first — the deepest green is the biggest story. Tap any pill to open its details, tap again to close. A logical read of your own logged data, recomputed at every refresh; patterns to discuss with Dr. Munir — not dosing instructions. Best week so far: week of ${o.bestWeek.label}, ${o.bestWeek.tir}% in range.</div>`
      + `</div></div>`;
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
    $('g-take').innerHTML = takeHTML();
    const takeSec = $('g-take');
    const toggle = document.getElementById('take-toggle');
    if (toggle) toggle.addEventListener('click', () => {
      const body = document.getElementById('take-body');
      const open = body.hidden;
      body.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      toggle.classList.toggle('open', open);
    });
    takeSec.querySelectorAll('.take-pill').forEach(p => p.addEventListener('click', () => {
      const open = p.classList.toggle('open');
      p.setAttribute('aria-expanded', String(open));
    }));
    renderWindow();
    $('g-insights').innerHTML = insightsHTML();
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
  root.GlucoseView = { init, statsHTML, tirHTML, agpSVG, daySVG, heatHTML, insightsHTML, takeHTML };
  if (typeof document !== 'undefined' && document.getElementById('g-stats')) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
