// =============================================
// SmartSake — Customize System v2
// Theme presets + custom colors, modal UI
// =============================================

(function () {
  'use strict';

  // ---- Theme Definitions ----
  const THEMES = {
    dark: {
      label: 'Dark',
      swatchBg: '#111114',
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
    galaxy: {
      label: 'Galaxy',
      swatchBg: '#0a0a1a',
      swatchBorder: '#a855f7',
      vars: {
        '--color-bg':            '#0a0a1a',
        '--color-surface':       '#0f0f2a',
        '--color-border':        'rgba(168,85,247,0.18)',
        '--color-border-strong': 'rgba(168,85,247,0.35)',
        '--color-text-1':        '#e0d8ff',
        '--color-text-2':        '#a090cc',
        '--color-text-3':        '#6a5a99',
        '--color-bg-subtle':     '#16143a',
        '--color-bg-tinted':     '#1e1a48',
        '--color-brand':         '#a855f7',
        '--color-brand-hover':   '#8b3de0',
        '--color-brand-light':   '#c990ff',
        '--color-run-name':      '#c990ff',
        '--color-accent':        '#a855f7',
        '--color-accent-hover':  '#8b3de0',
      }
    },
    ocean: {
      label: 'Ocean',
      swatchBg: '#021a2b',
      swatchBorder: '#00b4d8',
      vars: {
        '--color-bg':            '#021a2b',
        '--color-surface':       '#032538',
        '--color-border':        'rgba(0,180,216,0.15)',
        '--color-border-strong': 'rgba(0,180,216,0.30)',
        '--color-text-1':        '#a8d8f0',
        '--color-text-2':        '#6aabcc',
        '--color-text-3':        '#3a7a99',
        '--color-bg-subtle':     '#063048',
        '--color-bg-tinted':     '#0a3d5a',
        '--color-brand':         '#00b4d8',
        '--color-brand-hover':   '#0090ae',
        '--color-brand-light':   '#48d8f8',
        '--color-run-name':      '#48d8f8',
        '--color-accent':        '#00b4d8',
        '--color-accent-hover':  '#0090ae',
      }
    },
    forest: {
      label: 'Forest',
      swatchBg: '#081a0f',
      swatchBorder: '#4caf80',
      vars: {
        '--color-bg':            '#081a0f',
        '--color-surface':       '#0f2416',
        '--color-border':        'rgba(76,175,128,0.15)',
        '--color-border-strong': 'rgba(76,175,128,0.30)',
        '--color-text-1':        '#c8e8d0',
        '--color-text-2':        '#80b890',
        '--color-text-3':        '#4a7a58',
        '--color-bg-subtle':     '#142e1c',
        '--color-bg-tinted':     '#1a3a24',
        '--color-brand':         '#4caf80',
        '--color-brand-hover':   '#3a8f66',
        '--color-brand-light':   '#80d4a8',
        '--color-run-name':      '#80d4a8',
        '--color-accent':        '#4caf80',
        '--color-accent-hover':  '#3a8f66',
      }
    },
    amber: {
      label: 'Amber',
      swatchBg: '#1a1000',
      swatchBorder: '#f0a020',
      vars: {
        '--color-bg':            '#1a1000',
        '--color-surface':       '#241800',
        '--color-border':        'rgba(240,160,32,0.15)',
        '--color-border-strong': 'rgba(240,160,32,0.30)',
        '--color-text-1':        '#f5e0a0',
        '--color-text-2':        '#c0a860',
        '--color-text-3':        '#887030',
        '--color-bg-subtle':     '#302000',
        '--color-bg-tinted':     '#3c2a00',
        '--color-brand':         '#f0a020',
        '--color-brand-hover':   '#c07f10',
        '--color-brand-light':   '#ffc050',
        '--color-run-name':      '#ffc050',
        '--color-accent':        '#f0a020',
        '--color-accent-hover':  '#c07f10',
      }
    },
    sakura: {
      label: 'Sakura',
      swatchBg: '#1a0a10',
      swatchBorder: '#e05080',
      vars: {
        '--color-bg':            '#1a0a10',
        '--color-surface':       '#260f18',
        '--color-border':        'rgba(224,80,128,0.15)',
        '--color-border-strong': 'rgba(224,80,128,0.30)',
        '--color-text-1':        '#f0d8e0',
        '--color-text-2':        '#c090a8',
        '--color-text-3':        '#885070',
        '--color-bg-subtle':     '#321520',
        '--color-bg-tinted':     '#3e1c2a',
        '--color-brand':         '#e05080',
        '--color-brand-hover':   '#b83060',
        '--color-brand-light':   '#f088a8',
        '--color-run-name':      '#f088a8',
        '--color-accent':        '#e05080',
        '--color-accent-hover':  '#b83060',
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
    light: {
      label: 'Light',
      swatchBg: '#f4f4f6',
      swatchBorder: '#2277cc',
      vars: {
        '--color-bg':            '#f4f4f6',
        '--color-surface':       '#ffffff',
        '--color-border':        'rgba(0,0,0,0.07)',
        '--color-border-strong': 'rgba(0,0,0,0.14)',
        '--color-text-1':        '#1a1a2e',
        '--color-text-2':        '#555566',
        '--color-text-3':        '#888899',
        '--color-bg-subtle':     '#ebebef',
        '--color-bg-tinted':     '#e0e0e8',
        '--color-brand':         '#2277cc',
        '--color-brand-hover':   '#1a5fa8',
        '--color-brand-light':   '#5599ee',
        '--color-run-name':      '#1a5fa8',
        '--color-accent':        '#2277cc',
        '--color-accent-hover':  '#1a5fa8',
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
    },
  };

  // ---- State ----
  const _savedTheme = localStorage.getItem('ss-theme') || 'dark';
  let currentTheme = (THEMES[_savedTheme] || _savedTheme === 'custom') ? _savedTheme : 'dark';
  let customBrand  = localStorage.getItem('ss-custom-brand')  || '#d40000';
  let customAccent = localStorage.getItem('ss-custom-accent') || '#157efb';

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
    return Object.assign({}, THEMES.dark.vars, {
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
      : (THEMES[id] ? THEMES[id].vars : THEMES.dark.vars);

    const root = document.documentElement;
    Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
    root.setAttribute('data-theme', id);

    document.querySelectorAll('.theme-swatch').forEach(el => {
      el.classList.toggle('theme-swatch--active', el.dataset.theme === id);
    });
  }

  // ---- Modal ----

  function openModal() {
    var modal = document.getElementById('customize-modal');
    if (modal) modal.classList.add('is-open');
  }

  function closeModal() {
    var modal = document.getElementById('customize-modal');
    if (modal) modal.classList.remove('is-open');
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

  function buildModal() {
    var hasDashboardSettings = !!document.getElementById('settings-modal');

    var overlay = document.createElement('div');
    overlay.id = 'customize-modal';
    overlay.className = 'modal-overlay';

    overlay.innerHTML =
      '<div class="modal-popup customize-modal-popup">' +
        '<div class="modal-header">' +
          '<h3 class="modal-title">Settings</h3>' +
          '<button class="modal-close" id="customize-close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="modal-content">' +

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

          (hasDashboardSettings
            ? '<div class="settings-section">' +
                '<div class="settings-section__label">Run</div>' +
                '<button class="settings-edit-btn" id="run-settings-btn">Run Settings</button>' +
              '</div>'
            : '') +

        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    // Close on backdrop click
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeModal();
    });

    // Close button
    overlay.querySelector('#customize-close').addEventListener('click', closeModal);

    // Theme swatch clicks
    overlay.querySelectorAll('.theme-swatch').forEach(function (sw) {
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
    var brandPicker = overlay.querySelector('#custom-brand-picker');
    if (brandPicker) {
      brandPicker.addEventListener('input', function (e) {
        customBrand = e.target.value;
        localStorage.setItem('ss-custom-brand', customBrand);
        if (currentTheme === 'custom') applyTheme('custom');
      });
    }

    // Custom accent picker
    var accentPicker = overlay.querySelector('#custom-accent-picker');
    if (accentPicker) {
      accentPicker.addEventListener('input', function (e) {
        customAccent = e.target.value;
        localStorage.setItem('ss-custom-accent', customAccent);
        if (currentTheme === 'custom') applyTheme('custom');
      });
    }

    // Run Settings button — delegates to dashboard's openSettings() if available
    var runSettingsBtn = overlay.querySelector('#run-settings-btn');
    if (runSettingsBtn) {
      runSettingsBtn.addEventListener('click', function () {
        closeModal();
        if (typeof window.openDashboardSettings === 'function') {
          window.openDashboardSettings();
        }
      });
    }

    return overlay;
  }

  function wireSettingsBtn() {
    var btn = document.getElementById('settings-btn');
    if (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal();
      });
    }
  }

  // ---- Init ----

  function init() {
    applyTheme(currentTheme);
    buildModal();
    wireSettingsBtn();

    // ESC to close
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeModal();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
