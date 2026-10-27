// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Root Application Component.
 * Vanilla Web Component - no framework, no dependencies.
 * Manages layout, theme, and hash-based routing.
 *
 * Routes (Phase 3B: every nav item is a real view over a real
 * backend — no placeholder routes):
 *   #dashboard  -> <pae-dashboard>    #risk       -> <pae-risk>
 *   #factors    -> <pae-factors>      #montecarlo -> <pae-scenarios>
 *   #stress     -> <pae-scenarios>    #optimizer  -> <pae-optimize>
 *   #carry      -> <pae-carry>        #journal    -> <pae-journal>
 *   #knowledge  -> <pae-knowledge>    #import     -> <pae-import>
 *   #holdings   -> <pae-holdings>
 *   (unknown)   -> placeholder; no nav item leads here
 */

import {
  fetchPortfolios,
  resolveSelectedPortfolioId,
  storeSelectedPortfolioId,
} from '../portfolio-selection.js';

const VALID_THEMES = ['light', 'dark'] as const;
type Theme = typeof VALID_THEMES[number];

class PaeApp extends HTMLElement {
  private shadow: ShadowRoot;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.initTheme();
    this.render();
    this.setupRouting();
    void this.loadPortfolios();
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * Populate the shell's portfolio selector (Phase 3D, W1): the ONE
   * shared selection every data view reads. Defaults to the first
   * portfolio with holdings (see portfolio-selection.ts), so a
   * seeded install opens on the populated portfolio, not the empty
   * auto-created Default.
   */
  private async loadPortfolios(): Promise<void> {
    const sel = this.shadow.querySelector(
      '#portfolio-select',
    ) as HTMLSelectElement | null;
    if (!sel) return;
    try {
      const portfolios = await fetchPortfolios();
      if (!portfolios.length) {
        sel.innerHTML = '<option value="">No portfolios yet</option>';
        sel.disabled = true;
        return;
      }
      const selected = resolveSelectedPortfolioId(portfolios);
      sel.innerHTML = portfolios
        .map(
          (p) =>
            `<option value="${this.esc(p.id)}">${this.esc(p.name || p.id)}</option>`,
        )
        .join('');
      sel.value = selected;
      sel.disabled = false;
    } catch {
      sel.innerHTML = '<option value="">Portfolios unavailable</option>';
      sel.disabled = true;
    }
  }

  disconnectedCallback(): void {
    window.removeEventListener('hashchange', this._onHashChange);
  }

  private initTheme(): void {
    const saved = localStorage.getItem('pae-theme');
    const theme: Theme = saved === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
  }

  private toggleTheme(): void {
    const current = document.documentElement.getAttribute('data-theme');
    const next: Theme = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try {
      localStorage.setItem('pae-theme', next);
    } catch {
      // localStorage may be unavailable in private browsing
    }
  }

  private setupRouting(): void {
    this._onHashChange = this._onHashChange.bind(this);
    window.addEventListener('hashchange', this._onHashChange);
    this.handleRoute(window.location.hash);
  }

  private _onHashChange(): void {
    this.handleRoute(window.location.hash);
  }

  private handleRoute(hash: string): void {
    const route = hash.replace('#', '') || 'dashboard';

    // Update nav active state
    const navItems = this.shadow.querySelectorAll('.nav-item');
    navItems.forEach((item) => {
      const href = item.getAttribute('href') || '';
      const itemRoute = href.replace('#', '');
      if (itemRoute === route) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });

    // Swap main content based on route
    const main = this.shadow.querySelector('.pae-main');
    if (!main) return;

    const routeComponents: Record<string, string> = {
      'dashboard': '<pae-dashboard></pae-dashboard>',
      'import': '<pae-import></pae-import>',
      'risk': '<pae-risk></pae-risk>',
      'factors': '<pae-factors></pae-factors>',
      'montecarlo': '<pae-scenarios view="montecarlo"></pae-scenarios>',
      'stress': '<pae-scenarios view="stress"></pae-scenarios>',
      'optimize': '<pae-optimize></pae-optimize>',
      'optimizer': '<pae-optimize></pae-optimize>',
      'carry': '<pae-carry></pae-carry>',
      'journal': '<pae-journal></pae-journal>',
      'knowledge': '<pae-knowledge></pae-knowledge>',
      'holdings': '<pae-holdings></pae-holdings>',
    };

    const comingSoon = `
      <div style="text-align:center;padding:var(--space-16)">
        <div style="font-size:var(--font-size-xl);font-weight:700;color:var(--text-primary);margin-bottom:var(--space-4)">
          ${route.charAt(0).toUpperCase() + route.slice(1).replace(/-/g, ' ')}
        </div>
        <div style="color:var(--text-tertiary)">Coming soon. Building the ${route} module.</div>
      </div>
    `;

    main.innerHTML = routeComponents[route] || comingSoon;
  }

