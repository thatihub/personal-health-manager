/* ==========================================================================
   charts.js — shared SVG chart builders for Personal Health Manager v2.
   Extracted from the hand-rolled builders that were duplicated across
   dashboard.html, bp.html and composition.html. Rendering behavior is
   preserved; each page keeps a thin adapter that maps its own quirks
   (reference bands, tick formats, dot rules) onto this engine.

   Charts.line(svgId, cfg)
     cfg.series   [{ values:[num|null], color, width=2.5,
                     dots: fn(i, value, count) -> {r, fill, stroke, sw} | null,
                     endLabel: bool (label the last point's value) }]
     cfg.count    number of x positions (default: longest series)
     cfg.pad      {l,r,t,b}  (default {l:42,r:12,t:15,b:25})
     cfg.fallbackW / cfg.fallbackH  when the svg has no layout size yet
     cfg.y        { min, max, ticks=4, tickFormat: fn(v)->string | null,
                    tickFill, tickSize, gridStroke, gridW }
     cfg.band     { min, max, fill } — shaded reference band behind the line
     cfg.centerLine { value, label, stroke, labelFill }
     cfg.xLabel   { fn: (i)->string|null, fill, size, dy } (y = H + dy)
     cfg.cornerText { text, fill, size } — top-left annotation
     cfg.emptyText / cfg.emptyFill — shown when there are no values
   ========================================================================== */
