/**
 * PAE Factor View.
 * Fama-French 5-factor exposure bars + performance vs market-factor benchmark,
 * fed by POST /api/v1/analytics/factor?portfolio_id=.
 */

const API_BASE = 'http://localhost:3002';

interface FactorExposure {
  factor_name: string;
  beta: number;
  t_stat: number;
  contribution_pct: number;
}

interface FactorResponse {
  n_periods: number;
  alpha: number;
  alpha_t_stat: number;
  r_squared: number;
  residual_risk_pct: number;
  exposures: FactorExposure[];
  portfolio_cumulative: number[];
  benchmark_cumulative: number[];
  benchmark_name: string;
  factor_source: string;
  note: string;
}

const COLORS = {
  exposure: '#a78bfa',
  portfolio: '#38bdf8',
  benchmark: '#fbbf24',
};

class PaeFactors extends HTMLElement {
  private shadow: ShadowRoot;
  private data: FactorResponse | null = null;
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

  private async loadData(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();
    try {
      const pResp = await fetch(`${API_BASE}/api/v1/portfolios`);
      if (!pResp.ok) throw new Error(`portfolios: HTTP ${pResp.status}`);
      const portfolios = (await pResp.json()).portfolios || [];
      if (!portfolios.length) throw new Error('empty');
      const fResp = await fetch(
        `${API_BASE}/api/v1/analytics/factor?portfolio_id=${encodeURIComponent(portfolios[0].id)}`,
        { method: 'POST' },
      );
      if (!fResp.ok) {
        const detail = await fResp.text();
        throw new Error(`factor: HTTP ${fResp.status} — ${detail.slice(0, 160)}`);
      }
      this.data = (await fResp.json()) as FactorResponse;
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      if (this.error === 'empty') {
        this.error = '';
        this.data = null;
      }
    }
    this.render();
    this.drawCharts();
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Running factor decomposition…</div>`;
    } else if (this.error) {
      content = `<div style="text-align:center;padding:var(--space-16)"><div style="color:var(--color-negative);margin-bottom:var(--space-2)">Failed to load factor data</div><div style="font-size:var(--font-size-sm);color:var(--text-tertiary)">${this.error}</div></div>`;
    } else if (!this.data) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings to see factor exposures. <a href="#import">Go to Import</a></div>`;
    } else {
      const d = this.data;
      const rows = d.exposures.map(e => `
        <tr>
          <td><strong>${e.factor_name}</strong></td>
          <td class="numeric">${e.beta.toFixed(3)}</td>
          <td class="numeric">${e.t_stat.toFixed(2)}</td>
          <td class="numeric">${e.contribution_pct.toFixed(1)}%</td>
        </tr>`).join('');
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Factor Decomposition</h2>
        <div class="pae-grid pae-grid-4" style="margin-bottom:var(--space-6)">
          <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">R²</div><div class="pae-metric-value">${d.r_squared.toFixed(3)}</div></div></div>
          <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">Alpha</div><div class="pae-metric-value">${(d.alpha * 100).toFixed(2)}%</div></div></div>
          <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">Residual risk</div><div class="pae-metric-value">${d.residual_risk_pct.toFixed(1)}%</div></div></div>
          <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">Periods</div><div class="pae-metric-value">${d.n_periods}</div></div></div>
        </div>
        <div class="pae-grid pae-grid-2" style="margin-bottom:var(--space-6)">
          <div class="pae-card">
            <div class="pae-card-header"><span class="pae-card-title">Factor Exposures (betas)</span></div>
            <pae-chart id="exp-chart" type="bar" title="Fama-French five-factor exposures"></pae-chart>
            <table class="pae-table" style="margin-top:var(--space-4)">
              <thead><tr><th>Factor</th><th>Beta</th><th>t-stat</th><th>Contrib.</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div class="pae-card">
            <div class="pae-card-header"><span class="pae-card-title">Performance vs Benchmark</span></div>
            <pae-chart id="perf-chart" type="line" title="Growth of one unit: portfolio vs market factor"></pae-chart>
            <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-2)">Benchmark: ${d.benchmark_name}. Source: ${d.factor_source}.</div>
          </div>
        </div>
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${d.note} Educational analytics only — not investment advice.</div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
  }

  private drawCharts(): void {
    if (!this.data) return;
    const exp = this.shadow.querySelector('pae-chart#exp-chart') as unknown as {
      setSeries(d: unknown): void;
    } | null;
    exp?.setSeries({
      labels: this.data.exposures.map(e => e.factor_name),
      datasets: [{
        label: 'Beta',
        data: this.data.exposures.map(e => e.beta),
        color: COLORS.exposure,
      }],
    });
    const n = this.data.portfolio_cumulative.length;
    const labels = Array.from({ length: n }, (_, i) => `P${i + 1}`);
    const perf = this.shadow.querySelector('pae-chart#perf-chart') as unknown as {
      setSeries(d: unknown): void;
    } | null;
    perf?.setSeries({
      labels,
      yLabel: 'Growth of $1',
      datasets: [
        { label: 'Portfolio', data: this.data.portfolio_cumulative, color: COLORS.portfolio },
        { label: 'Benchmark', data: this.data.benchmark_cumulative, color: COLORS.benchmark },
      ],
    });
  }
}

customElements.define('pae-factors', PaeFactors);

export {};
