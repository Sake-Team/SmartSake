/* dashboard-phase2.js — Phase 2 feature layer for SmartSake dashboard
 *
 * Depends on globals set by dashboard.html's inline IIFEs:
 *   window._runId            — active run id (int)
 *   window._tempChart        — Chart.js instance for temperature chart
 *   window._envChart         — Chart.js instance for env (humidity) chart
 *   window._weightChart      — Chart.js instance for weight chart
 *   window._sampleElapsedMin — latest elapsed_min value pushed to live charts
 *   window._chartLabels      — shared elapsed-minute labels array (live temp chart)
 *
 * All chart updates use chart.update('none') for Pi 4B performance.
 */

/* ── 1. Stage Marker IIFE ───────────────────────────────────────────────────── */
// Shared HTML-escape helper used across the IIFE modules below.
// Server-side input validation (server.py) already rejects HTML metachars in
// run names + event labels, but we escape on render too as defense in depth.
function escHtmlPhase2(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

(function stageMarkerModule() {
  "use strict";

  const KOJI_STAGES = [
    { label: "Hiki-komi",        color: "#6c8ebf" },
    { label: "Kiri-kaeshi",      color: "#82b366" },
    { label: "Naka-shigoto",     color: "#d79b00" },
    { label: "Shimai-shigoto",   color: "#ae4132" },
  ];

  let _markers = [];          // [{id, label, elapsed_min}]
  let _pendingSave = false;

  // Inline Chart.js plugin — draws vertical dashed lines + rotated labels
  const stageMarkerPlugin = {
    id: "stageMarkers",
    afterDraw(chart) {
      if (!_markers.length) return;
      const { ctx, scales, chartArea } = chart;
      const xScale = scales.x;
      if (!xScale || !chartArea) return;

      // Find pixel for an elapsed_min value in the category scale
      const elapsedMins = window._chartElapsedMins || [];
      function elapsedToPx(em) {
        if (!elapsedMins.length) return null;
        let bestIdx = 0, bestDist = Infinity;
        for (let i = 0; i < elapsedMins.length; i++) {
          const d = Math.abs(elapsedMins[i] - em);
          if (d < bestDist) { bestDist = d; bestIdx = i; }
        }
        const lbl = (chart.data.labels || [])[bestIdx];
        return lbl != null ? xScale.getPixelForValue(lbl) : null;
      }

      ctx.save();
      _markers.forEach(m => {
        const color = (KOJI_STAGES.find(s => s.label === m.label) || {}).color || "#888";
        const xPx = elapsedToPx(m.elapsed_min);
        if (xPx == null || xPx < chartArea.left || xPx > chartArea.right) return;

        ctx.beginPath();
        ctx.setLineDash([4, 3]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.moveTo(xPx, chartArea.top);
        ctx.lineTo(xPx, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);

        // Rotated label
        ctx.save();
        ctx.translate(xPx - 3, chartArea.top + 6);
        ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = color;
        ctx.font = "10px sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(m.label, 0, 0);
        ctx.restore();
      });
      ctx.restore();
    },
  };

  function _registerPlugin() {
    const chart = window._tempChart;
    if (!chart) return;
    if (!chart.config.plugins) chart.config.plugins = [];
    // Avoid double-registration
    if (!chart.config.plugins.find(p => p.id === "stageMarkers")) {
      chart.config.plugins.push(stageMarkerPlugin);
    }
  }

  function _loadMarkers() {
    const runId = window._runId;
    if (!runId) return;
    fetch(`/api/runs/${runId}/events`)
      .then(r => r.json())
      .then(data => {
        _markers = Array.isArray(data) ? data : [];
        _renderMarkerList();
        const chart = window._tempChart;
        if (chart) chart.update("none");
      })
      .catch(() => {});
  }

  function _addMarker(label, elapsed_min) {
    if (_pendingSave) return;
    _pendingSave = true;
    const runId = window._runId;
    fetch(`/api/runs/${runId}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, elapsed_min, event_type: "stage" }),
    })
      .then(r => r.json())
      .then(() => _loadMarkers())
      .catch(() => {})
      .finally(() => { _pendingSave = false; });
  }

  function _deleteMarker(eventId) {
    const runId = window._runId;
    fetch(`/api/runs/${runId}/events/${eventId}`, { method: "DELETE" })
      .then(() => _loadMarkers())
      .catch(() => {});
  }

  function _renderMarkerList() {
    const el = document.getElementById("stage-marker-list");
    if (!el) return;
    if (!_markers.length) {
      el.innerHTML = "<span class='marker-list__empty'>No stages logged yet</span>";
      return;
    }
    el.innerHTML = _markers.map(m => {
      const color = (KOJI_STAGES.find(s => s.label === m.label) || {}).color || "#888";
      const h = Math.floor(m.elapsed_min / 60);
      const min = m.elapsed_min % 60;
      const timeStr = `${h}h ${min}m`;
      return `<span class="marker-chip" style="border-color:${color}">
        <span class="marker-chip__dot" style="background:${color}"></span>
        ${escHtmlPhase2(m.label)} &middot; ${timeStr}
        <button class="marker-chip__del" data-id="${m.id}" title="Remove">&times;</button>
      </span>`;
    }).join("");

    el.querySelectorAll(".marker-chip__del").forEach(btn => {
      btn.addEventListener("click", () => _deleteMarker(Number(btn.dataset.id)));
    });
  }

  function _buildToolbar() {
    const toolbar = document.getElementById("stage-marker-toolbar");
    if (!toolbar) return;

    // Build stage buttons
    KOJI_STAGES.forEach(stage => {
      const btn = document.createElement("button");
      btn.className = "marker-btn";
      btn.style.borderColor = stage.color;
      btn.textContent = stage.label;
      btn.title = `Log "${stage.label}" at current elapsed time`;
      btn.addEventListener("click", () => {
        const elapsed = Math.round(window._sampleElapsedMin || 0);
        _addMarker(stage.label, elapsed);
      });
      toolbar.appendChild(btn);
    });
  }

  // Init after DOM + chart ready
  function _init() {
    _buildToolbar();
    _registerPlugin();
    _loadMarkers();
    // Refresh markers every 30s — with visibility guard to avoid polling when hidden
    var _stageTimer = setInterval(_loadMarkers, 30000);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { clearInterval(_stageTimer); _stageTimer = null; }
      else if (!_stageTimer) { _loadMarkers(); _stageTimer = setInterval(_loadMarkers, 30000); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(_init, 500));
  } else {
    setTimeout(_init, 500);
  }
})();


/* ── 2. Overlay Comparison IIFE ─────────────────────────────────────────────── */
(function overlayModule() {
  "use strict";

  const MAX_OVERLAYS = 3;
  const GHOST_COLORS = ["#9b59b6", "#e67e22", "#1abc9c"];
  const DOWNSAMPLE_N = 300;

  let _activeOverlays = [];   // [{run_id, run_name, datasetIndices:[]}]

  function _mapToCurrentLabels(labels, elapsedMins, samples) {
    /* samples: [{elapsed_min, value}]
     * labels:  string labels array (current run's Chart.js X axis)
     * elapsedMins: parallel numeric elapsed-minute values for each label
     * Returns array of y-values aligned to labels, nulls where no data. */
    if (!samples.length) return labels.map(() => null);
    if (!elapsedMins.length) return labels.map(() => null);
    const out = [];
    let si = 0;
    for (let li = 0; li < labels.length; li++) {
      const em = elapsedMins[li];
      // Advance sample pointer
      while (si < samples.length - 1 && samples[si + 1].elapsed_min <= em) si++;
      const s = samples[si];
      // Only emit if within ±5 minutes of label
      out.push(Math.abs(s.elapsed_min - em) <= 5 ? s.value : null);
    }
    return out;
  }

  function _removeOverlay(run_id) {
    const chart = window._tempChart;
    if (!chart) return;
    const ov = _activeOverlays.find(o => o.run_id === run_id);
    if (!ov) return;
    // Remove by label prefix (avoids stale index bugs when multiple overlays exist)
    const prefix = ov.run_name + " TC";
    chart.data.datasets = chart.data.datasets.filter(ds => !ds.label.startsWith(prefix));
    _activeOverlays = _activeOverlays.filter(o => o.run_id !== run_id);
    chart.update("none");
    _renderOverlayChips();
  }

  function _addOverlay(run_id, run_name) {
    if (_activeOverlays.length >= MAX_OVERLAYS) return;
    if (_activeOverlays.find(o => o.run_id === run_id)) return;
    const chart = window._tempChart;
    if (!chart) return;

    fetch(`/api/runs/${run_id}/readings?n=${DOWNSAMPLE_N}`)
      .then(r => r.json())
      .then(readings => {
        fetch(`/api/runs/${run_id}`)
          .then(r => r.json())
          .then(run => {
            const startedAt = new Date(run.started_at);
            // Build [{elapsed_min, tc1}..{tc6}] per zone
            const zones = [1, 2, 3, 4, 5, 6];
            const colorIdx = _activeOverlays.length % GHOST_COLORS.length;
            const baseColor = GHOST_COLORS[colorIdx];

            const currentLabels = chart.data.labels || [];
            const currentElapsed = window._chartElapsedMins || [];
            const hex = baseColor.replace("#", "");
            const cr = parseInt(hex.slice(0,2),16);
            const cg = parseInt(hex.slice(2,4),16);
            const cb = parseInt(hex.slice(4,6),16);

            zones.forEach((z, zi) => {
              const samples = readings
                .filter(r => r[`tc${z}`] != null)
                .map(r => ({
                  elapsed_min: (new Date(r.recorded_at) - startedAt) / 60000,
                  value: r[`tc${z}`],
                }));
              if (!samples.length) return;
              const yData = _mapToCurrentLabels(currentLabels, currentElapsed, samples);
              const alpha = Math.max(0.08, 0.35 - zi * 0.04);
              chart.data.datasets.push({
                label: `${run_name} TC${z}`,
                data: yData,
                borderColor: `rgba(${cr},${cg},${cb},${alpha})`,
                backgroundColor: "transparent",
                borderWidth: 1,
                pointRadius: 0,
                spanGaps: true,
                tension: 0,
              });
            });

            _activeOverlays.push({ run_id, run_name });
            chart.update("none");
            _renderOverlayChips();
          });
      })
      .catch(() => {});
  }

  function _renderOverlayChips() {
    const el = document.getElementById("overlay-chips");
    if (!el) return;
    el.innerHTML = _activeOverlays.map((ov, i) => {
      const color = GHOST_COLORS[i % GHOST_COLORS.length];
      return `<span class="overlay-chip" style="border-color:${color}">
        <span class="overlay-chip__dot" style="background:${color}"></span>
        ${escHtmlPhase2(ov.run_name)}
        <button class="overlay-chip__del" data-id="${ov.run_id}" title="Remove">&times;</button>
      </span>`;
    }).join("");
    el.querySelectorAll(".overlay-chip__del").forEach(btn => {
      btn.addEventListener("click", () => _removeOverlay(Number(btn.dataset.id)));
    });
  }

  function _populateSelector() {
    const sel = document.getElementById("overlay-run-select");
    if (!sel) return;
    fetch("/api/runs/completed")
      .then(r => r.json())
      .then(runs => {
        sel.innerHTML = '<option value="">— Add past run overlay —</option>' +
          runs.map(r =>
            `<option value="${r.id}">${escHtmlPhase2(r.name)}</option>`
          ).join("");
        sel.addEventListener("change", () => {
          const val = Number(sel.value);
          if (!val) return;
          const run = runs.find(r => r.id === val);
          if (run) _addOverlay(run.id, run.name);
          sel.value = "";
        });
      })
      .catch(() => {});
  }

  function _init() {
    _populateSelector();
    _renderOverlayChips();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(_init, 600));
  } else {
    setTimeout(_init, 600);
  }
})();


/* ── 3. Weight Analytics IIFE ───────────────────────────────────────────────── */
(function weightModule() {
  "use strict";

  let _weightBandPlugin = null;
  let _targetMin = null;
  let _targetMax = null;

  function _buildBandPlugin() {
    return {
      id: "weightBand",
      beforeDraw(chart) {
        if (_targetMin == null || _targetMax == null) return;
        const { ctx, scales, chartArea } = chart;
        const yScale = scales.y;
        if (!yScale || !chartArea) return;
        const yTop = yScale.getPixelForValue(_targetMax);
        const yBot = yScale.getPixelForValue(_targetMin);
        if (yTop > chartArea.bottom || yBot < chartArea.top) return;
        const top = Math.max(yTop, chartArea.top);
        const bot = Math.min(yBot, chartArea.bottom);
        ctx.save();
        ctx.fillStyle = "rgba(130,179,102,0.12)";
        ctx.fillRect(chartArea.left, top, chartArea.right - chartArea.left, bot - top);
        ctx.restore();
      },
    };
  }

  function _registerWeightPlugin() {
    const chart = window._weightChart;
    if (!chart || _weightBandPlugin) return;
    _weightBandPlugin = _buildBandPlugin();
    if (!chart.config.plugins) chart.config.plugins = [];
    if (!chart.config.plugins.find(p => p.id === "weightBand")) {
      chart.config.plugins.push(_weightBandPlugin);
    }
  }

  function _updateStats(data) {
    const fmt = (v, d=2) => v == null ? "—" : Number(v).toFixed(d);

    const setEl = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    setEl("wt-initial",  data.initial_lbs  != null ? fmt(data.initial_lbs) + " lbs" : "—");
    setEl("wt-current",  data.current_lbs  != null ? fmt(data.current_lbs) + " lbs" : "—");
    setEl("wt-loss-lbs", data.loss_lbs     != null ? fmt(data.loss_lbs) + " lbs" : "—");
    setEl("wt-loss-pct", data.loss_pct     != null ? fmt(data.loss_pct) + "%" : "—");
    setEl("wt-rate",     data.rate_lbs_per_hr != null
      ? fmt(data.rate_lbs_per_hr, 3) + " lbs/hr" : "—");

    _targetMin = data.weight_target_min;
    _targetMax = data.weight_target_max;

    const tgtEl = document.getElementById("wt-target");
    if (tgtEl) {
      tgtEl.textContent = (_targetMin != null && _targetMax != null)
        ? `${fmt(_targetMin)}–${fmt(_targetMax)} lbs target`
        : "No target set";
    }
  }

  function _pushToChart(samples) {
    const chart = window._weightChart;
    if (!chart || !samples.length) return;
    chart.data.labels = samples.map(s => s.elapsed_min);
    chart.data.datasets[0].data = samples.map(s => s.weight_total_lbs != null ? s.weight_total_lbs : s.weight_lbs);
    chart.update("none");
  }

  function _poll() {
    const runId = window._runId;
    if (!runId) return;
    fetch(`/api/runs/${runId}/weight-analytics`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        _updateStats(data);
        _pushToChart(data.samples);
      })
      .catch(() => {});
  }

  function _setupTargetForm() {
    const form = document.getElementById("wt-target-form");
    if (!form) return;
    form.addEventListener("submit", e => {
      e.preventDefault();
      const minEl = form.querySelector("[name=wt_min]");
      const maxEl = form.querySelector("[name=wt_max]");
      const tMin = parseFloat(minEl.value);
      const tMax = parseFloat(maxEl.value);
      if (isNaN(tMin) || isNaN(tMax) || tMin >= tMax) return;
      const runId = window._runId;
      fetch(`/api/runs/${runId}/weight-targets`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_min: tMin, target_max: tMax }),
      }).then(() => _poll()).catch(() => {});
      const modal = document.getElementById("wt-target-modal");
      if (modal) modal.hidden = true;
    });
  }

  function _init() {
    _registerWeightPlugin();
    _setupTargetForm();
    _poll();
    var _weightTimer = setInterval(_poll, 30000);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { clearInterval(_weightTimer); _weightTimer = null; }
      else if (!_weightTimer) { _poll(); _weightTimer = setInterval(_poll, 30000); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(_init, 700));
  } else {
    setTimeout(_init, 700);
  }
})();


/* ── 4. Humidity Band IIFE ───────────────────────────────────────────────────── */
(function humidityBandModule() {
  "use strict";

  let _humMin = null;
  let _humMax = null;

  const humBandPlugin = {
    id: "humidityBand",
    beforeDraw(chart) {
      if (_humMin == null || _humMax == null) return;
      const { ctx, scales, chartArea } = chart;
      // env chart uses 'yHum' axis (0–100 %RH, right side)
      const yScale = scales.yHum || scales.y1 || scales.y;
      if (!yScale || !chartArea) return;

      const yTop = yScale.getPixelForValue(_humMax);
      const yBot = yScale.getPixelForValue(_humMin);
      if (Math.min(yTop, yBot) > chartArea.bottom || Math.max(yTop, yBot) < chartArea.top) return;

      const top = Math.max(Math.min(yTop, yBot), chartArea.top);
      const bot = Math.min(Math.max(yTop, yBot), chartArea.bottom);

      ctx.save();
      ctx.fillStyle = "rgba(130,179,102,0.10)";
      ctx.fillRect(chartArea.left, top, chartArea.right - chartArea.left, bot - top);

      // Dashed boundary lines
      ctx.strokeStyle = "rgba(130,179,102,0.5)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      [[yTop, _humMax], [yBot, _humMin]].forEach(([py]) => {
        const clamped = Math.max(chartArea.top, Math.min(py, chartArea.bottom));
        ctx.beginPath();
        ctx.moveTo(chartArea.left, clamped);
        ctx.lineTo(chartArea.right, clamped);
        ctx.stroke();
      });
      ctx.setLineDash([]);
      ctx.restore();
    },
  };

  function _registerPlugin() {
    const chart = window._envChart;
    if (!chart) return;
    if (!chart.config.plugins) chart.config.plugins = [];
    if (!chart.config.plugins.find(p => p.id === "humidityBand")) {
      chart.config.plugins.push(humBandPlugin);
    }
  }

  function _loadTargets() {
    const runId = window._runId;
    if (!runId) return;
    fetch(`/api/runs/${runId}`)
      .then(r => r.json())
      .then(run => {
        _humMin = run.humidity_target_min;
        _humMax = run.humidity_target_max;
        const chart = window._envChart;
        if (chart) chart.update("none");
        // Pre-fill form fields
        const minEl = document.querySelector("[name=hum_min]");
        const maxEl = document.querySelector("[name=hum_max]");
        if (minEl && _humMin != null) minEl.value = _humMin;
        if (maxEl && _humMax != null) maxEl.value = _humMax;
      })
      .catch(() => {});
  }

  function _setupForm() {
    const form = document.getElementById("hum-target-form");
    if (!form) return;
    form.addEventListener("submit", e => {
      e.preventDefault();
      const minEl = form.querySelector("[name=hum_min]");
      const maxEl = form.querySelector("[name=hum_max]");
      const tMin = parseFloat(minEl.value);
      const tMax = parseFloat(maxEl.value);
      if (isNaN(tMin) || isNaN(tMax) || tMin < 0 || tMax > 100 || tMin >= tMax) return;
      const runId = window._runId;
      fetch(`/api/runs/${runId}/humidity-targets`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_min: tMin, target_max: tMax }),
      }).then(() => {
        _humMin = tMin;
        _humMax = tMax;
        const chart = window._envChart;
        if (chart) chart.update("none");
        const modal = document.getElementById("hum-target-modal");
        if (modal) modal.hidden = true;
      }).catch(() => {});
    });
  }

  function _init() {
    _registerPlugin();
    _loadTargets();
    _setupForm();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(_init, 800));
  } else {
    setTimeout(_init, 800);
  }
})();
