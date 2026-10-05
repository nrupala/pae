/**
 * PAE Scenarios View — serves both #montecarlo and #stress routes via the
 * `view` attribute.
 * - view="montecarlo": percentile fan chart from POST /api/v1/analytics/montecarlo
 * - view="stress": scenario selector + position-impact bars from POST /api/v1/analytics/stress
 */

const API_BASE = 'http://localhost:3002';

interface MonteCarloResponse {
  percentiles: { p5: number[]; p25: number[]; p50: number[]; p75: number[]; p95: number[] };
  num_simulations: number;
  time_horizon_months: number;
  probability_of_loss: number;
}

interface PositionImpact {
  symbol: string;
  impact_pct: number;
  impact_value: number;
}

interface StressResponse {
  scenario: string;
  portfolio_impact_pct: number;
  position_impacts: PositionImpact[];
}

const SCENARIOS = [
  { id: '2008', label: '2008 Financial Crisis' },
  { id: '2020', label: '2020 COVID Crash' },
  { id: 'rate_shock', label: 'Rate Shock (+200bp)' },
];

class PaeScenarios extends HTMLElement {
  private shadow: ShadowRoot;
  private view: string = 'montecarlo';
  private mc: MonteCarloResponse | null = null;
  private stress: StressResponse | null = null;
  private scenario: string = SCENARIOS[0].id;
  private error: string = '';
  private loading: boolean = true;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  static get observedAttributes(): string[] {
    return ['view'];
  }

  attributeChangedCallback(): void {
    this.view = this.getAttribute('view') === 'stress' ? 'stress' : 'montecarlo';
    this.render();
    void this.loadData();
  }

