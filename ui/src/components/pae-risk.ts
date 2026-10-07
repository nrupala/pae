// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Risk View.
 * Risk metric cards (VaR/CVaR, Sharpe, Sortino, volatility, max drawdown, beta)
 * plus drawdown and cumulative-growth charts, all fed by live endpoints:
 * - POST /api/v1/analytics/risk?portfolio_id=  -> RiskResponse
 * - GET  /api/v1/analytics/series?portfolio_id= -> period/cumulative/drawdown
 */

const API_BASE = 'http://localhost:3002';

interface RiskResponse {
  var_95: number;
  var_99: number;
  cvar_95: number;
  max_drawdown: number;
  beta: number | null;
  sharpe: number;
  sortino: number;
  volatility: number;
}

interface SeriesResponse {
  n_periods: number;
  period_returns: number[];
  cumulative: number[];
  drawdown: number[];
  note: string;
}

const CHART_COLORS = {
  drawdown: '#fb7185',
  cumulative: '#38bdf8',
};

class PaeRisk extends HTMLElement {
  private shadow: ShadowRoot;
  private risk: RiskResponse | null = null;
  private series: SeriesResponse | null = null;
  private error: string = '';
  private loading: boolean = true;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    void this.loadData();
  }

  private async firstPortfolioId(): Promise<string> {
    const resp = await fetch(`${API_BASE}/api/v1/portfolios`);
    if (!resp.ok) throw new Error(`portfolios: HTTP ${resp.status}`);
    const data = await resp.json();
    const portfolios = data.portfolios || [];
    if (!portfolios.length) throw new Error('empty');
    return portfolios[0].id;
  }

  private async loadData(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();
    try {
      const portfolioId = await this.firstPortfolioId();
      const [riskResp, seriesResp] = await Promise.all([
        fetch(`${API_BASE}/api/v1/analytics/risk?portfolio_id=${encodeURIComponent(portfolioId)}`, { method: 'POST' }),
        fetch(`${API_BASE}/api/v1/analytics/series?portfolio_id=${encodeURIComponent(portfolioId)}`),
      ]);
      if (!riskResp.ok) throw new Error(`risk: HTTP ${riskResp.status}`);
      if (!seriesResp.ok) throw new Error(`series: HTTP ${seriesResp.status}`);
      this.risk = (await riskResp.json()) as RiskResponse;
      this.series = (await seriesResp.json()) as SeriesResponse;
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      if (this.error === 'empty') {
        this.error = '';
        this.risk = null;
        this.series = null;
      }
    }
    this.render();
    this.drawCharts();
  }

  private fmt(v: number, d: number = 2): string {
    return Number.isFinite(v) ? v.toFixed(d) : '–';
  }

  private metricCard(label: string, value: string, hint: string): string {
    return `
      <div class="pae-card">
        <div class="pae-metric">
          <div class="pae-metric-label">${label}</div>
          <div class="pae-metric-value">${value}</div>
          <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:4px">${hint}</div>
        </div>
      </div>`;
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Loading risk metrics…</div>`;
    } else if (this.error) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--color-negative)">Failed to load risk data: ${this.error}</div>`;
    } else if (!this.risk) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings to see risk metrics. <a href="#import">Go to Import</a></div>`;
    } else {
      const r = this.risk;
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Risk Metrics</h2>
        <div class="pae-grid pae-grid-4" style="margin-bottom:var(--space-6)">
          ${this.metricCard('VaR 95%', (r.var_95 * 100).toFixed(2) + '%', 'Max expected loss, 95% confidence')}
          ${this.metricCard('VaR 99%', (r.var_99 * 100).toFixed(2) + '%', 'Max expected loss, 99% confidence')}
          ${this.metricCard('CVaR 95%', (r.cvar_95 * 100).toFixed(2) + '%', 'Avg loss beyond VaR 95%')}
          ${this.metricCard('Max Drawdown', (r.max_drawdown * 100).toFixed(2) + '%', 'Worst peak-to-trough fall')}
          ${this.metricCard('Sharpe', this.fmt(r.sharpe), 'Return per unit of volatility')}
          ${this.metricCard('Sortino', this.fmt(r.sortino), 'Return per unit of downside')}
          ${this.metricCard('Volatility', (r.volatility * 100).toFixed(2) + '%', 'Annualized std dev')}
          ${this.metricCard('Beta', r.beta === null ? '–' : this.fmt(r.beta), 'Sensitivity to market')}
        </div>
        <div class="pae-grid pae-grid-2" style="margin-bottom:var(--space-6)">
          <div class="pae-card">
            <div class="pae-card-header"><span class="pae-card-title">Drawdown</span></div>
            <pae-chart id="dd-chart" type="area" title="Portfolio drawdown over time"></pae-chart>
          </div>
          <div class="pae-card">
            <div class="pae-card-header"><span class="pae-card-title">Growth of $1</span></div>
            <pae-chart id="cum-chart" type="line" title="Cumulative growth of one unit invested"></pae-chart>
          </div>
        </div>
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">
          Educational analytics only — the tool calculates, you decide. Not investment advice.
        </div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
  }

  private drawCharts(): void {
    if (!this.series) return;
    const labels = this.series.period_returns.map((_, i) => `P${i + 1}`);
    const dd = this.shadow.querySelector('pae-chart#dd-chart') as unknown as {
      setSeries(d: unknown): void;
    } | null;
    dd?.setSeries({
      labels,
      yLabel: 'Drawdown %',
      datasets: [{
        label: 'Drawdown',
        data: this.series.drawdown.map(v => v * 100),
        color: CHART_COLORS.drawdown,
      }],
    });
    const cum = this.shadow.querySelector('pae-chart#cum-chart') as unknown as {
      setSeries(d: unknown): void;
    } | null;
    cum?.setSeries({
      labels,
      yLabel: 'Value',
      datasets: [{
        label: 'Portfolio',
        data: this.series.cumulative,
        color: CHART_COLORS.cumulative,
      }],
    });
  }
}

customElements.define('pae-risk', PaeRisk);

export {};
