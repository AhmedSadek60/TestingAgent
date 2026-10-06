(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage may be blocked */ } }
  };

  // ---- theme -------------------------------------------------------------------------------------------------
  var root = document.documentElement;
  var saved = store.get("agentlab-theme");
  if (saved === "light" || saved === "dark") { root.setAttribute("data-theme", saved); }
  var themeBtn = $("#btn-theme");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var dark = root.getAttribute("data-theme") === "dark" ||
        (!root.getAttribute("data-theme") && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
      var next = dark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      store.set("agentlab-theme", next);
    });
  }
  var printBtn = $("#btn-print");
  if (printBtn) { printBtn.addEventListener("click", function () { window.print(); }); }
  var jsonBtn = $("#btn-json");
  if (jsonBtn) {
    jsonBtn.addEventListener("click", function () {
      var el = $("#agentlab-data");
      if (!el) { return; }
      var blob = new Blob([el.textContent || ""], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = jsonBtn.getAttribute("data-name") || "report.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
  }

  // ---- table of contents highlight ---------------------------------------------------------------------------
  var links = $$(".toc a[href^='#']");
  if ("IntersectionObserver" in window && links.length) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && byId[e.target.id]) {
          links.forEach(function (a) { a.classList.remove("active"); });
          byId[e.target.id].classList.add("active");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    $$("main section[id]").forEach(function (s) { io.observe(s); });
  }

  // ---- results table: filter, search, sort, expand -----------------------------------------------------------
  var table = $("#results");
  if (table) {
    var rows = $$("tr.row", table);
    var q = $("#q");
    var cat = $("#f-cat");
    var chips = $$("#status-chips .chip");
    var count = $("#result-count");
    var active = {};
    var apply = function () {
      var text = q ? q.value.trim().toLowerCase() : "";
      var c = cat ? cat.value : "";
      var any = Object.keys(active).length > 0;
      var shown = 0;
      rows.forEach(function (r) {
        var ok = (!any || active[r.getAttribute("data-status")]) &&
          (!c || r.getAttribute("data-cat") === c) &&
          (!text || (r.getAttribute("data-text") || "").indexOf(text) !== -1);
        r.hidden = !ok;
        var d = r.nextElementSibling;
        if (d && d.classList.contains("detail") && !ok) { d.hidden = true; }
        if (ok) { shown++; }
      });
      if (count) { count.textContent = shown + " of " + rows.length + " tests"; }
    };
    chips.forEach(function (b) {
      b.addEventListener("click", function () {
        var s = b.getAttribute("data-status");
        if (active[s]) { delete active[s]; b.setAttribute("aria-pressed", "false"); }
        else { active[s] = true; b.setAttribute("aria-pressed", "true"); }
        apply();
      });
    });
    if (q) { q.addEventListener("input", apply); }
    if (cat) { cat.addEventListener("change", apply); }
    rows.forEach(function (r) {
      var toggle = function () {
        var d = r.nextElementSibling;
        if (d && d.classList.contains("detail")) { d.hidden = !d.hidden; r.setAttribute("aria-expanded", String(!d.hidden)); }
      };
      r.addEventListener("click", function (e) { if (e.target.tagName !== "A") { toggle(); } });
      r.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    });
    $$("th.sortable", table).forEach(function (th, idx) {
      th.addEventListener("click", function () {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        $$("th.sortable", table).forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var col = parseInt(th.getAttribute("data-col"), 10);
        var numeric = th.getAttribute("data-type") === "num";
        var body = table.tBodies[0];
        var pairs = rows.map(function (r) { return [r, r.nextElementSibling]; });
        pairs.sort(function (a, b) {
          var x = a[0].children[col].getAttribute("data-v") || a[0].children[col].textContent;
          var y = b[0].children[col].getAttribute("data-v") || b[0].children[col].textContent;
          if (numeric) { x = parseFloat(x) || 0; y = parseFloat(y) || 0; return asc ? x - y : y - x; }
          return asc ? String(x).localeCompare(String(y)) : String(y).localeCompare(String(x));
        });
        pairs.forEach(function (p) { body.appendChild(p[0]); if (p[1]) { body.appendChild(p[1]); } });
        void idx;
      });
    });
    // clicking a cell of the test matrix filters the table to that category and status
    $$("rect.cell").forEach(function (cell) {
      cell.addEventListener("click", function () {
        var c = cell.getAttribute("data-row");
        var s = cell.getAttribute("data-col");
        if (cat) { cat.value = c || ""; }
        active = {};
        chips.forEach(function (b) {
          var on = b.getAttribute("data-status") === s;
          b.setAttribute("aria-pressed", String(on));
          if (on) { active[s] = true; }
        });
        apply();
        table.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    apply();
  }

  // ---- findings: severity filter -----------------------------------------------------------------------------
  var findings = $$(".finding");
  var sevChips = $$("#sev-chips .chip");
  if (findings.length && sevChips.length) {
    var sevActive = {};
    sevChips.forEach(function (b) {
      b.addEventListener("click", function () {
        var s = b.getAttribute("data-sev");
        if (sevActive[s]) { delete sevActive[s]; b.setAttribute("aria-pressed", "false"); }
        else { sevActive[s] = true; b.setAttribute("aria-pressed", "true"); }
        var any = Object.keys(sevActive).length > 0;
        findings.forEach(function (f) { f.hidden = any && !sevActive[f.getAttribute("data-sev")]; });
      });
    });
  }

  // ---- expand / collapse all ---------------------------------------------------------------------------------
  var exp = $("#btn-expand");
  if (exp) {
    var open = false;
    exp.addEventListener("click", function () {
      open = !open;
      $$("main details").forEach(function (d) { d.open = open; });
      exp.textContent = open ? "Collapse all" : "Expand all";
      exp.classList.toggle("on", open);
    });
  }

  // ---- deep links open the details they point into ------------------------------------------------------------
  var openHash = function () {
    var id = window.location.hash.slice(1);
    if (!id) { return; }
    var el = document.getElementById(id);
    var d = el && (el.tagName === "DETAILS" ? el : el.closest("details"));
    if (d) { d.open = true; }
  };
  window.addEventListener("hashchange", openHash);
  openHash();
})();