  connectedCallback(): void {
    this.view = this.getAttribute('view') === 'stress' ? 'stress' : 'montecarlo';
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
    this.mc = null;
    this.stress = null;
    this.render();
    try {
      const portfolioId = await this.firstPortfolioId();
      if (this.view === 'montecarlo') {
        const resp = await fetch(
          `${API_BASE}/api/v1/analytics/montecarlo?portfolio_id=${encodeURIComponent(portfolioId)}&num_simulations=1000&time_horizon_months=12`,
          { method: 'POST' },
        );
        if (!resp.ok) throw new Error(`montecarlo: HTTP ${resp.status}`);
        this.mc = (await resp.json()) as MonteCarloResponse;
      } else {
        await this.runStress(portfolioId);
      }
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

  private async runStress(portfolioId: string): Promise<void> {
    const resp = await fetch(
      `${API_BASE}/api/v1/analytics/stress?portfolio_id=${encodeURIComponent(portfolioId)}&scenario=${encodeURIComponent(this.scenario)}`,
      { method: 'POST' },
    );
    if (!resp.ok) throw new Error(`stress: HTTP ${resp.status}`);
    this.stress = (await resp.json()) as StressResponse;
  }

  private onScenarioChange(ev: Event): void {
    const sel = ev.target as HTMLSelectElement;
    this.scenario = sel.value;
    this.loading = true;
    this.render();
    this.firstPortfolioId()
      .then(id => this.runStress(id))
      .then(() => { this.loading = false; this.render(); this.drawCharts(); })
      .catch((e: unknown) => {
        this.loading = false;
        this.error = e instanceof Error ? e.message : 'Unknown error';
        this.render();
      });
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Running simulation…</div>`;
    } else if (this.error) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--color-negative)">Failed: ${this.error}</div>`;
    } else if (this.view === 'montecarlo') {
      content = this.renderMonteCarlo();
    } else {
      content = this.renderStress();
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
    const sel = this.shadow.querySelector('#scenario-select');
    sel?.addEventListener('change', (e) => this.onScenarioChange(e));
  }

  private renderMonteCarlo(): string {
    if (!this.mc) {
      return `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings to run simulations. <a href="#import">Go to Import</a></div>`;
    }
    const m = this.mc;
    const p50end = m.percentiles.p50[m.percentiles.p50.length - 1];
    return `
      <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Monte Carlo Simulation</h2>
      <div class="pae-grid pae-grid-3" style="margin-bottom:var(--space-6)">
        <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">Simulations</div><div class="pae-metric-value">${m.num_simulations.toLocaleString()}</div></div></div>
        <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">Horizon</div><div class="pae-metric-value">${m.time_horizon_months} mo</div></div></div>
        <div class="pae-card"><div class="pae-metric"><div class="pae-metric-label">P(loss)</div><div class="pae-metric-value">${(m.probability_of_loss * 100).toFixed(1)}%</div></div></div>
      </div>
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div class="pae-card-header"><span class="pae-card-title">Projected portfolio value — median ends at $${p50end.toLocaleString('en-CA', { maximumFractionDigits: 0 })}</span></div>
        <pae-chart id="mc-chart" type="fan" title="Monte Carlo projected portfolio value percentiles"></pae-chart>
      </div>
      <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">Geometric Brownian motion, ${m.num_simulations.toLocaleString()} paths. Educational illustration — not a prediction. Not investment advice.</div>`;
  }

  private renderStress(): string {
    const options = SCENARIOS.map(s =>
      `<option value="${s.id}"${s.id === this.scenario ? ' selected' : ''}>${s.label}</option>`).join('');
    let body = `
      <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Stress Testing</h2>
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <label for="scenario-select" style="font-size:var(--font-size-sm);color:var(--text-secondary)">Scenario&nbsp;</label>
        <select id="scenario-select" style="padding:6px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color)">${options}</select>
      </div>`;
    if (!this.stress) {
      body += `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings to run stress tests. <a href="#import">Go to Import</a></div>`;
    } else {
      const s = this.stress;
      const rows = s.position_impacts.map(p => `
        <tr><td><strong>${p.symbol}</strong></td>
        <td class="numeric">${p.impact_pct.toFixed(2)}%</td>
        <td class="numeric">$${p.impact_value.toLocaleString('en-CA', { maximumFractionDigits: 0 })}</td></tr>`).join('');
      body += `
        <div class="pae-grid pae-grid-2" style="margin-bottom:var(--space-6)">
          <div class="pae-card">
            <div class="pae-metric"><div class="pae-metric-label">Portfolio impact (${s.scenario})</div>
            <div class="pae-metric-value" style="color:var(--color-negative)">${s.portfolio_impact_pct.toFixed(2)}%</div></div>
          </div>
          <div class="pae-card">
            <div class="pae-card-header"><span class="pae-card-title">Impact by position</span></div>
            <pae-chart id="stress-chart" type="bar" title="Position-level stress impact"></pae-chart>
          </div>
        </div>
        <div class="pae-card"><div class="pae-card-header"><span class="pae-card-title">Position impacts</span></div>
          <table class="pae-table"><thead><tr><th>Symbol</th><th>Impact %</th><th>Impact $</th></tr></thead><tbody>${rows}</tbody></table>
        </div>`;
    }
    body += `<div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-4)">Historical scenarios applied to current holdings. Educational — not investment advice.</div>`;
    return body;
  }

  private drawCharts(): void {
    if (this.view === 'montecarlo' && this.mc) {
      const n = this.mc.percentiles.p50.length;
      const labels = Array.from({ length: n }, (_, i) => `M${i + 1}`);
      const chart = this.shadow.querySelector('pae-chart#mc-chart') as unknown as {
        setFan(d: unknown): void;
      } | null;
      chart?.setFan({
        labels,
        p5: this.mc.percentiles.p5,
        p25: this.mc.percentiles.p25,
        p50: this.mc.percentiles.p50,
        p75: this.mc.percentiles.p75,
        p95: this.mc.percentiles.p95,
        color: '#38bdf8',
        yLabel: 'Portfolio value $',
      });
    }
    if (this.view === 'stress' && this.stress) {
      const chart = this.shadow.querySelector('pae-chart#stress-chart') as unknown as {
        setSeries(d: unknown): void;
      } | null;
      chart?.setSeries({
        labels: this.stress.position_impacts.map(p => p.symbol),
        yLabel: 'Impact %',
        datasets: [{
          label: 'Impact %',
          data: this.stress.position_impacts.map(p => p.impact_pct),
          color: '#fb7185',
        }],
      });
    }
  }
}

customElements.define('pae-scenarios', PaeScenarios);

export {};
