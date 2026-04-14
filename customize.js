// =============================================
// SmartSake — Customize System v1
// Theme presets, custom colors, drag-to-reorder
// =============================================

(function () {
  'use strict';

  // ---- Theme Definitions ----
  const THEMES = {
    default: {
      label: 'Default',
      swatchBg: '#f1f2f5',
      swatchBorder: '#d40000',
      vars: {
        '--color-bg':            '#f1f2f5',
        '--color-surface':       '#ffffff',
        '--color-border':        'rgba(0,0,0,0.07)',
        '--color-border-strong': 'rgba(0,0,0,0.14)',
        '--color-text-1':        '#111111',
        '--color-text-2':        '#555555',
        '--color-text-3':        '#888888',
        '--color-bg-subtle':     '#f5f5f7',
        '--color-bg-tinted':     '#eaeaee',
        '--color-brand':         '#d40000',
        '--color-brand-hover':   '#b00000',
        '--color-brand-light':   '#ff6b6b',
        '--color-run-name':      '#1e3a8a',
        '--color-accent':        '#157efb',
        '--color-accent-hover':  '#0f6cd4',
      }
    },
    dark: {
      label: 'Dark',
      swatchBg: '#0f1015',
      swatchBorder: '#ff5555',
      vars: {
        '--color-bg':            '#111114',
        '--color-surface':       '#1c1c20',
        '--color-border':        'rgba(255,255,255,0.08)',
        '--color-border-strong': 'rgba(255,255,255,0.15)',
        '--color-text-1':        '#f0f0f0',
        '--color-text-2':        '#a0a0a8',
        '--color-text-3':        '#666672',
        '--color-bg-subtle':     '#28282e',
        '--color-bg-tinted':     '#323238',
        '--color-brand':         '#ff5555',
        '--color-brand-hover':   '#cc3333',
        '--color-brand-light':   '#ff8888',
        '--color-run-name':      '#93c5fd',
        '--color-accent':        '#5599ff',
        '--color-accent-hover':  '#4488ee',
      }
    },
    sake: {
      label: 'Sake',
      swatchBg: '#fdf6ea',
      swatchBorder: '#c25c1a',
      vars: {
        '--color-bg':            '#fdf6ea',
        '--color-surface':       '#fffcf6',
        '--color-border':        'rgba(80,50,20,0.08)',
        '--color-border-strong': 'rgba(80,50,20,0.18)',
        '--color-text-1':        '#1a1209',
        '--color-text-2':        '#6b5a3e',
        '--color-text-3':        '#a09070',
        '--color-bg-subtle':     '#f5ede0',
        '--color-bg-tinted':     '#ecdcc8',
        '--color-brand':         '#c25c1a',
        '--color-brand-hover':   '#9e4a14',
        '--color-brand-light':   '#e8844a',
        '--color-run-name':      '#3d7a6c',
        '--color-accent':        '#3d7a6c',
        '--color-accent-hover':  '#2e6258',
      }
    },
    minimal: {
      label: 'Minimal',
      swatchBg: '#f7f7f7',
      swatchBorder: '#1a1a1a',
      vars: {
        '--color-bg':            '#f7f7f7',
        '--color-surface':       '#ffffff',
        '--color-border':        'rgba(0,0,0,0.06)',
        '--color-border-strong': 'rgba(0,0,0,0.13)',
        '--color-text-1':        '#111111',
        '--color-text-2':        '#666666',
        '--color-text-3':        '#999999',
        '--color-bg-subtle':     '#f0f0f0',
        '--color-bg-tinted':     '#e4e4e4',
        '--color-brand':         '#1a1a1a',
        '--color-brand-hover':   '#000000',
        '--color-brand-light':   '#555555',
        '--color-run-name':      '#1a1a1a',
        '--color-accent':        '#1a1a1a',
        '--color-accent-hover':  '#000000',
      }
    }
  };

  // ---- State ----
  let currentTheme  = localStorage.getItem('ss-theme')  || 'dark';
  let customBrand   = localStorage.getItem('ss-custom-brand')  || '#d40000';
  let customAccent  = localStorage.getItem('ss-custom-accent') || '#157efb';
  let editMode  = false;
  let panelOpen = false;
  let dragSrc   = null;

  // ---- Theme Helpers ----

  function shadeColor(hex, pct) {
    const n = parseInt(hex.replace('#', ''), 16);
    const clamp = v => Math.min(255, Math.max(0, v));
    const r = clamp((n >> 16)         + Math.round(255 * pct / 100));
    const g = clamp(((n >> 8) & 0xff) + Math.round(255 * pct / 100));
    const b = clamp((n & 0xff)        + Math.round(255 * pct / 100));
    return '#' + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
  }

  function buildCustomVars() {
    return Object.assign({}, THEMES.default.vars, {
      '--color-brand':         customBrand,
      '--color-brand-hover':   shadeColor(customBrand, -20),
      '--color-brand-light':   shadeColor(customBrand, 28),
      '--color-accent':        customAccent,
      '--color-accent-hover':  shadeColor(customAccent, -12),
    });
  }

  function applyTheme(id) {
    const vars = id === 'custom'
      ? buildCustomVars()
      : (THEMES[id] ? THEMES[id].vars : THEMES.default.vars);

    const root = document.documentElement;
    Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
    // Also set attribute so CSS [data-theme] blocks fire (e.g. for Chart.js overrides)
    root.setAttribute('data-theme', id);

    document.querySelectorAll('.theme-swatch').forEach(el => {
      el.classList.toggle('theme-swatch--active', el.dataset.theme === id);
    });
  }

  // ---- Drag and Drop ----

  function makeDraggable(container, selector, storageKey) {
    function getItems() {
      return Array.from(container.querySelectorAll(selector));
    }

    function saveOrder() {
      const ids = getItems().map(el => el.dataset.widgetId).filter(Boolean);
      if (ids.length) localStorage.setItem(storageKey, JSON.stringify(ids));
    }

    function restoreOrder() {
      try {
        const saved = localStorage.getItem(storageKey);
        if (!saved) return;
        const ids = JSON.parse(saved);
        if (!Array.isArray(ids)) return;
        ids.forEach(id => {
          const el = container.querySelector('[data-widget-id="' + id + '"]');
          if (el) container.appendChild(el);
        });
      } catch (e) { /* ignore malformed storage */ }
    }

    restoreOrder();

    container.addEventListener('dragstart', function (e) {
      const item = e.target.closest(selector);
      if (!item) return;
      dragSrc = item;
      item.classList.add('widget-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', item.dataset.widgetId || '');
    });

    container.addEventListener('dragend', function (e) {
      const item = e.target.closest(selector);
      if (item) item.classList.remove('widget-dragging');
      container.querySelectorAll('.widget-drag-over').forEach(el => {
        el.classList.remove('widget-drag-over');
      });
      dragSrc = null;
      saveOrder();
      // Notify Chart.js to reflow after reorder
      setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 60);
    });

    container.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const item = e.target.closest(selector);
      if (item && item !== dragSrc) {
        container.querySelectorAll('.widget-drag-over').forEach(el => {
          el.classList.remove('widget-drag-over');
        });
        item.classList.add('widget-drag-over');
      }
    });

    container.addEventListener('dragleave', function (e) {
      const item = e.target.closest(selector);
      if (item && !item.contains(e.relatedTarget)) {
        item.classList.remove('widget-drag-over');
      }
    });

    container.addEventListener('drop', function (e) {
      e.preventDefault();
      const target = e.target.closest(selector);
      if (!target || !dragSrc || target === dragSrc) return;
      target.classList.remove('widget-drag-over');

      const items = getItems();
      const si = items.indexOf(dragSrc);
      const ti = items.indexOf(target);
      if (si < ti) {
        container.insertBefore(dragSrc, target.nextSibling);
      } else {
        container.insertBefore(dragSrc, target);
      }
    });
  }

  // ---- Edit Mode ----

  function setEditMode(on) {
    editMode = on;
    document.body.classList.toggle('layout-edit-mode', on);

    const btn = document.getElementById('edit-layout-btn');
    if (btn) {
      btn.textContent = on ? 'Done Editing' : 'Edit Layout';
      btn.classList.toggle('active', on);
    }

    const draggables = [].concat(
      Array.from(document.querySelectorAll('.left > .long')),
      Array.from(document.querySelectorAll('.zones-combined > .zone-combined'))
    );
    draggables.forEach(function (el) { el.draggable = on; });
  }

  // ---- Panel ----

  function togglePanel(force) {
    panelOpen = (force !== undefined) ? force : !panelOpen;
    const panel  = document.getElementById('settings-panel');
    const toggle = document.getElementById('settings-toggle') || document.getElementById('settings-btn');
    if (panel)  panel.classList.toggle('settings-panel--open', panelOpen);
    if (toggle) toggle.classList.toggle('settings-toggle--active', panelOpen);
  }

  function buildSwatchHTML() {
    return Object.entries(THEMES).map(function (_ref) {
      var id = _ref[0], t = _ref[1];
      var active = (id === currentTheme) ? ' theme-swatch--active' : '';
      return '<button class="theme-swatch' + active + '" data-theme="' + id + '" title="' + t.label + '">' +
        '<span class="theme-swatch__preview" style="background:' + t.swatchBg + ';border-color:' + t.swatchBorder + '"></span>' +
        '<span class="theme-swatch__label">' + t.label + '</span>' +
        '</button>';
    }).join('') +
    '<button class="theme-swatch' + (currentTheme === 'custom' ? ' theme-swatch--active' : '') + '" data-theme="custom" title="Custom">' +
      '<span class="theme-swatch__preview theme-swatch__preview--custom"></span>' +
      '<span class="theme-swatch__label">Custom</span>' +
    '</button>';
  }

  function buildPanel() {
    var panel = document.createElement('div');
    panel.id = 'settings-panel';
    panel.className = 'settings-panel';

    panel.innerHTML =
      '<div class="settings-panel__header">' +
        '<span class="settings-panel__title">Customize</span>' +
        '<button class="settings-panel__close" id="settings-close" aria-label="Close">&times;</button>' +
      '</div>' +

      '<div class="settings-section">' +
        '<div class="settings-section__label">Theme</div>' +
        '<div class="theme-swatches">' + buildSwatchHTML() + '</div>' +
      '</div>' +

      '<div class="settings-section" id="custom-colors-section" style="' + (currentTheme === 'custom' ? '' : 'display:none') + '">' +
        '<div class="settings-section__label">Custom Colors</div>' +
        '<div class="color-picker-row">' +
          '<label class="color-picker-label">Brand' +
            '<input type="color" id="custom-brand-picker" value="' + customBrand + '">' +
          '</label>' +
          '<label class="color-picker-label">Accent' +
            '<input type="color" id="custom-accent-picker" value="' + customAccent + '">' +
          '</label>' +
        '</div>' +
      '</div>' +

      '<div class="settings-section">' +
        '<div class="settings-section__label">Layout</div>' +
        '<button class="settings-edit-btn" id="edit-layout-btn">Edit Layout</button>' +
        '<p class="settings-hint">Drag charts and zone cards to reorder. Tap "Done Editing" when finished.</p>' +
      '</div>';

    document.body.appendChild(panel);

    // Theme swatch clicks
    panel.querySelectorAll('.theme-swatch').forEach(function (sw) {
      sw.addEventListener('click', function () {
        var id = sw.dataset.theme;
        currentTheme = id;
        localStorage.setItem('ss-theme', id);
        var customSection = document.getElementById('custom-colors-section');
        if (customSection) customSection.style.display = (id === 'custom') ? '' : 'none';
        applyTheme(id);
      });
    });

    // Custom brand picker
    var brandPicker = panel.querySelector('#custom-brand-picker');
    if (brandPicker) {
      brandPicker.addEventListener('input', function (e) {
        customBrand = e.target.value;
        localStorage.setItem('ss-custom-brand', customBrand);
        if (currentTheme === 'custom') applyTheme('custom');
      });
    }

    // Custom accent picker
    var accentPicker = panel.querySelector('#custom-accent-picker');
    if (accentPicker) {
      accentPicker.addEventListener('input', function (e) {
        customAccent = e.target.value;
        localStorage.setItem('ss-custom-accent', customAccent);
        if (currentTheme === 'custom') applyTheme('custom');
      });
    }

    // Edit layout button
    var editBtn = panel.querySelector('#edit-layout-btn');
    if (editBtn) {
      editBtn.addEventListener('click', function () { setEditMode(!editMode); });
    }

    // Close button
    var closeBtn = panel.querySelector('#settings-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () { togglePanel(false); });
    }

    return panel;
  }

  function buildToggle() {
    // Prefer the header-embedded #settings-btn if the page has one
    var existing = document.getElementById('settings-btn');
    if (existing) {
      existing.addEventListener('click', function (e) { e.stopPropagation(); togglePanel(); });
      return existing;
    }
    // Fallback: build a floating toggle for pages without a header button
    var btn = document.createElement('button');
    btn.id = 'settings-toggle';
    btn.className = 'settings-toggle';
    btn.setAttribute('aria-label', 'Open customize panel');
    btn.innerHTML =
      '<svg viewBox="0 0 20 20" fill="none" width="18" height="18" aria-hidden="true">' +
        '<circle cx="10" cy="10" r="3" stroke="currentColor" stroke-width="1.6"/>' +
        '<path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.22 4.22l1.41 1.41M14.36 14.36l1.41 1.41M4.22 15.78l1.41-1.41M14.36 5.64l1.41-1.41"' +
          ' stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
      '</svg>';
    document.body.appendChild(btn);
    btn.addEventListener('click', function (e) { e.stopPropagation(); togglePanel(); });
    return btn;
  }

  function buildEditBanner() {
    var banner = document.createElement('div');
    banner.className = 'edit-mode-banner';
    banner.textContent = 'EDITING LAYOUT — DRAG TO REORDER';
    document.body.appendChild(banner);
  }

  // ---- Init ----

  function init() {
    // Apply saved or default theme
    applyTheme(currentTheme);

    // Build UI chrome
    buildPanel();
    buildToggle();
    buildEditBanner();

    // Wire drag on charts column
    var leftCol = document.querySelector('.left');
    if (leftCol) makeDraggable(leftCol, '.long', 'ss-chart-order');

    // Wire drag on zone grid
    var zonesGrid = document.querySelector('.zones-combined');
    if (zonesGrid) makeDraggable(zonesGrid, '.zone-combined', 'ss-zone-order');

    // Click outside to close panel
    document.addEventListener('click', function (e) {
      if (!panelOpen) return;
      var panel  = document.getElementById('settings-panel');
      var toggle = document.getElementById('settings-toggle');
      if (panel && toggle && !panel.contains(e.target) && !toggle.contains(e.target)) {
        togglePanel(false);
      }
    });

    // ESC to close panel or exit edit mode
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (editMode) { setEditMode(false); return; }
        if (panelOpen) { togglePanel(false); }
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
