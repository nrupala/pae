/**
 * PAE Root Application Component.
 * Vanilla Web Component - no framework, no dependencies.
 * Manages layout, theme, and hash-based routing.
 *
 * Routes:
 *   #dashboard  -> <pae-dashboard>
 *   #import     -> <pae-import>
 *   (others)    -> placeholder with coming soon message
 */

const VALID_THEMES = ['light', 'dark'] as const;
type Theme = typeof VALID_THEMES[number];

class PaeApp extends HTMLElement {
  private shadow: ShadowRoot;
  private _activeRoute: string = 'dashboard';

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.initTheme();
    this.render();
    this.setupRouting();
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
    this._activeRoute = route;

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
            Analytics
          </div>
          <div class="nav-items" role="list">
            <a class="nav-item active" href="#dashboard" role="listitem">Dashboard</a>
            <a class="nav-item" href="#risk" role="listitem">Risk Metrics</a>
            <a class="nav-item" href="#factors" role="listitem">Factor Decomposition</a>
            <a class="nav-item" href="#correlation" role="listitem">Correlation</a>
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
            <a class="nav-item" href="#biases" role="listitem">Bias Detection</a>
            <a class="nav-item" href="#calibration" role="listitem">Calibration</a>
            <a class="nav-item" href="#knowledge" role="listitem">Knowledge Base</a>
          </div>

          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em;margin:var(--space-6) 0 var(--space-3)">
            Data
          </div>
          <div class="nav-items" role="list">
            <a class="nav-item" href="#import" role="listitem">Import</a>
            <a class="nav-item" href="#holdings" role="listitem">Holdings</a>
            <a class="nav-item" href="#tax" role="listitem">Tax Analyzer</a>
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
        .nav-item:focus-visible {
          outline: 2px solid var(--accent-primary);
          outline-offset: 2px;
        }
      </style>
    `;

    const themeBtn = this.shadow.getElementById('theme-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', () => this.toggleTheme());
    }
  }
}

customElements.define('pae-app', PaeApp);
