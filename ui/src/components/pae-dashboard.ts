/**
 * PAE Dashboard Component.
 * Shows portfolio overview: metrics, allocation, performance.
 * Vanilla Web Component.
 */

class PaeDashboard extends HTMLElement {
  private shadow: ShadowRoot;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
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
            <canvas id="allocation-chart" width="400" height="300"></canvas>
          </div>
          <div class="pae-card">
            <div class="pae-card-header">
              <span class="pae-card-title">Performance</span>
            </div>
            <canvas id="performance-chart" width="400" height="300"></canvas>
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
