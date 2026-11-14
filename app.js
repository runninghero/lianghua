(function () {
  "use strict";

  var WINDOWS = ["1", "3", "6", "9", "12"];
  var state = { key: "ret:9", dir: "desc", payload: null };

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function weekday(iso) {
    var parts = iso.split("-");
    var utc = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
    return "周" + ["日", "一", "二", "三", "四", "五", "六"][utc.getUTCDay()];
  }

  function fmtUpdated(iso) {
    var match = String(iso).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
    if (!match) return String(iso);
    return match[1] + " " + match[2];
  }

  function fmtPct(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return null;
    var pct = value * 100;
    var body = Math.abs(pct).toFixed(2) + "%";
    if (pct > 0) return { text: "+" + body, cls: "up" };
    if (pct < 0) return { text: "−" + body, cls: "down" };
    return { text: body, cls: "flat" };
  }

  function fmtPx(value) {
    return Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function valueOf(row, key) {
    if (key === "ticker") return row.ticker;
    if (key === "name") return row.name;
    if (key === "price") return row.adjClose;
    if (key.indexOf("ret:") === 0) return row.returns[key.slice(4)];
    if (key.indexOf("rank:") === 0) return row.ranks[key.slice(5)];
    return null;
  }

  function compare(a, b) {
    var va = valueOf(a, state.key);
    var vb = valueOf(b, state.key);
    var aMissing = va === null || va === undefined;
    var bMissing = vb === null || vb === undefined;
    if (aMissing && bMissing) return a.ticker.localeCompare(b.ticker);
    if (aMissing) return 1;
    if (bMissing) return -1;
    var diff = 0;
    if (typeof va === "string") diff = va.localeCompare(vb, "zh");
    else diff = va === vb ? 0 : va < vb ? -1 : 1;
    if (diff === 0) return a.ticker.localeCompare(b.ticker);
    return state.dir === "asc" ? diff : -diff;
  }

  function tip(row, months) {
    var ret = row.returns[months];
    if (ret === null || ret === undefined) return "该窗口没有足够历史，不参与排名";
    return (
      "起点 " + row.startDate[months] +
      " 复权价 " + fmtPx(row.startAdjClose[months]) +
      " → 终点 " + state.payload.asOf +
      " 复权价 " + fmtPx(row.adjClose)
    );
  }

  function renderFacts(payload) {
    var source = payload.source === "yahoo" ? "Yahoo Finance 复权收盘价" : payload.source;
    document.getElementById("facts").innerHTML =
      '<article><h3>截至日期</h3><strong>' + esc(payload.asOf) + " " + esc(weekday(payload.asOf)) +
      "</strong><span>最近一个美股交易日收盘</span></article>" +
      '<article><h3>页面更新时间</h3><strong>' + esc(fmtUpdated(payload.updatedAt)) +
      "</strong><span>亚洲/上海（UTC+8）</span></article>" +
      '<article><h3>价格</h3><strong>' + esc(source) +
      "</strong><span>分红再投资 · 11 只行业 ETF</span></article>";

    var asof = document.getElementById("asof");
    asof.innerHTML =
      '<p class="asof-label">截至收盘</p>' +
      '<p class="asof-date">' + esc(payload.asOf) + "</p>" +
      '<p class="asof-sub">美股最近交易日 · ' + esc(weekday(payload.asOf)) + "</p>";
  }

  function renderSnapshot(rows) {
    var section = document.getElementById("snapshot");
    var usable = rows.filter(function (row) { return row.returns["9"] !== null && row.returns["9"] !== undefined; });
    if (!usable.length) {
      section.hidden = true;
      return;
    }
    var maxAbs = usable.reduce(function (max, row) {
      return Math.max(max, Math.abs(row.returns["9"]));
    }, 0.0001);
    var ordered = usable.slice().sort(function (a, b) {
      if (a.returns["9"] === b.returns["9"]) return a.ticker.localeCompare(b.ticker);
      return b.returns["9"] - a.returns["9"];
    });
    var html = '<h2>9 个月总回报</h2><div class="bars">';
    ordered.forEach(function (row) {
      var value = row.returns["9"];
      var pct = fmtPct(value);
      var width = Math.abs(value) / maxAbs * 50;
      var style = value >= 0
        ? "left:50%;width:" + width.toFixed(4) + "%"
        : "left:" + (50 - width).toFixed(4) + "%;width:" + width.toFixed(4) + "%";
      html +=
        '<div class="bar-row">' +
          '<div class="bar-name"><b>' + esc(row.ticker) + "</b>" + esc(row.name) + "</div>" +
          '<div class="track" aria-hidden="true"><i class="' + pct.cls + '" style="' + style + '"></i></div>' +
          '<div class="bar-val ' + pct.cls + '">' + esc(pct.text) + "</div>" +
          '<div class="bar-rank">' + esc(row.ranks["9"]) + "</div>" +
        "</div>";
    });
    html += "</div>";
    section.innerHTML = html;
    section.hidden = false;
  }

  function renderTable(rows) {
    var body = rows.map(function (row) {
      var cells =
        '<td class="sticky">' + esc(row.ticker) + "</td>" +
        '<td class="name">' + esc(row.name) + "</td>" +
        '<td class="num">' + esc(fmtPx(row.adjClose)) + "</td>";
      WINDOWS.forEach(function (months) {
        var ret = row.returns[months];
        var rank = row.ranks[months];
        var key = months === "9" ? " key" : "";
        if (ret === null || ret === undefined) {
          cells += '<td class="num' + key + '"><span class="na">n/a</span></td>';
          cells += '<td class="num' + key + '"><span class="na">n/a</span></td>';
          return;
        }
        var pct = fmtPct(ret);
        var rankCls = rank === 1 ? "rank top" : "rank";
        cells +=
          '<td class="num' + key + '"><span class="' + pct.cls + '" title="' + esc(tip(row, months)) + '">' +
          esc(pct.text) + "</span></td>";
        cells += '<td class="num' + key + '"><span class="' + rankCls + '">' + esc(rank) + "</span></td>";
      });
      return "<tr>" + cells + "</tr>";
    }).join("");
    document.getElementById("rows").innerHTML = body;
    markSort();
  }

  function markSort() {
    document.querySelectorAll("th").forEach(function (th) {
      th.classList.remove("sorted");
      var button = th.querySelector("button");
      if (!button) return;
      if (button.getAttribute("data-sort") === state.key) {
        th.classList.add("sorted");
        button.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
      } else {
        button.removeAttribute("aria-sort");
      }
    });
  }

  function render() {
    var rows = state.payload.rows.slice().sort(compare);
    renderFacts(state.payload);
    renderSnapshot(state.payload.rows);
    renderTable(rows);
  }

  function defaultDir(key) {
    if (key.indexOf("rank:") === 0 || key === "ticker" || key === "name") return "asc";
    return "desc";
  }

  document.getElementById("board").addEventListener("click", function (event) {
    var button = event.target.closest("button[data-sort]");
    if (!button || !state.payload) return;
    var key = button.getAttribute("data-sort");
    if (state.key === key) state.dir = state.dir === "asc" ? "desc" : "asc";
    else {
      state.key = key;
      state.dir = defaultDir(key);
    }
    render();
  });

  fetch("./data/returns.json", { cache: "no-store" })
    .then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    })
    .then(function (payload) {
      if (!payload || !Array.isArray(payload.rows) || payload.rows.length !== 11) {
        throw new Error("收益数据不完整");
      }
      state.payload = payload;
      render();
    })
    .catch(function (error) {
      document.getElementById("facts").innerHTML =
        '<p>未能读取 ./data/returns.json（' + esc(error.message) + "）。</p>";
      document.getElementById("rows").innerHTML =
        '<tr><td class="wait" colspan="13">数据暂时无法显示。</td></tr>';
    });
})();
