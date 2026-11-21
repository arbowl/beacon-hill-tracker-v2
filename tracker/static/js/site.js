(function () {
  "use strict";
  var charts = [];

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn) return;
    var target = document.getElementById(btn.getAttribute("data-copy"));
    if (!target || !navigator.clipboard) return;
    navigator.clipboard.writeText(target.textContent.trim()).then(function () {
      var original = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(function () { btn.textContent = original; }, 1500);
    });
  });

  function initCharts() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var holder = document.getElementById("charts");
    if (!holder || !holder.dataset.charts || !window.Chart) return;
    var data = JSON.parse(holder.dataset.charts);
    var css = getComputedStyle(document.documentElement);
    var token = function (name) { return css.getPropertyValue(name).trim(); };
    var fmt = function (n) { return n.toLocaleString("en-US"); };

    Chart.defaults.font.family = token("--font-ui");
    Chart.defaults.font.size = 12;
    Chart.defaults.color = token("--c-muted");
    var tooltip = {
      backgroundColor: token("--c-text"), titleColor: token("--c-bg"), bodyColor: token("--c-bg"),
      padding: 10, cornerRadius: 4, displayColors: true, boxPadding: 4,
      titleFont: { family: token("--font-serif"), weight: "700" },
      bodyFont: { family: token("--font-ui") }
    };

    var b = data.breakdown;
    var total = b.values.reduce(function (a, v) { return a + v; }, 0);
    charts.push(new Chart(document.getElementById("pie"), {
      type: "pie",
      data: {
        labels: b.labels,
        datasets: [{
          data: b.values,
          backgroundColor: [token("--c-compliant"), token("--c-provisional"), token("--c-noncompliant")],
          borderColor: token("--c-surface"), borderWidth: 2, hoverOffset: 6
        }]
      },
      options: {
        maintainAspectRatio: false, layout: { padding: 8 },
        onClick: function (_event, elements) {
          if (!elements.length) return;
          var radio = document.querySelector('input[name="status"][value="' + b.filters[elements[0].index] + '"]');
          if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); }
        },
        onHover: function (event, elements) {
          event.native.target.style.cursor = elements.length ? "pointer" : "default";
        },
        plugins: {
          legend: { display: false },
          tooltip: Object.assign({}, tooltip, {
            callbacks: {
              label: function (ctx) {
                return " " + fmt(ctx.parsed) + " bill segments (" + (100 * ctx.parsed / total).toFixed(1) + "%)";
              }
            }
          })
        }
      }
    }));

    var f = data.funnel;
    var barLabels = {
      id: "barLabels",
      afterDatasetsDraw: function (chart) {
        var ctx = chart.ctx;
        var meta = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = "600 12px " + token("--font-ui");
        ctx.textBaseline = "middle";
        meta.data.forEach(function (bar, i) {
          var text = fmt(f.values[i]);
          var width = ctx.measureText(text).width;
          var left = Math.min(bar.x, bar.base);
          var span = Math.abs(bar.x - bar.base);
          if (width + 16 <= span) {
            ctx.fillStyle = token("--c-surface");
            ctx.textAlign = "center";
            ctx.fillText(text, left + span / 2, bar.y);
          } else {
            ctx.fillStyle = token("--c-text");
            ctx.textAlign = "left";
            ctx.fillText(text, left + span + 6, bar.y);
          }
        });
        ctx.restore();
      }
    };
    var last = f.values.length - 1;
    var widest = Math.max(f.values[0], 1);
    charts.push(new Chart(document.getElementById("funnel"), {
      type: "bar",
      data: {
        labels: f.labels,
        datasets: [{
          data: f.values.map(function (v) { return [-v / 2, v / 2]; }),
          backgroundColor: f.values.map(function (_, i) { return i === last ? token("--c-compliant") : token("--c-faint"); }),
          borderRadius: 4, borderSkipped: false, maxBarThickness: 24, categoryPercentage: 0.9, barPercentage: 0.9
        }]
      },
      options: {
        indexAxis: "y", maintainAspectRatio: false,
        scales: {
          x: { display: false, min: -widest / 2, max: widest / 2 },
          y: { grid: { display: false }, border: { display: false }, ticks: { color: token("--c-text"), font: { size: 12 } } }
        },
        plugins: {
          legend: { display: false },
          tooltip: Object.assign({}, tooltip, {
            displayColors: false,
            callbacks: {
              label: function (ctx) {
                var i = ctx.dataIndex;
                var line = fmt(f.values[i]) + " bill segments";
                if (i > 0 && f.values[i - 1]) line += ", -" + (100 * (1 - f.values[i] / f.values[i - 1])).toFixed(1) + "% from previous stage";
                return line;
              }
            }
          })
        }
      },
      plugins: [barLabels]
    }));
  }

  document.addEventListener("DOMContentLoaded", initCharts);
  document.addEventListener("htmx:load", function (event) {
    if (event.detail.elt.id === "charts") initCharts();
  });
})();
