/* Calendar-month windows anchored to the latest recorded day, not the system clock. */
(function (root) {
  const iso = d => d.toISOString().slice(0, 10);
  const date = s => new Date(s + 'T00:00:00Z');
  function shiftMonths(d, months) {
    const result = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1));
    const last = new Date(Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0)).getUTCDate();
    result.setUTCDate(Math.min(d.getUTCDate(), last));
    return result;
  }
  function period(rows, start, endExclusive) {
    const end = new Date(endExclusive); end.setUTCDate(end.getUTCDate() - 1);
    const selected = rows.filter(r => r.date >= iso(start) && r.date < iso(endExclusive));
    return { start: iso(start), end: iso(end), rows: selected, count: selected.length,
      systolic: selected.length ? selected.reduce((s,r) => s + Number(r.systolic), 0) / selected.length : null,
      diastolic: selected.length ? selected.reduce((s,r) => s + Number(r.diastolic), 0) / selected.length : null };
  }
  function calculate(input) {
    const rows = input.filter(r => /^\d{4}-\d{2}-\d{2}$/.test(r.date) && Number(r.systolic) > 0 && Number(r.diastolic) > 0)
      .slice().sort((a,b) => a.date.localeCompare(b.date) || String(a.time).localeCompare(String(b.time)));
    if (!rows.length) return {earliest: {rows:[],count:0},previous: {rows:[],count:0},current:{rows:[],count:0}};
    const end = date(rows[rows.length - 1].date); end.setUTCDate(end.getUTCDate() + 1);
    const currentStart = shiftMonths(end, -3);
    const previousStart = shiftMonths(end, -6);
    return { earliest: period(rows, shiftMonths(end, -9), previousStart), previous: period(rows, previousStart, currentStart), current: period(rows, currentStart, end) };
  }
  const fmt = s => date(s).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});
  function render(el, result) {
    const {earliest:e,previous:p,current:c} = result;
    const meta = period => period.count
      ? `<div class="bp-pill-meta">${fmt(period.start)} – ${fmt(period.end)}</div>`
        + `<div class="bp-pill-meta">${period.count} readings · ${new Set(period.rows.map(r=>r.date)).size} days measured</div>`
        + `<div class="bp-pill-meta">Recorded ${fmt(period.rows[0].date)} – ${fmt(period.rows[period.rows.length-1].date)}</div>`
      : `<div class="bp-pill-meta">Awaiting data — upload readings for this period</div>`;
    const val = period => period.count
      ? `${Math.round(period.systolic)}/${Math.round(period.diastolic)} <span class="bp-pill-unit">mmHg</span>`
      : `Needs data`;
    const delta = (from, to, k) => { const v = to[k] - from[k]; return (v < 0 ? '↓ ' : v > 0 ? '↑ ' : '') + Math.abs(v).toFixed(1); };
    const deltaBody = (from, to) => (from.count && to.count)
      ? `<div class="bp-pill-delta-row">SYS ${delta(from, to, 'systolic')}</div><div class="bp-pill-delta-row">DIA ${delta(from, to, 'diastolic')}</div>`
        + `<div class="bp-pill-meta">mmHg · based on unrounded averages</div>`
      : `<div class="bp-pill-value">Awaiting data</div><div class="bp-pill-meta">Both periods need readings</div>`;
    el.innerHTML = `<div class="bp-pills">`
      + `<div class="bp-pill bp-pill-earliest"><div class="bp-pill-tag">Earliest 3 Months</div><div class="bp-pill-value">${val(e)}</div>${meta(e)}</div>`
      + `<div class="bp-pill bp-pill-prev"><div class="bp-pill-tag">Previous 3 Months</div><div class="bp-pill-value">${val(p)}</div>${meta(p)}</div>`
      + `<div class="bp-pill bp-pill-cur"><div class="bp-pill-tag">Current 3 Months</div><div class="bp-pill-value">${val(c)}</div>${meta(c)}</div>`
      + `</div>`
      + `<div class="bp-pills bp-pills-deltas">`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Previous − Earliest</div>${deltaBody(e, p)}</div>`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Current − Previous</div>${deltaBody(p, c)}</div>`
      + `</div>`
      + `<div class="bp-detail bp-method">All individual readings, equally weighted. Three non-overlapping calendar-month windows ending on the latest reading date. Unmeasured days are excluded. The trend toggle does not change this comparison.</div>`;
  }
  root.BPComparison = {calculate,render};
})(typeof window !== 'undefined' ? window : globalThis);
