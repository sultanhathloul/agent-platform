/* Sovereign report kit v1 (reports PR, I2, ADR-0370; option C, ADR-0406): draws a report's charts from the
   platform-rendered page with the vendored Apache ECharts, one <script type="application/json"
   class="kit-chart-data" id="cN"> per chart beside its <div class="kit-chart" data-chart="cN">, and adds the
   theme toggle, tabs and sortable tables. A missing value (null) is a gap; no figure carries a label (ADR-0408).
   No network, no storage (the page runs sandboxed with an opaque origin, connect-src 'none'), and text from the
   data is only ever set as text (ECharts escapes it in its SVG renderer). */
(function () {
  "use strict";

  var root = document.documentElement;
  var rtl = (root.getAttribute("dir") || "").toLowerCase() === "rtl";
  var english = /^en\b/i.test(root.getAttribute("lang") || "");
  var TEXT = english ? { dark: "Dark", light: "Light" } : { dark: "داكن", light: "فاتح" };
  var charts = [];

  function css(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }

  function color(index) {
    return css("--kit-series-" + ((index % 8) + 1)) || "#2a78d6";
  }

  function format(unit) {
    return function (value) {
      if (typeof value !== "number") return value === null || value === undefined ? "—" : String(value);
      var text = value.toLocaleString("en-US", { maximumFractionDigits: 2 });
      return unit ? text + (unit === "%" ? "%" : " " + unit) : text;
    };
  }

  function readChart(node) {
    try {
      var data = JSON.parse(node.textContent || "{}");
      return data && typeof data === "object" ? data : null;
    } catch (e) {
      return null;
    }
  }

  function option(spec) {
    var kind = spec.type;
    var unit = typeof spec.unit === "string" ? spec.unit : "";
    var fmt = format(unit);
    var labels = Array.isArray(spec.labels) ? spec.labels.map(String) : [];
    var list = Array.isArray(spec.series) ? spec.series : [];
    var ink = css("--kit-ink-2") || "#52514e";
    var grid = css("--kit-grid") || "#e5e4e0";
    var font = getComputedStyle(document.body).fontFamily;
    var text = { color: ink, fontFamily: font };
    var base = {
      animation: false,
      textStyle: text,
      color: list.map(function (_, i) { return color(i); }),
      tooltip: { trigger: kind === "pie" || kind === "doughnut" ? "item" : "axis", valueFormatter: fmt, confine: true },
      legend: { show: list.length > 1 || kind === "pie" || kind === "doughnut", top: 0, textStyle: text, right: rtl ? 0 : "auto", left: rtl ? "auto" : 0 }
    };
    if (kind === "pie" || kind === "doughnut") {
      var first = list[0] || { data: [] };
      base.color = labels.map(function (_, i) { return color(i); });
      base.series = [{
        type: "pie",
        name: String(first.name || ""),
        radius: kind === "doughnut" ? ["45%", "70%"] : "70%",
        top: 28,
        label: { color: ink, fontFamily: font, formatter: function (p) { return p.name + ": " + fmt(p.value); } },
        data: labels.map(function (name, i) { var v = (first.data || [])[i]; return { name: name, value: typeof v === "number" ? v : null }; })
          .filter(function (d) { return d.value !== null; })
      }];
      return base;
    }
    base.grid = { top: 36, bottom: 8, left: 8, right: 8, containLabel: true };
    base.xAxis = { type: "category", data: labels, inverse: rtl, axisLabel: { color: ink, fontFamily: font, hideOverlap: true }, axisLine: { lineStyle: { color: grid } } };
    base.yAxis = { type: "value", position: rtl ? "right" : "left", axisLabel: { color: ink, fontFamily: font, formatter: fmt }, splitLine: { lineStyle: { color: grid } } };
    base.series = list.map(function (set) {
      var data = (Array.isArray(set.data) ? set.data : []).map(function (v) { return typeof v === "number" ? v : null; });
      var out = { name: String(set.name || ""), data: data, connectNulls: false };
      if (kind === "line" || kind === "area") {
        out.type = "line";
        out.symbolSize = 6;
        if (kind === "area") out.areaStyle = { opacity: 0.18 };
      } else {
        out.type = "bar";
        out.barMaxWidth = 36;
        if (kind === "stacked_bar") out.stack = "total";
      }
      return out;
    });
    return base;
  }

  function renderCharts() {
    charts.forEach(function (c) { c.dispose(); });
    charts = [];
    if (typeof echarts === "undefined") return;
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    Array.prototype.forEach.call(document.querySelectorAll("script.kit-chart-data"), function (node) {
      var spec = readChart(node);
      var box = document.querySelector('.kit-chart[data-chart="' + node.id + '"]');
      if (!spec || !box || box.offsetParent === null) return;
      if (!box.style.height) box.style.height = "320px";
      var chart = echarts.init(box, dark ? "dark" : null, { renderer: "svg" });
      var opts = option(spec);
      if (dark) opts.backgroundColor = "transparent";
      chart.setOption(opts);
      charts.push(chart);
    });
  }

  window.addEventListener("resize", function () { charts.forEach(function (c) { c.resize(); }); });

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
