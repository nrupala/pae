/**
 * PAE Dashboard Component.
 * Fetches real data from the Python API server and renders portfolio overview.
 * Vanilla Web Component -- no framework, no dependencies.
 *
 * API endpoints used:
 * - GET /api/v1/dashboard/{portfolio_id} -> summary, allocation, top holdings
 * - GET /api/v1/portfolios -> list of portfolios
 *
 * The Python server (port 3002) handles data persistence and proxies
 * risk calculations to the Rust engine (port 3001).
 */

const API_BASE = 'http://localhost:3002';

interface DashboardData {
  summary: {
    portfolio_id: string;
    holding_count: number;
    total_market_value: number;
    total_cost_basis: number;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
  };
  allocation: Record<string, number>;
  top_holdings: Array<{
    symbol: string;
    name: string;
    market_value: number;
    weight_pct: number;
    yield_pct: number;
    unrealized_pnl: number;
  }>;
  holding_count: number;
}

interface PortfolioListItem {
  id: string;
  name: string;
  holding_count: number;
  total_market_value: number;
}

class PaeDashboard extends HTMLElement {
  private shadow: ShadowRoot;
  private portfolioId: string = '';
  private data: DashboardData | null = null;
  private error: string = '';
  private loading: boolean = true;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    this.loadData();
  }

  private async loadData(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();

    try {
      // First, get the list of portfolios
      const portfoliosResp = await fetch(`${API_BASE}/api/v1/portfolios`);
      if (!portfoliosResp.ok) {
        throw new Error(`Failed to fetch portfolios: ${portfoliosResp.status}`);
      }
      const portfoliosData = await portfoliosResp.json();
      const portfolios: PortfolioListItem[] = portfoliosData.portfolios || [];

      if (portfolios.length === 0) {
        this.loading = false;
        this.error = '';
        this.data = null;
        this.render();
        return;
      }

      // Use the first portfolio (or a selected one)
      this.portfolioId = portfolios[0].id;

      // Fetch dashboard data
      const dashResp = await fetch(`${API_BASE}/api/v1/dashboard/${this.portfolioId}`);
      if (!dashResp.ok) {
        throw new Error(`Failed to fetch dashboard: ${dashResp.status}`);
      }
      this.data = await dashResp.json();
      this.loading = false;
      this.error = '';
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      this.data = null;
    }

    this.render();
  }

  private fmt(value: number, decimals: number = 2): string {
    return value.toLocaleString('en-CA', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  private fmtCurrency(value: number): string {
    return '$' + this.fmt(value);
  }

  private fmtPct(value: number): string {
    return this.fmt(value) + '%';
  }

  private pnlClass(value: number): string {
    if (value > 0) return 'pae-metric-positive';
    if (value < 0) return 'pae-metric-negative';
    return '';
  }

  private renderLoading(): string {
    return `
      <div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">
        Loading portfolio data...
      </div>
    `;
  }

  private renderError(): string {
    return `
      <div style="text-align:center;padding:var(--space-16)">
        <div style="color:var(--color-negative);margin-bottom:var(--space-4)">
          Failed to load dashboard
        </div>
        <div style="color:var(--text-tertiary);font-size:var(--font-size-sm)">
          ${this.error}
        </div>
        <div style="margin-top:var(--space-4);font-size:var(--font-size-sm);color:var(--text-tertiary)">
          Make sure the Python server is running: <code>uvicorn pae.server:app --port 3002</code>
        </div>
        <button onclick="this.getRootNode().host.connectedCallback()"
                style="margin-top:var(--space-4);padding:var(--space-2) var(--space-4);
                       background:var(--accent-primary);color:white;border:none;
                       border-radius:var(--radius-sm);cursor:pointer">
          Retry
        </button>
      </div>
    `;
  }

  private renderEmpty(): string {
    return `
      <div style="text-align:center;padding:var(--space-16)">
        <div style="font-size:var(--font-size-xl);font-weight:700;color:var(--text-primary);margin-bottom:var(--space-4)">
          Welcome to PAE
        </div>
        <div style="color:var(--text-secondary);margin-bottom:var(--space-6)">
          Import your holdings to get started.
        </div>
        <div style="color:var(--text-tertiary);font-size:var(--font-size-sm)">
          Upload a CSV from your broker (IBKR, Questrade, Wealthsimple) or add holdings manually.
        </div>
      </div>
    `;
  }

  private renderDashboard(): string {
    if (!this.data) return this.renderEmpty();

    const s = this.data.summary;
    const alloc = this.data.allocation;
    const holdings = this.data.top_holdings;

    const allocRows = Object.entries(alloc)
      .sort((a, b) => b[1] - a[1])
      .map(([cls, pct]) => `
        <tr>
          <td>${cls.replace('_', ' ')}</td>
          <td class="numeric">${this.fmtPct(pct)}</td>
          <td>
            <div style="background:var(--bg-tertiary);border-radius:2px;height:8px;width:100%">
              <div style="background:var(--accent-primary);border-radius:2px;height:8px;width:${Math.min(pct, 100)}%"></div>
            </div>
          </td>
        </tr>
      `).join('');

    const holdingRows = holdings.map(h => `
      <tr>
        <td><strong>${h.symbol}</strong></td>
        <td style="color:var(--text-secondary);font-size:var(--font-size-xs)">${h.name}</td>
        <td class="numeric">${this.fmtCurrency(h.market_value)}</td>
        <td class="numeric">${this.fmtPct(h.weight_pct)}</td>
        <td class="numeric">${this.fmtPct(h.yield_pct)}</td>
        <td class="numeric ${this.pnlClass(h.unrealized_pnl)}">${this.fmtCurrency(h.unrealized_pnl)}</td>
      </tr>
    `).join('');

    return `
      <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">
        Portfolio Dashboard
      </h2>

      <!-- Key Metrics -->
      <div class="pae-grid pae-grid-4" style="margin-bottom:var(--space-6)">
        <div class="pae-card">
          <div class="pae-metric">
            <div class="pae-metric-label">Net Asset Value</div>
            <div class="pae-metric-value">${this.fmtCurrency(s.total_market_value)}</div>
          </div>
        </div>
        <div class="pae-card">
          <div class="pae-metric">
            <div class="pae-metric-label">Cost Basis</div>
            <div class="pae-metric-value">${this.fmtCurrency(s.total_cost_basis)}</div>
          </div>
        </div>
        <div class="pae-card">
          <div class="pae-metric">
            <div class="pae-metric-label">Unrealized P&L</div>
            <div class="pae-metric-value ${this.pnlClass(s.unrealized_pnl)}">
              ${this.fmtCurrency(s.unrealized_pnl)}
            </div>
          </div>
        </div>
        <div class="pae-card">
          <div class="pae-metric">
            <div class="pae-metric-label">P&L %</div>
            <div class="pae-metric-value ${this.pnlClass(s.unrealized_pnl_pct)}">
              ${this.fmtPct(s.unrealized_pnl_pct)}
            </div>
          </div>
        </div>
      </div>

      <!-- Allocation + Holdings -->
      <div class="pae-grid pae-grid-2" style="margin-bottom:var(--space-6)">
        <div class="pae-card">
          <div class="pae-card-header">
            <span class="pae-card-title">Asset Allocation</span>
            <span style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${s.holding_count} positions</span>
          </div>
          <table class="pae-table">
            <thead><tr><th>Class</th><th>Weight</th><th></th></tr></thead>
            <tbody>${allocRows || '<tr><td colspan="3" style="text-align:center;color:var(--text-tertiary)">No data</td></tr>'}</tbody>
          </table>
        </div>

        <div class="pae-card">
          <div class="pae-card-header">
            <span class="pae-card-title">Top Holdings</span>
          </div>
          <table class="pae-table">
            <thead><tr><th>Symbol</th><th>Name</th><th>Value</th><th>Weight</th><th>Yield</th><th>P&L</th></tr></thead>
            <tbody>${holdingRows || '<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary)">No holdings</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  private render(): void {
    let content: string;

    if (this.loading) {
      content = this.renderLoading();
    } else if (this.error) {
      content = this.renderError();
    } else if (!this.data || this.data.summary.holding_count === 0) {
      content = this.renderEmpty();
    } else {
      content = this.renderDashboard();
    }

    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>
    `;
  }
}

customElements.define('pae-dashboard', PaeDashboard);
