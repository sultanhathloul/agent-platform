/* Sovereign report kit v1 (reports PR, I2, ADR-0370): renders a report's charts from its one
   <script type="application/json" id="report-data"> block with the vendored Chart.js, marks the
   source of every value the platform labelled (owner decision A, ADR-0388), and adds the theme toggle, tabs and sortable tables.
   No network, no storage (the page runs sandboxed with an opaque origin, connect-src 'none'), and
   text from the data is only ever set with textContent. */
(function () {
  "use strict";

  var root = document.documentElement;
  var rtl = (root.getAttribute("dir") || "").toLowerCase() === "rtl";
  var english = /^en\b/i.test(root.getAttribute("lang") || "");
  var TEXT = english
    ? { sources: "Sources of this chart's values:", dark: "Dark", light: "Light", missing: "No data for this chart." }
    : { sources: "مصادر قيم هذا الرسم:", dark: "داكن", light: "فاتح", missing: "لا بيانات لهذا الرسم." };
  var charts = [];

  function css(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }

  function series(index) {
    return css("--kit-series-" + ((index % 8) + 1)) || "#2a78d6";
  }

  function readData() {
    var node = document.getElementById("report-data");
    if (!node) return {};
    try {
      var data = JSON.parse(node.textContent || "{}");
      return data && typeof data === "object" ? data : {};
    } catch (e) {
      return {};
    }
  }

  function numberFormat(unit) {
    return function (value) {
      if (typeof value !== "number") return value;
      var text = value.toLocaleString("en-US", { maximumFractionDigits: 2 });
      return unit ? text + (unit === "%" ? "%" : " " + unit) : text;
    };
  }

  function chartType(spec) {
    var kind = String(spec.type || "bar");
    if (kind === "area") return "line";
    if (kind === "horizontalBar" || kind === "stackedBar") return "bar";
    return ["bar", "line", "pie", "doughnut", "radar", "polarArea"].indexOf(kind) >= 0 ? kind : "bar";
  }

  function config(spec) {
    var kind = String(spec.type || "bar");
    var type = chartType(spec);
    var round = type === "pie" || type === "doughnut" || type === "polarArea";
    var surface = css("--kit-surface");
    var labels = Array.isArray(spec.labels) ? spec.labels.map(String) : [];
    var datasets = (Array.isArray(spec.datasets) ? spec.datasets : []).map(function (set, i) {
      var general = Array.isArray(set.general) && set.general.length > 0;
      var color = series(i);
      var label = String(set.label || "");
      var data = (Array.isArray(set.data) ? set.data : []).map(function (v) { return typeof v === "number" ? v : null; });
      var out = { label: label, data: data, borderWidth: 2 };
      if (round) {
        out.backgroundColor = data.map(function (_, j) { return series(j); });
        out.borderColor = surface;
      } else if (type === "line") {
        out.borderColor = color;
        out.backgroundColor = kind === "area" ? color + "33" : color;
        out.fill = kind === "area";
        out.tension = 0.3;
        out.pointRadius = 3;
        out.pointHoverRadius = 6;
        if (general) out.borderDash = [6, 4];
      } else {
        out.backgroundColor = color;
        out.borderColor = color;
        out.borderRadius = 4;
        out.borderSkipped = "start";
        out.maxBarThickness = 44;
        if (general) { out.backgroundColor = color + "66"; out.borderDash = [4, 3]; }
      }
      return out;
    });
    var ink = css("--kit-ink-2"), grid = css("--kit-grid"), muted = css("--kit-muted");
    var format = numberFormat(spec.unit);
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      locale: "en-US",
      font: { family: css("--kit-font") },
      plugins: {
        legend: { display: datasets.length > 1 || round, position: "bottom", rtl: rtl, textDirection: rtl ? "rtl" : "ltr", labels: { color: ink, usePointStyle: true, boxWidth: 8, font: { family: css("--kit-font") } } },
        tooltip: { rtl: rtl, textDirection: rtl ? "rtl" : "ltr", callbacks: {
          title: function (items) { var it = items[0]; if (!it) return ""; var named = Array.isArray(spec.label_sources) ? spec.label_sources[it.dataIndex] : null; return String(it.label || "") + (named ? " (" + named + ")" : ""); },
          label: function (ctx) { var v = ctx.parsed && typeof ctx.parsed === "object" ? (kind === "horizontalBar" ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed; var set = (spec.datasets || [])[ctx.datasetIndex] || {}; var from = Array.isArray(set.sources) ? set.sources[ctx.dataIndex] : null; return (ctx.dataset.label ? ctx.dataset.label + ": " : "") + format(v) + (from ? " (" + from + ")" : ""); }
        } },
        title: { display: false }
      }
    };
    if (!round && type !== "radar") {
      var horizontal = kind === "horizontalBar";
      var category = { reverse: rtl && !horizontal, grid: { display: false }, ticks: { color: muted, font: { family: css("--kit-font") } }, border: { color: css("--kit-axis") }, stacked: !!spec.stacked || kind === "stackedBar" };
      var value = { beginAtZero: true, position: horizontal ? "bottom" : (rtl ? "right" : "left"), grid: { color: grid }, ticks: { color: muted, callback: format, font: { family: css("--kit-font") } }, border: { display: false }, stacked: !!spec.stacked || kind === "stackedBar" };
      if (horizontal) { options.indexAxis = "y"; options.scales = { x: value, y: category }; value.reverse = rtl; category.position = rtl ? "right" : "left"; }
      else options.scales = { x: category, y: value };
    }
    return { type: type, data: { labels: labels, datasets: datasets }, options: options };
  }

  // Under each chart, the sources of its values, one chip each (general knowledge in its marker colour).
  function note(container, labels) {
    var p = document.createElement("div");
    p.className = "kit-chart-note";
    p.appendChild(document.createTextNode(TEXT.sources + " "));
    labels.forEach(function (item) {
      var b = document.createElement("span");
      b.className = "kit-figure-note";
      b.setAttribute("data-kit-source", item.general ? "general" : "source");
      b.textContent = item.text;
      p.appendChild(b);
    });
    container.parentNode.insertBefore(p, container.nextSibling);
  }

  function chartSources(spec) {
    var seen = {}, out = [];
    (spec.datasets || []).forEach(function (set) {
      var general = {};
      (Array.isArray(set.general) ? set.general : []).forEach(function (i) { general[i] = true; });
      (Array.isArray(set.sources) ? set.sources : []).forEach(function (text, i) {
        if (typeof text !== "string" || !text || seen[text]) return;
        seen[text] = true;
        out.push({ text: text, general: !!general[i] });
      });
    });
    return out;
  }

  function renderCharts() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var data = readData();
    var specs = data.charts && typeof data.charts === "object" ? data.charts : {};
    var nodes = document.querySelectorAll(".kit-chart[data-chart]");
    Array.prototype.forEach.call(nodes, function (node) {
      var spec = specs[node.getAttribute("data-chart")];
      node.textContent = "";
      if (!spec || typeof window.Chart !== "function") {
        var empty = document.createElement("div");
        empty.className = "kit-chart-missing";
        empty.textContent = TEXT.missing;
        node.appendChild(empty);
        return;
      }
      var canvas = document.createElement("canvas");
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", String(spec.title || ""));
      node.appendChild(canvas);
      charts.push(new window.Chart(canvas, config(spec)));
      var labels = chartSources(spec);
      if (labels.length && !node.getAttribute("data-kit-noted")) {
        node.setAttribute("data-kit-noted", "1");
        note(node, labels);
      }
    });
  }

  function themeToggle() {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "kit-theme-toggle";
    function dark() {
      var set = root.getAttribute("data-theme");
      if (set) return set === "dark";
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function label() { button.textContent = dark() ? TEXT.light : TEXT.dark; }
    button.addEventListener("click", function () {
      root.setAttribute("data-theme", dark() ? "light" : "dark");
      label();
      renderCharts();
    });
    label();
    document.body.appendChild(button);
  }

  function tabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".kit-tabs"), function (group) {
      var panels = Array.prototype.filter.call(group.children, function (el) { return el.classList.contains("kit-tab"); });
      if (!panels.length) return;
      var bar = document.createElement("div");
      bar.className = "kit-tabs-bar";
      bar.setAttribute("role", "tablist");
      panels.forEach(function (panel, i) {
        var button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "tab");
        button.textContent = panel.getAttribute("data-label") || String(i + 1);
        button.addEventListener("click", function () { select(i); });
        bar.appendChild(button);
      });
      function select(index) {
        panels.forEach(function (panel, i) { panel.hidden = i !== index; });
        Array.prototype.forEach.call(bar.children, function (b, i) { b.setAttribute("aria-selected", i === index ? "true" : "false"); });
        renderCharts();
      }
      group.insertBefore(bar, group.firstChild);
      select(0);
    });
  }

  function cellValue(cell) {
    var text = (cell.textContent || "").trim(); // a figure's label is CSS content, not text
    var number = parseFloat(text.replace(/[,%\s]/g, ""));
    return isNaN(number) ? text : number;
  }

  function sortable() {
    Array.prototype.forEach.call(document.querySelectorAll("table.kit-table[data-kit-sortable]"), function (table) {
      var head = table.tHead && table.tHead.rows[0];
      var body = table.tBodies[0];
      if (!head || !body) return;
      Array.prototype.forEach.call(head.cells, function (th, column) {
        th.setAttribute("data-kit-sort", "");
        th.addEventListener("click", function () {
          var asc = th.getAttribute("data-kit-sort") !== "asc";
          Array.prototype.forEach.call(head.cells, function (other) { other.setAttribute("data-kit-sort", ""); });
          th.setAttribute("data-kit-sort", asc ? "asc" : "desc");
          var rows = Array.prototype.slice.call(body.rows);
          rows.sort(function (a, b) {
            var x = cellValue(a.cells[column] || a), y = cellValue(b.cells[column] || b);
            var order = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), english ? "en" : "ar");
            return asc ? order : -order;
          });
          rows.forEach(function (row) { body.appendChild(row); });
        });
      });
    });
  }

  function start() {
    themeToggle();
    tabs();
    sortable();
    renderCharts();
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      if (query.addEventListener) query.addEventListener("change", function () { if (!root.getAttribute("data-theme")) renderCharts(); });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
