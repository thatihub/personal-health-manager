/* ==========================================================================
   app.js — shared helpers for Personal Health Manager v2.
   fetch wrappers, toasts, formatting, storage-status + last-updated badges.
   Include before the page's own script:
     <script src="./static/js/app.js"></script>
   ========================================================================== */
(function () {
  "use strict";

  function ensureToastRoot() {
    var root = document.querySelector(".toast-root");
    if (!root) {
      root = document.createElement("div");
      root.className = "toast-root";
      document.body.appendChild(root);
    }
    return root;
  }

  function toast(msg, kind) {
    var root = ensureToastRoot();
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(function () { el.remove(); }, 320);
    }, 3400);
  }

  function check(res, url) {
    if (!res.ok) throw new Error("Request failed (" + res.status + ") — " + url);
    return res;
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { return check(r, url); })
      .then(function (r) { return r.json(); });
  }

  function postJSON(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(data || {})
    })
      .then(function (r) { return check(r, url); })
      .then(function (r) { return r.json(); });
  }

  function postForm(url, formData) {
    return fetch(url, { method: "POST", body: formData })
      .then(function (r) { return check(r, url); })
      .then(function (r) { return r.json(); });
  }

  /* ---------- formatting ---------- */
  function fmtNum(n, digits) {
    if (n === null || n === undefined || n === "" || !isFinite(Number(n))) return "—";
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: digits == null ? 0 : digits,
      maximumFractionDigits: digits == null ? 2 : digits
    });
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso.length <= 10 ? iso + "T12:00:00" : iso);
    if (isNaN(d)) return String(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  /* value pill for lab readings: normal | high | low | na */
  function valuePill(value, flag) {
    var cls = "value-pill " + (flag || "na");
    return '<span class="' + cls + '">' + esc(value == null ? "—" : value) + "</span>";
  }

  /* ---------- header badges ---------- */
  function refreshStoragePill() {
    var pill = document.getElementById("storage-pill");
    if (!pill) return;
    getJSON("./api/storage-status")
      .then(function (s) {
        var ok = !!(s && (s.writes_enabled || s.enabled));
        pill.classList.toggle("ok", ok);
        pill.classList.toggle("limited", !ok);
        pill.innerHTML = '<span class="dot"></span><span>' +
          (ok ? "saves on" : "saves paused") + "</span>";
        pill.title = (s && s.detail) || pill.title;
      })
      .catch(function () {
        pill.innerHTML = '<span class="dot"></span><span>offline</span>';
      });
  }

  function refreshUpdatedBadge(sel) {
    var nodes = document.querySelectorAll(sel || ".updated-banner");
    if (!nodes.length) return;
    getJSON("./api/last-updated")
      .then(function (d) {
        var label = (d && (d.label || d.last_updated)) || "";
        nodes.forEach(function (n) {
          n.innerHTML = "Updated " + esc(label || "—");
        });
      })
      .catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    refreshStoragePill();
    refreshUpdatedBadge();
  });

  window.App = {
    getJSON: getJSON,
    postJSON: postJSON,
    postForm: postForm,
    toast: toast,
    fmtNum: fmtNum,
    fmtDate: fmtDate,
    esc: esc,
    el: el,
    valuePill: valuePill,
    refreshStoragePill: refreshStoragePill,
    refreshUpdatedBadge: refreshUpdatedBadge
  };
})();
