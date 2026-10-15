/* Activity, sleep, resting heart rate and glucose from Apple Health.
   Data: window.ACTIVITY_DATA — daily {date, steps, active, exercise, dist,
   resting, glucose, gmin, gmax}; window.SLEEP_DATA — per-night
   {date, total, deep, rem, core, awake, eff, awakenings} (minutes).
   Windows: the same three calendar-month cycles as the BP comparison,
   anchored to the latest recorded day in the activity data. */
(function (root) {
  const iso = d => d.toISOString().slice(0, 10);
  const date = s => new Date(s + 'T00:00:00Z');
  function shiftMonths(d, months) {
    const result = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1));
    const last = new Date(Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0)).getUTCDate();
    result.setUTCDate(Math.min(d.getUTCDate(), last));
    return result;
  }
  function windows(rows) {
    const sorted = (rows || []).filter(r => /^\d{4}-\d{2}-\d{2}$/.test(r.date))
      .slice().sort((a, b) => a.date.localeCompare(b.date));
    if (!sorted.length) return null;
    const end = date(sorted[sorted.length - 1].date); end.setUTCDate(end.getUTCDate() + 1);
    const cur = shiftMonths(end, -3), prev = shiftMonths(end, -6), ear = shiftMonths(end, -9);
    const mk = (start, endEx) => {
      const e = new Date(endEx); e.setUTCDate(e.getUTCDate() - 1);
      return { start: iso(start), end: iso(e) };
    };
    return { earliest: mk(ear, prev), previous: mk(prev, cur), current: mk(cur, end) };
  }
  function avg(rows, k) {
    const vals = rows.map(r => Number(r[k])).filter(v => v > 0);
    return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
  }
  function summarize(rows, period, keys) {
    const sel = rows.filter(r => r.date >= period.start && r.date <= period.end);
    const out = { period, rows: sel, count: sel.length };
    keys.forEach(k => { out[k] = avg(sel, k); });
    return out;
  }
  const fmtD = s => date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const fmtDur = mins => {
    if (mins == null) return 'Needs data';
    const m = Math.round(mins), h = Math.floor(m / 60), r = m % 60;
    return h ? `${h}h ${String(r).padStart(2, '0')}m` : `${r}m`;
  };
  const fmtInt = v => v == null ? 'Needs data' : Math.round(v).toLocaleString('en-US');
  const meta = (s, unit) => s.count
    ? `<div class="bp-pill-meta">${fmtD(s.period.start)} – ${fmtD(s.period.end)}</div>`
      + `<div class="bp-pill-meta">${s.count} ${unit} measured</div>`
      + `<div class="bp-pill-meta">Recorded ${fmtD(s.rows[0].date)} – ${fmtD(s.rows[s.rows.length - 1].date)}</div>`
    : `<div class="bp-pill-meta">Awaiting data — no Apple Health days in this period yet</div>`;
  const delta = (from, to, k, fmt) => {
    if (from[k] == null || to[k] == null) return null;
    const v = to[k] - from[k];
    return (v < 0 ? '↓ ' : v > 0 ? '↑ ' : '') + fmt(Math.abs(v));
  };
  function section(el, cfg) {
    if (!el) return;
    const w = cfg.windows;
    const e = summarize(cfg.rows, w.earliest, cfg.keys);
    const p = summarize(cfg.rows, w.previous, cfg.keys);
    const c = summarize(cfg.rows, w.current, cfg.keys);
    const pill = (s, cls, tag) => `<div class="bp-pill ${cls}"><div class="bp-pill-tag">${tag}</div>`
      + `<div class="bp-pill-value">${s.count ? cfg.big(s) : 'Needs data'}</div>`
      + (s.count ? cfg.lines(s) : '') + meta(s, cfg.unit) + `</div>`;
    const deltaBody = (from, to) => (from.count && to.count)
      ? cfg.deltas.map(d => {
          const v = delta(from, to, d.k, d.fmt);
          return v == null ? '' : `<div class="bp-pill-delta-row">${d.label} ${v}${d.unit || ''}</div>`;
        }).join('') + `<div class="bp-pill-meta">based on unrounded daily averages</div>`
      : `<div class="bp-pill-value">Awaiting data</div><div class="bp-pill-meta">Both periods need Apple Health days</div>`;
    el.innerHTML = `<div class="heart-head">${cfg.title}</div>`
      + `<div class="bp-pills">`
      + pill(e, 'bp-pill-earliest', 'Earliest 3 Months')
      + pill(p, 'bp-pill-prev', 'Previous 3 Months')
      + pill(c, 'bp-pill-cur', 'Current 3 Months')
      + `</div>`
      + `<div class="bp-pills bp-pills-deltas">`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Previous − Earliest</div>${deltaBody(e, p)}</div>`
      + `<div class="bp-pill bp-pill-delta"><div class="bp-pill-tag">Change · Current − Previous</div>${deltaBody(p, c)}</div>`
      + `</div>`
      + `<div class="bp-detail bp-method">${cfg.method}</div>`;
  }
  function render(root2) {
    const act = (root.ACTIVITY_DATA || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    const slp = (root.SLEEP_DATA || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    const w = windows(act);
    if (!w) {
      const el = root2.document.getElementById('activity-comparison');
      if (el) el.innerHTML = `<div class="heart-head">👟 Activity · Apple Health</div>`
        + `<div class="bp-detail bp-method" style="background:rgba(220,137,32,.16); border:1px solid rgba(220,137,32,.4); border-radius:12px; padding:14px; font-weight:700;">`
        + `Activity data files are not on this Mac yet. Save <b>activity_data.js</b> and <b>sleep_data.js</b> `
        + `(sent by Muse in chat) into the Health Manager folder — the same folder as heart_data.js — then refresh this page.</div>`;
      return;
    }
    const line = (label, val) => `<div class="bp-pill-meta">${label} ${val}</div>`;
    section(root2.document.getElementById('activity-comparison'), {
      title: '👟 Activity · Apple Health', rows: act, windows: w, unit: 'days',
      keys: ['steps', 'exercise', 'active', 'dist'],
      big: s => `${fmtInt(s.steps)} <span class="bp-pill-unit">steps / day</span>`,
      lines: s => line('Exercise', `${fmtInt(s.exercise)} min/day`)
        + line('Active energy', `${fmtInt(s.active)} kcal/day`)
        + line('Distance', `${(s.dist / 1000).toFixed(1)} km/day`),
      deltas: [
        { k: 'steps', label: 'Steps', fmt: v => Math.round(v).toLocaleString('en-US') },
        { k: 'exercise', label: 'Exercise', fmt: v => v.toFixed(0), unit: ' min' }
      ],
      method: 'Daily Apple Health totals, equally weighted, in the same three windows as the blood pressure comparison. Days the watch was not worn are excluded.'
    });
    section(root2.document.getElementById('sleep-comparison'), {
      title: '😴 Sleep · Apple Health', rows: slp, windows: w, unit: 'nights',
      keys: ['total', 'deep', 'rem', 'eff', 'awakenings'],
      big: s => `${fmtDur(s.total)} <span class="bp-pill-unit">avg sleep</span>`,
      lines: s => line('Deep', fmtDur(s.deep)) + line('REM', fmtDur(s.rem))
        + line('Efficiency', s.eff != null ? `${s.eff.toFixed(0)}%` : '—')
        + line('Awakenings', s.awakenings != null ? s.awakenings.toFixed(1) : '—'),
      deltas: [
        { k: 'total', label: 'Sleep', fmt: v => fmtDur(v) },
        { k: 'eff', label: 'Efficiency', fmt: v => v.toFixed(1), unit: '%' }
      ],
      method: 'Sleep as recorded by Apple Watch, nights running noon to noon (an afternoon nap counts into that night). Nights under one hour are excluded, and nights the watch was not worn do not appear — so short averages mean short recorded sleep, not missing weeks.'
    });
    section(root2.document.getElementById('resting-comparison'), {
      title: '💓 Resting Heart Rate · Apple Health', rows: act, windows: w, unit: 'days',
      keys: ['resting'],
      big: s => `${fmtInt(s.resting)} <span class="bp-pill-unit">bpm resting</span>`,
      lines: s => {
        const vals = s.rows.map(r => Number(r.resting)).filter(v => v > 0);
        return vals.length ? line('Lowest–highest daily', `${Math.min(...vals)}–${Math.max(...vals)} bpm`) : '';
      },
      deltas: [{ k: 'resting', label: 'Resting HR', fmt: v => v.toFixed(1), unit: ' bpm' }],
      method: 'Apple Health resting heart rate (the watch measures it during rest). The all-day average heart rate and HRV live on the BP page.'
    });
    section(root2.document.getElementById('glucose-comparison'), {
      title: '🩸 Blood Glucose · Apple Health', rows: act, windows: w, unit: 'days',
      keys: ['glucose', 'gmin', 'gmax'],
      big: s => `${fmtInt(s.glucose)} <span class="bp-pill-unit">mg/dL average</span>`,
      lines: s => line('Typical low', s.gmin != null ? `${fmtInt(s.gmin)} mg/dL` : '—')
        + line('Typical high', s.gmax != null ? `${fmtInt(s.gmax)} mg/dL` : '—'),
      deltas: [
        { k: 'glucose', label: 'Average', fmt: v => v.toFixed(0), unit: ' mg/dL' },
        { k: 'gmax', label: 'High', fmt: v => v.toFixed(0), unit: ' mg/dL' }
      ],
      method: 'Daily average blood glucose from Apple Health, with the typical daily low and high. Averages hide spikes — the daily detail sits in the glucose source app.'
    });
    renderRecent(act, slp);
  }
  function renderRecent(act, slp) {
    const daysEl = document.getElementById('recent-days');
    if (daysEl) {
      const rows = act.slice(-14).reverse();
      daysEl.innerHTML = `<div class="heart-head">📅 Last 14 Days</div>` + rows.map(r =>
        `<div class="recent-row"><span class="recent-date">${fmtD(r.date)}</span>`
        + `<span class="recent-chip chip-steps">${r.steps != null ? Math.round(r.steps).toLocaleString('en-US') + ' steps' : '—'}</span>`
        + `<span class="recent-chip chip-ex">${r.exercise != null ? Math.round(r.exercise) + ' min exercise' : '—'}</span>`
        + `<span class="recent-chip chip-rest">${r.resting != null ? Math.round(r.resting) + ' bpm resting' : '—'}</span>`
        + `<span class="recent-chip chip-glu">${r.glucose != null ? Math.round(r.glucose) + ' mg/dL glucose' : '—'}</span></div>`
      ).join('');
    }
    const nightsEl = document.getElementById('recent-nights');
    if (nightsEl) {
      const rows = slp.slice(-14).reverse();
      nightsEl.innerHTML = `<div class="heart-head">🌙 Last 14 Nights</div>` + rows.map(r => {
        const staged = (r.deep || 0) + (r.rem || 0) + (r.core || 0) > 0;
        return `<div class="recent-row"><span class="recent-date">${fmtD(r.date)}</span>`
          + `<span class="recent-chip chip-sleep">${fmtDur(r.total)} asleep</span>`
          + (staged
            ? `<span class="recent-chip chip-deep">Deep ${fmtDur(r.deep)}</span>`
              + `<span class="recent-chip chip-rem">REM ${fmtDur(r.rem)}</span>`
            : `<span class="recent-chip chip-deep">stages not recorded</span>`)
          + `<span class="recent-chip chip-eff">${r.eff != null ? Math.round(r.eff) + '% efficiency' : '—'}</span></div>`;
      }).join('');
    }
  }
  root.ActivityPage = { windows, summarize, render };
  if (typeof document !== 'undefined' && document.getElementById('activity-comparison')) render(root);
})(typeof window !== 'undefined' ? window : globalThis);
