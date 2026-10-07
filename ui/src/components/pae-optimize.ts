// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Optimize View — serves the #optimize route (and #optimizer).
 *
 * Posts to POST /api/v1/analytics/optimize with the first portfolio's id
 * and renders the long-only efficient frontier (scatter chart) with
 * markers for the maximum-Sharpe, minimum-variance, and risk-parity mixes,
 * plus a weights table.
 *
 * Analytics only — no buy/sell language anywhere on this view. Every
 * number shown is a calculation from the inputs; the user decides what,
 * if anything, to do with it.
 */

const API_BASE = 'http://localhost:3002';

const DISCLOSURE =
  'Educational analytics only. PAE calculates; the user decides. ' +
  'No investment advice.';

interface MixResult {
  label: string;
  weights: Record<string, number>;
  expected_return: number;
  volatility: number;
  sharpe_ratio: number;
  method: string;
}

interface FrontierPoint {
  expected_return: number;
  volatility: number;
  weights: Record<string, number>;
}

interface OptimizeResponse {
  symbols: string[];
  risk_free_rate: number;
  max_sharpe: MixResult;
  min_variance: MixResult;
  risk_parity: MixResult;
  frontier: FrontierPoint[];
  covariance_regularized: boolean;
  regularization_amount: number;
  notes: string[];
  disclosure: string;
}

const MIXES: Array<{ key: 'max_sharpe' | 'min_variance' | 'risk_parity'; color: string; blurb: string }> = [
  {
    key: 'max_sharpe',
    color: '#38bdf8',
    blurb: 'Highest expected return per unit of volatility under long-only constraints.',
  },
  {
    key: 'min_variance',
    color: '#34d399',
    blurb: 'Lowest expected volatility under long-only constraints.',
  },
  {
    key: 'risk_parity',
    color: '#a78bfa',
    blurb: 'Each holding contributes approximately equal risk to the mix.',
  },
];

class PaeOptimize extends HTMLElement {
  private shadow: ShadowRoot;
  private result: OptimizeResponse | null = null;
  private error: string = '';
  private loading: boolean = true;
  private riskFreeRate: number = 0.02;

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
    const portfolios = (await resp.json()).portfolios || [];
    if (!portfolios.length) throw new Error('empty');
    return portfolios[0].id;
  }

  private async loadData(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.result = null;
    this.render();
    try {
      const portfolioId = await this.firstPortfolioId();
      const resp = await fetch(`${API_BASE}/api/v1/analytics/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          portfolio_id: portfolioId,
          risk_free_rate: this.riskFreeRate,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`optimize: HTTP ${resp.status} ${detail}`);
      }
      this.result = (await resp.json()) as OptimizeResponse;
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      if (this.error === 'empty') {
        this.error = '';
      }
    }
    this.render();
    this.drawCharts();
  }

  private onRiskFreeChange(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const v = parseFloat(input.value);
    if (Number.isFinite(v) && v >= -1 && v <= 1) {
      this.riskFreeRate = v;
      void this.loadData();
    }
  }

  private pct(v: number): string {
    return `${(v * 100).toFixed(2)}%`;
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Computing optimal mixes…</div>`;
    } else if (this.error) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--color-negative)">Failed: ${this.error}</div>`;
    } else if (!this.result) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings with return history to run the optimizer. <a href="#import">Go to Import</a></div>`;
    } else {
      content = this.renderResult(this.result);
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
    const rf = this.shadow.querySelector('#rf-input');
    rf?.addEventListener('change', (e) => this.onRiskFreeChange(e));
  }

  private renderResult(r: OptimizeResponse): string {
    const cards = MIXES.map(({ key, color, blurb }) => {
      const m = r[key];
      return `
        <div class="pae-card">
          <div class="pae-card-header">
            <span class="pae-card-title" style="color:${color}">${m.label}</span>
          </div>
          <div class="pae-metric"><div class="pae-metric-label">Expected return</div><div class="pae-metric-value">${this.pct(m.expected_return)}</div></div>
          <div class="pae-metric"><div class="pae-metric-label">Volatility</div><div class="pae-metric-value">${this.pct(m.volatility)}</div></div>
          <div class="pae-metric"><div class="pae-metric-label">Sharpe ratio</div><div class="pae-metric-value">${m.sharpe_ratio.toFixed(3)}</div></div>
          <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-2)">${blurb}</div>
          <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">Method: ${m.method}</div>
        </div>`;
    }).join('');

    const weightRows = r.symbols.map(sym => {
      const cells = MIXES.map(({ key }) =>
        `<td class="numeric">${this.pct(r[key].weights[sym] ?? 0)}</td>`).join('');
      return `<tr><td><strong>${sym}</strong></td>${cells}</tr>`;
    }).join('');

    const notes = r.notes.length
      ? `<div class="pae-card" style="margin-bottom:var(--space-6)">
           <div class="pae-card-header"><span class="pae-card-title">Computation notes</span></div>
           <ul style="margin:0;padding-left:var(--space-6);font-size:var(--font-size-sm);color:var(--text-secondary)">
             ${r.notes.map(n => `<li>${n}</li>`).join('')}
           </ul>
         </div>`
      : '';

    const regularized = r.covariance_regularized
      ? `<div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-2)">Covariance was near-singular and regularized (amount ${r.regularization_amount.toExponential(2)}). Weights reflect the regularized matrix.</div>`
      : '';

    return `
      <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Portfolio Optimization</h2>
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <label for="rf-input" style="font-size:var(--font-size-sm);color:var(--text-secondary)">Risk-free rate (per period, decimal)&nbsp;</label>
        <input id="rf-input" type="number" step="0.005" min="-1" max="1" value="${this.riskFreeRate}"
          style="padding:6px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);width:7em" />
        <span style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-left:var(--space-3)">Used for Sharpe ratios only. Expected returns and covariance come from the holdings' stored return series.</span>
      </div>
      <div class="pae-grid pae-grid-3" style="margin-bottom:var(--space-6)">${cards}</div>
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div class="pae-card-header"><span class="pae-card-title">Efficient frontier — expected return vs volatility</span></div>
        <pae-chart id="frontier-chart" title="Efficient frontier with optimal mix markers"></pae-chart>
        ${regularized}
      </div>
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div class="pae-card-header"><span class="pae-card-title">Mix weights by holding</span></div>
        <table class="pae-table">
          <thead><tr><th>Holding</th><th>Max Sharpe</th><th>Min Variance</th><th>Risk Parity</th></tr></thead>
          <tbody>${weightRows}</tbody>
        </table>
      </div>
      ${notes}
      <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${DISCLOSURE} Mixes are computed from the inputs supplied — they describe calculated trade-offs, not a recommendation to trade.</div>`;
  }

  private drawCharts(): void {
    if (!this.result) return;
    const r = this.result;
    const chart = this.shadow.querySelector('pae-chart#frontier-chart') as unknown as {
      setScatter(d: unknown): void;
    } | null;
    if (!chart) return;
    const markers = MIXES.map(({ key, color }) => {
      const m = r[key];
      return {
        x: m.volatility * 100,
        y: m.expected_return * 100,
        label: `${m.label}: ${this.pct(m.expected_return)} return, ${this.pct(m.volatility)} vol`,
        color,
      };
    });
    chart.setScatter({
      points: r.frontier.map(p => ({
        x: p.volatility * 100,
        y: p.expected_return * 100,
      })),
      markers,
      xLabel: 'Volatility %',
      yLabel: 'Expected return %',
      lineColor: '#64748b',
    });
  }
}

customElements.define('pae-optimize', PaeOptimize);

export {};
