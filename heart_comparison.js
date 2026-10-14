/* Heart rate & HRV shown in the same three windows as the BP comparison.
   Data: window.HEART_DATA — daily Apple Health exports {date, hr, hrv}. */
(function (root) {
  function avg(rows, k) {
    const vals = rows.map(r => Number(r[k])).filter(v => v > 0);
    return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
  }
  function summarize(rows, period) {
    const sel = rows.filter(r => r.date >= period.start && r.date <= period.end);
    return { period, rows: sel, count: sel.length, hrv: avg(sel, 'hrv'), hr: avg(sel, 'hr') };
  }
  function calculate(heartRows, comparison) {
    const rows = (heartRows || []).filter(r => /^\d{4}-\d{2}-\d{2}$/.test(r.date))
      .slice().sort((a, b) => a.date.localeCompare(b.date));
    return {
      earliest: summarize(rows, comparison.earliest),
      previous: summarize(rows, comparison.previous),
      current: summarize(rows, comparison.current)
    };
  }
  const fmt = s => new Date(s + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  function render(el, heartRows, comparison) {
    if (!el || !comparison || !comparison.earliest) return;
    const { earliest: e, previous: p, current: c } = calculate(heartRows, comparison);
    const meta = s => s.count
      ? `<div class="bp-pill-meta">${fmt(s.period.start)} – ${fmt(s.period.end)}</div>`
        + `<div class="bp-pill-meta">${s.count} days measured</div>`
        + `<div class="bp-pill-meta">Recorded ${fmt(s.rows[0].date)} – ${fmt(s.rows[s.rows.length - 1].date)}</div>`
      : `<div class="bp-pill-meta">Awaiting data — no Apple Health days in this period yet</div>`;
    const val = s => s.count
      ? `${s.hrv.toFixed(1)} <span class="bp-pill-unit">ms HRV</span>`
      : `Needs data`;
    const hrLine = s => s.count ? `<div class="bp-pill-meta">Heart rate avg ${Math.round(s.hr)} bpm</div>` : '';
    const delta = (from, to, k) => { const v = to[k] - from[k]; return (v < 0 ? '↓ ' : v > 0 ? '↑ ' : '') + Math.abs(v).toFixed(1); };
    const deltaBody = (from, to) => (from.count && to.count)
      ? `<div class="bp-pill-delta-row">HRV ${delta(from, to, 'hrv')} ms</div><div class="bp-pill-delta-row">HR ${delta(from, to, 'hr')} bpm</div>`
        + `<div class="bp-pill-meta">based on unrounded daily averages</div>`
      : `<div class="bp-pill-value">Awaiting data</div><div class="bp-pill-meta">Both periods need Apple Health days</div>`;
    el.innerHTML = `<div class="heart-head">Heart Rate &amp; HRV · Apple Health</div>`
      + `<div class="bp-pills">`
      + `<div class="bp-pill bp-pill-h-e"><div class="bp-pill-tag">Earliest 3 Months</div><div class="bp-pill-value">${val(e)}</div>${hrLine(e)}${meta(e)}</div>`
      + `<div class="bp-pill bp-pill-h-p"><div class="bp-pill-tag">Previous 3 Months</div><div class="bp-pill-value">${val(p)}</div>${hrLine(p)}${meta(p)}</div>`
      + `<div class="bp-pill bp-pill-h-c"><div class="bp-pill-tag">Current 3 Months</div><div class="bp-pill-value">${val(c)}</div>${hrLine(c)}${meta(c)}</div>`
      + `</div>`
      + `<div class="bp-pills bp-pills-deltas">`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Previous − Earliest</div>${deltaBody(e, p)}</div>`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Current − Previous</div>${deltaBody(p, c)}</div>`
      + `</div>`
      + `<div class="bp-detail bp-method">Daily Apple Health values, equally weighted, in the same three windows as the blood pressure comparison above. HRV is heart rate variability (SDNN) in milliseconds; HR is each day's average heart rate. The pulse on each BP reading stays with that reading above.</div>`;
  }
  root.HeartComparison = { calculate, render };
})(typeof window !== 'undefined' ? window : globalThis);
