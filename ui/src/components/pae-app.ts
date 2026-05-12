/**
 * PAE Root Application Component.
 * Vanilla Web Component - no framework, no dependencies.
 * Manages layout, theme, and top-level routing.
 */

class PaeApp extends HTMLElement {
  private shadow: ShadowRoot;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.initTheme();
    this.render();
  }

  private initTheme(): void {
    const saved = localStorage.getItem('pae-theme');
    if (saved) {
      document.documentElement.setAttribute('data-theme', saved);
    }
  }

  private toggleTheme(): void {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('pae-theme', next);
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
            <button class="pae-theme-toggle" id="theme-btn">Theme</button>
          </div>
        </header>

        <nav class="pae-sidebar">
          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:var(--space-3)">
            Analytics
          </div>
          <div class="nav-items">
            <a class="nav-item active" href="#dashboard">Dashboard</a>
            <a class="nav-item" href="#risk">Risk Metrics</a>
            <a class="nav-item" href="#factors">Factor Decomposition</a>
            <a class="nav-item" href="#correlation">Correlation</a>
            <a class="nav-item" href="#montecarlo">Monte Carlo</a>
            <a class="nav-item" href="#stress">Stress Testing</a>
            <a class="nav-item" href="#optimizer">Optimizer</a>
            <a class="nav-item" href="#carry">Carry Analysis</a>
          </div>

          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin:var(--space-6) 0 var(--space-3)">
            Intelligence
          </div>
          <div class="nav-items">
            <a class="nav-item" href="#journal">Decision Journal</a>
            <a class="nav-item" href="#biases">Bias Detection</a>
            <a class="nav-item" href="#calibration">Calibration</a>
            <a class="nav-item" href="#knowledge">Knowledge Base</a>
          </div>

          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin:var(--space-6) 0 var(--space-3)">
            Data
          </div>
          <div class="nav-items">
            <a class="nav-item" href="#import">Import</a>
            <a class="nav-item" href="#holdings">Holdings</a>
            <a class="nav-item" href="#tax">Tax Analyzer</a>
          </div>
        </nav>

        <main class="pae-main">
          <pae-dashboard></pae-dashboard>
        </main>
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
      </style>
    `;

    this.shadow.getElementById('theme-btn')
      ?.addEventListener('click', () => this.toggleTheme());
  }
}

customElements.define('pae-app', PaeApp);