(function () {
  "use strict";

  function validNum(v) {
    if (v === null || v === undefined) return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function line(svgId, cfg) {
    var svg = typeof svgId === "string" ? document.getElementById(svgId) : svgId;
    if (!svg) return;
    cfg = cfg || {};
    var pad = cfg.pad || { l: 42, r: 12, t: 15, b: 25 };
    var W = svg.clientWidth || cfg.fallbackW || 520;
    var H = svg.clientHeight || cfg.fallbackH || 200;
    var w = W - pad.l - pad.r;
    var h = H - pad.t - pad.b;
    var series = cfg.series || [];
    var count = cfg.count || 0;
    series.forEach(function (s) {
      count = Math.max(count, (s.values || []).length);
    });

    var all = [];
    series.forEach(function (s) {
      (s.values || []).forEach(function (v) {
        var n = validNum(v);
        if (n !== null) all.push(n);
      });
    });

    function renderEmpty() {
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.innerHTML = cfg.emptyText
        ? '<text x="50%" y="50%" text-anchor="middle" fill="' + (cfg.emptyFill || "#98adbf") +
          '" font-size="12" font-weight="700" dy="4">' + esc(cfg.emptyText) + "</text>"
        : "";
    }
    if (!all.length) { renderEmpty(); return; }

    var yc = cfg.y || {};
    var yMin = yc.min != null ? yc.min : Math.min.apply(null, all);
    var yMax = yc.max != null ? yc.max : Math.max.apply(null, all);
    if (!(yMax > yMin)) yMax = yMin + 1;

    var y = function (v) { return pad.t + (1 - (v - yMin) / (yMax - yMin)) * h; };
    var x = function (i) { return pad.l + (i / Math.max(1, count - 1)) * w; };

    var html = "";

    if (cfg.band && cfg.band.min != null && cfg.band.max != null) {
      var bTop = y(Math.max(cfg.band.min, cfg.band.max));
      var bBot = y(Math.min(cfg.band.min, cfg.band.max));
      html += '<rect x="' + pad.l + '" y="' + bTop + '" width="' + w + '" height="' +
        Math.max(0, bBot - bTop) + '" fill="' + (cfg.band.fill || "rgba(58,125,68,0.12)") + '"/>';
    }

    var ticks = yc.ticks == null ? 4 : yc.ticks;
    for (var gi = 0; gi < ticks; gi++) {
      var frac = ticks === 1 ? 0 : gi / (ticks - 1);
      var yy = pad.t + frac * h;
      html += '<line x1="' + pad.l + '" y1="' + yy + '" x2="' + (pad.l + w) + '" y2="' + yy +
        '" stroke="' + (yc.gridStroke || "rgba(160,180,198,0.28)") + '" stroke-width="' + (yc.gridW || 1) +
        '" stroke-dasharray="3 4"/>';
      if (yc.tickFormat) {
        html += '<text x="' + (pad.l - 6) + '" y="' + (yy + 4) + '" text-anchor="end" fill="' +
          (yc.tickFill || "#98adbf") + '" font-size="' + (yc.tickSize || 10) + '" font-weight="700">' +
          esc(yc.tickFormat(yMax - frac * (yMax - yMin))) + "</text>";
      }
    }

    if (cfg.centerLine && cfg.centerLine.value != null) {
      var cY = y(cfg.centerLine.value);
      html += '<line x1="' + pad.l + '" y1="' + cY + '" x2="' + (pad.l + w) + '" y2="' + cY +
        '" stroke="' + (cfg.centerLine.stroke || "#c5d9e8") + '" stroke-opacity="0.65" stroke-dasharray="5 5" stroke-width="0.9"/>';
      if (cfg.centerLine.label != null) {
        html += '<text x="' + (pad.l + 6) + '" y="' + (cY - 6) + '" fill="' +
          (cfg.centerLine.labelFill || "#35566d") + '" font-size="11" font-weight="800">' +
          esc(cfg.centerLine.label) + "</text>";
      }
    }

    if (cfg.xLabel && typeof cfg.xLabel.fn === "function") {
      for (var xi = 0; xi < count; xi++) {
        var lab = cfg.xLabel.fn(xi);
        if (lab === null || lab === undefined || lab === "") continue;
        html += '<text x="' + x(xi) + '" y="' + (H + (cfg.xLabel.dy == null ? -8 : cfg.xLabel.dy)) +
          '" text-anchor="middle" fill="' + (cfg.xLabel.fill || "#98adbf") + '" font-size="' +
          (cfg.xLabel.size || 10) + '" font-weight="700">' + esc(lab) + "</text>";
      }
    }

    series.forEach(function (s) {
      var vals = s.values || [];
      var path = [];
      vals.forEach(function (v, i) {
        var n = validNum(v);
        if (n === null) return;
        path.push((path.length ? "L" : "M") + x(i) + " " + y(n));
      });
      if (!path.length) return;
      html += '<path d="' + path.join(" ") + '" fill="none" stroke="' + s.color +
        '" stroke-width="' + (s.width || 2.5) + '" stroke-linecap="round" stroke-linejoin="round"/>';

      if (typeof s.dots === "function") {
        vals.forEach(function (v, i) {
          var n = validNum(v);
          if (n === null) return;
          var d = s.dots(i, n, count);
          if (!d) return;
          html += '<circle cx="' + x(i) + '" cy="' + y(n) + '" r="' + d.r + '" fill="' + d.fill +
            '" stroke="' + (d.stroke || "none") + '" stroke-width="' + (d.sw || 0) + '"/>';
        });
      }

      if (s.endLabel) {
        for (var li = vals.length - 1; li >= 0; li--) {
          var lv = validNum(vals[li]);
          if (lv === null) continue;
          html += '<text x="' + (x(li) + 8) + '" y="' + (y(lv) - 6) + '" fill="' + s.color +
            '" font-size="11" font-weight="800">' + esc(String(vals[li])) + "</text>";
          break;
        }
      }
    });

    if (cfg.cornerText && cfg.cornerText.text) {
      html += '<text x="' + pad.l + '" y="' + (pad.t + 10) + '" fill="' +
        (cfg.cornerText.fill || "#35566d") + '" font-size="' + (cfg.cornerText.size || 11) +
        '" font-weight="800">' + esc(cfg.cornerText.text) + "</text>";
    }

    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.innerHTML = html;
  }

  window.Charts = { line: line };
})();
