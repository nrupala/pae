/**
 * PAE Dashboard Component.
 * Shows portfolio overview: metrics, allocation, performance.
 * Vanilla Web Component.
 *
 * @element pae-dashboard
 *
 * Fetches data from the Rust engine API and populates metric cards,
 * holdings table, and carry analysis section.
 */

/** Shape of a single holding returned by the API or loaded from CSV. */
interface DashboardHolding {
  symbol: string;
  market_value: number;
  weight: number;
  yield_pct?: number;
  returns: number[];
}

/** Shape of the /api/v1/portfolio/metrics response. */
interface MetricsResponse {
  total_return: number;
  sharpe: number;
  max_drawdown: number;
}

/** Shape of the /api/v1/portfolio/carry response (future). */
interface CarryResponse {
  total_annual_income: number;
  total_annual_margin_cost: number;
  net_carry: number;
}

const API_BASE = '/api/v1';
const FETCH_TIMEOUT_MS = 15_000;

class PaeDashboard extends HTMLElement {
  private shadow: ShadowRoot;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
  }

  /**
   * Update a metric card's displayed value.
   * @param id - The element ID of the metric value span.
   * @param value - The formatted string to display.
   */
  private setMetric(id: string, value: string): void {
    const el = this.shadow.getElementById(id);
    if (el) {
      el.textContent = value;
    }
  }

  /**
   * Format a number as currency (USD).
   * Returns '--' for non-finite values.
   */
  private formatCurrency(value: number): string {
    if (!Number.isFinite(value)) return '--';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  }

  /**
   * Format a number as a percentage.
   * Returns '--' for non-finite values.
   */
  private formatPercent(value: number): string {
    if (!Number.isFinite(value)) return '--';
    return (value * 100).toFixed(2) + '%';
  }

  /**
   * Format a number with fixed decimal places.
   * Returns '--' for non-finite values.
   */
  private formatNumber(value: number, decimals: number = 2): string {
    if (!Number.isFinite(value)) return '--';
    return value.toFixed(decimals);
  }

  /**
   * Fetch data from the engine API with timeout and error handling.
   * @param endpoint - API path relative to API_BASE (e.g., '/portfolio/metrics').
   * @param body - Request payload.
   * @returns Parsed JSON response, or null on error.
   */
  private async fetchApi<T>(endpoint: string, body: unknown): Promise<T | null> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorBody = await response.text();
        console.error(`PAE API error ${response.status} on ${endpoint}:`, errorBody);
        return null;
      }

      return await response.json() as T;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.error(`PAE API timeout on ${endpoint}`);
      } else {
        console.error(`PAE API fetch error on ${endpoint}:`, error);
      }
      return null;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  private render(): void {
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">

      <div>
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">
          Portfolio Dashboard
        </h2>

        <div class="pae-grid pae-grid-4" style="margin-bottom:var(--space-6)">
          <div class="pae-card">
            <div class="pae-metric">
              <div class="pae-metric-label">Net Asset Value</div>
              <div class="pae-metric-value" id="nav-value">--</div>
            </div>
          </div>
          <div class="pae-card">
            <div class="pae-metric">
              <div class="pae-metric-label">Total Return</div>
              <div class="pae-metric-value" id="return-value">--</div>
            </div>
          </div>
          <div class="pae-card">
            <div class="pae-metric">
              <div class="pae-metric-label">Sharpe Ratio</div>
              <div class="pae-metric-value" id="sharpe-value">--</div>
            </div>
          </div>
          <div class="pae-card">
            <div class="pae-metric">
              <div class="pae-metric-label">Max Drawdown</div>
              <div class="pae-metric-value" id="dd-value">--</div>
            </div>
          </div>
        </div>

        <div class="pae-grid pae-grid-2" style="margin-bottom:var(--space-6)">
          <div class="pae-card">
            <div class="pae-card-header">
              <span class="pae-card-title">Allocation</span>
            </div>
            <canvas id="allocation-chart" width="400" height="300" role="img" aria-label="Portfolio allocation chart"></canvas>
          </div>
          <div class="pae-card">
            <div class="pae-card-header">
              <span class="pae-card-title">Performance</span>
            </div>
            <canvas id="performance-chart" width="400" height="300" role="img" aria-label="Portfolio performance chart"></canvas>
          </div>
        </div>

        <div class="pae-card">
          <div class="pae-card-header">
            <span class="pae-card-title">Holdings</span>
          </div>
          <table class="pae-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Value</th>
                <th>Weight</th>
                <th>Yield</th>
                <th>Return</th>
              </tr>
            </thead>
            <tbody id="holdings-body">
              <tr>
                <td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:var(--space-8)">
                  Import holdings via CSV or connect a brokerage to get started.
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="pae-card" style="margin-top:var(--space-4)">
          <div class="pae-card-header">
            <span class="pae-card-title">Margin Carry Analysis</span>
          </div>
          <div class="pae-grid pae-grid-3">
            <div class="pae-metric">
              <div class="pae-metric-label">Annual Income</div>
              <div class="pae-metric-value" id="income-value">--</div>
            </div>
            <div class="pae-metric">
              <div class="pae-metric-label">Margin Cost</div>
              <div class="pae-metric-value" id="margin-cost-value">--</div>
            </div>
            <div class="pae-metric">
              <div class="pae-metric-label">Net Carry</div>
              <div class="pae-metric-value" id="net-carry-value">--</div>
            </div>
          </div>
        </div>
      </div>
    `;
  }
}

customElements.define('pae-dashboard', PaeDashboard);
