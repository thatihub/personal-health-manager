/* ==========================================================================
   shell.js — shared page shell for Personal Health Manager v2.
   Injects the top bar (brand + nav + storage pill) and the mobile bottom
   tab bar. Sets the per-page accent from the registry. Include on every
   page AFTER the page's own content containers exist, e.g.:

     <body data-page="home">
       ...
       <script src="./static/js/shell.js"></script>
   ========================================================================== */
(function () {
  "use strict";

  var PAGES = [
    { id: "home",         file: "index.html",               label: "Home",      icon: "🏠", accent: "#12a4d4", accent2: "#0d8bb8" },
    { id: "labs",         file: "dashboard.html",           label: "Labs",      icon: "🧪", accent: "#9f7aea", accent2: "#7b5dd6" },
    { id: "bioage",       file: "bio-age.html",             label: "Bio Age",   icon: "⏳", accent: "#14b8a6", accent2: "#0f8f82" },
    { id: "bp",           file: "bp.html",                  label: "BP",        icon: "❤️", accent: "#1bc471", accent2: "#0f9a57" },
    { id: "composition",  file: "composition.html",         label: "Body Comp", icon: "📊", accent: "#1aa672", accent2: "#0f8368" },
    { id: "weight",       file: "weight.html",              label: "Weight",    icon: "⚖️", accent: "#60a5fa", accent2: "#3c84db" },
    { id: "medications",  file: "medications.html",         label: "Meds",      icon: "💊", accent: "#ef6f59", accent2: "#cf5541" },
    { id: "doctor-notes", file: "doctor-notes.html",        label: "Doctor",    icon: "📝", accent: "#e38f1f", accent2: "#be7415" },
    { id: "vault",        file: "vault.html",               label: "Vault",     icon: "🔐", accent: "#f0a43a", accent2: "#cf8417" },
    { id: "history",      file: "history.html",            label: "History",   icon: "📜", accent: "#60a5fa", accent2: "#3c84db" },
    { id: "immunizations",file: "immunizations.html",      label: "Vaccines",  icon: "💉", accent: "#8beee0", accent2: "#5ccabc" },
    { id: "cabinet",      file: "cabinet.html",             label: "Cabinet",   icon: "🗄️", accent: "#ef6f59", accent2: "#cf5541" },
    { id: "kaiser-labs",  file: "kaiser-labs.html",         label: "Kaiser",    icon: "🏥", accent: "#67d8ff", accent2: "#3aa8e0" },
    { id: "kaiser-summary", file: "kaiser-health-summary.html", label: "Kaiser+",   icon: "📋", accent: "#8dcdf5", accent2: "#4f9fd8" },
    { id: "lab-import",   file: "lab-import.html",          label: "Import",    icon: "📥", accent: "#e38f1f", accent2: "#be7415" },
    { id: "youtube",      file: "youtube-comments.html",    label: "YouTube",   icon: "▶️", accent: "#ff6b6b", accent2: "#d94f4f" },
    { id: "admin",        file: "admin.html",               label: "Admin",     icon: "⚙️", accent: "#14b8a6", accent2: "#0f8f82" }
  ];
  var PRIMARY = ["home", "labs", "bioage", "bp", "composition"];

  var pageId = document.body.getAttribute("data-page") || "home";
  var page = PAGES.find(function (p) { return p.id === pageId; }) || PAGES[0];

  // Per-page accent -> CSS vars (drives buttons, active nav, card tops, glows)
  document.body.style.setProperty("--accent", page.accent);
  document.body.style.setProperty("--accent-2", page.accent2);
  document.title = page.label + " · Personal Health Manager";

  function link(p, cls) {
    var a = document.createElement("a");
    a.href = "./" + p.file;
    a.className = cls || "";
    if (p.id === pageId) a.classList.add("active");
    a.innerHTML = '<span class="ico">' + p.icon + "</span><span>" + p.label + "</span>";
    return a;
  }

  /* ---- top bar ---- */
  var topbar = document.createElement("header");
  topbar.className = "topbar";

  var brand = document.createElement("a");
  brand.className = "brand";
  brand.href = "./index.html";
  brand.innerHTML = '<span class="brand-mark">🫀</span><span>Personal Health Manager</span>';
  topbar.appendChild(brand);

  var nav = document.createElement("nav");
  nav.className = "nav";
  nav.setAttribute("aria-label", "Primary");
  PRIMARY.forEach(function (id) {
    var p = PAGES.find(function (x) { return x.id === id; });
    nav.appendChild(link(p));
  });

  // "More" dropdown for the rest
  var more = document.createElement("details");
  more.className = "nav-more";
  var sum = document.createElement("summary");
  sum.textContent = "More ▾";
  more.appendChild(sum);
  var menu = document.createElement("div");
  menu.className = "more-menu";
  PAGES.forEach(function (p) {
    if (PRIMARY.indexOf(p.id) === -1) menu.appendChild(link(p));
  });
  more.appendChild(menu);
  nav.appendChild(more);
  // close dropdown on selection / outside click
  menu.addEventListener("click", function () { more.removeAttribute("open"); });
  document.addEventListener("click", function (e) {
    if (!more.contains(e.target)) more.removeAttribute("open");
  });
  topbar.appendChild(nav);

  // storage status pill (filled by app.js when present, quiet default otherwise)
  var pill = document.createElement("span");
  pill.className = "storage-pill";
  pill.id = "storage-pill";
  pill.innerHTML = '<span class="dot"></span><span>storage…</span>';
  pill.title = "Where weight & scan saves go";
  topbar.appendChild(pill);

  var wrap = document.querySelector(".wrap");
  if (wrap) wrap.insertBefore(topbar, wrap.firstChild);
  else document.body.insertBefore(topbar, document.body.firstChild);

  /* ---- mobile bottom tab bar ---- */
  var bottom = document.createElement("nav");
  bottom.className = "bottomnav";
  bottom.setAttribute("aria-label", "Mobile");
  var row = document.createElement("div");
  row.className = "bottomnav-row";
  PRIMARY.forEach(function (id) {
    var p = PAGES.find(function (x) { return x.id === id; });
    row.appendChild(link(p));
  });
  var moreBtn = document.createElement("button");
  moreBtn.className = "more-btn";
  moreBtn.type = "button";
  moreBtn.innerHTML = '<span class="ico">⋯</span><span>More</span>';
  row.appendChild(moreBtn);
  bottom.appendChild(row);
  document.body.appendChild(bottom);

  // "More" bottom sheet (all pages)
  var scrim = document.createElement("div");
  scrim.className = "sheet-scrim";
  scrim.hidden = true;
  var sheet = document.createElement("div");
  sheet.className = "more-sheet";
  sheet.hidden = true;
  sheet.innerHTML = "<h4>All sections</h4>";
  var grid = document.createElement("div");
  grid.className = "more-grid";
  PAGES.forEach(function (p) { grid.appendChild(link(p)); });
  sheet.appendChild(grid);
  document.body.appendChild(scrim);
  document.body.appendChild(sheet);

  function closeSheet() { sheet.hidden = true; scrim.hidden = true; }
  moreBtn.addEventListener("click", function () {
    sheet.hidden = false; scrim.hidden = false;
  });
  scrim.addEventListener("click", closeSheet);
  sheet.addEventListener("click", function (e) {
    if (e.target.closest("a")) closeSheet();
  });

  // expose registry for pages that need it
  window.PHM = window.PHM || {};
  window.PHM.pages = PAGES;
  window.PHM.page = page;
})();