  private render(): void {
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">

      <div class="pae-layout">
        <header class="pae-header">
          <div style="display:flex;align-items:center;gap:var(--space-3)">
            <h1 style="font-size:var(--font-size-lg);font-weight:700;margin:0;color:var(--text-primary)">
              PAE
            </h1>
            <span style="font-size:var(--font-size-sm);color:var(--text-tertiary)">
              Personal Analytics Engine
            </span>
          </div>
          <div style="display:flex;align-items:center;gap:var(--space-3)">
            <span style="font-size:var(--font-size-xs);color:var(--text-tertiary)">v0.1.0</span>
            <button class="pae-theme-toggle" id="theme-btn" aria-label="Toggle theme">Theme</button>
          </div>
        </header>

        <nav class="pae-sidebar" aria-label="Main navigation">
          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:var(--space-3)">
            Portfolio
          </div>
          <select id="portfolio-select" aria-label="Active portfolio"
            style="width:100%;padding:6px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);margin-bottom:var(--space-6)">
            <option value="">Loading…</option>
          </select>
          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:var(--space-3)">
            Analytics
          </div>
          <div class="nav-items" role="list">
            <a class="nav-item active" href="#dashboard" role="listitem">Dashboard</a>
            <a class="nav-item" href="#risk" role="listitem">Risk Metrics</a>
            <a class="nav-item" href="#factors" role="listitem">Factor Decomposition</a>
            <a class="nav-item" href="#montecarlo" role="listitem">Monte Carlo</a>
            <a class="nav-item" href="#stress" role="listitem">Stress Testing</a>
            <a class="nav-item" href="#optimizer" role="listitem">Optimizer</a>
            <a class="nav-item" href="#carry" role="listitem">Carry Analysis</a>
          </div>

          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin:var(--space-6) 0 var(--space-3)">
            Intelligence
          </div>
          <div class="nav-items" role="list">
            <a class="nav-item" href="#journal" role="listitem">Decision Journal</a>
            <a class="nav-item" href="#knowledge" role="listitem">Knowledge Base</a>
          </div>

          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin:var(--space-6) 0 var(--space-3)">
            Data
          </div>
          <div class="nav-items" role="list">
            <a class="nav-item" href="#import" role="listitem">Import</a>
            <a class="nav-item" href="#holdings" role="listitem">Holdings</a>
          </div>
        </nav>

        <main class="pae-main">
          <pae-dashboard></pae-dashboard>
        </main>

        <nav class="pae-mobile-nav" aria-label="Mobile navigation">
          <a class="nav-item" href="#dashboard">Dashboard</a>
          <a class="nav-item" href="#risk">Risk</a>
          <a class="nav-item" href="#factors">Factors</a>
          <a class="nav-item" href="#montecarlo">Monte Carlo</a>
          <a class="nav-item" href="#stress">Stress</a>
          <a class="nav-item" href="#import">Import</a>
        </nav>
      </div>

      <style>
        .nav-items {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .nav-item {
          display: block;
          padding: var(--space-2) var(--space-3);
          border-radius: var(--radius-sm);
          color: var(--text-secondary);
          text-decoration: none;
          font-size: var(--font-size-sm);
          transition: all var(--transition-fast);
          cursor: pointer;
        }
        .nav-item:hover {
          background: var(--bg-hover);
          color: var(--text-primary);
        }
        .nav-item.active {
          background: var(--accent-primary);
          color: var(--text-inverse);
        }
        .nav-item:focus-visible {
          outline: 2px solid var(--accent-primary);
          outline-offset: 2px;
        }
        .pae-mobile-nav {
          display: none;
        }
        @media (max-width: 1024px) {
          .pae-mobile-nav {
            display: flex;
            gap: 4px;
            overflow-x: auto;
            padding: var(--space-2);
            background: var(--bg-secondary);
            border-top: 1px solid var(--border-color);
            position: sticky;
            bottom: 0;
            z-index: 10;
          }
          .pae-mobile-nav .nav-item {
            white-space: nowrap;
            flex-shrink: 0;
          }
        }
      </style>
    `;

    const themeBtn = this.shadow.getElementById('theme-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', () => this.toggleTheme());
    }

    const portfolioSel = this.shadow.getElementById('portfolio-select');
    if (portfolioSel) {
      portfolioSel.addEventListener('change', () => {
        const value = (portfolioSel as HTMLSelectElement).value;
        if (value) {
          storeSelectedPortfolioId(value);
          // Remount the current view so it reloads on the new selection.
          this.handleRoute(window.location.hash);
        }
      });
    }
  }
}

customElements.define('pae-app', PaeApp);
