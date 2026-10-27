// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Carry View — serves the #carry route.
 *
 * Margin carry arithmetic from POST /api/v1/analytics/carry
 * (analytics service): given how much margin the user has borrowed
 * and the rate they pay, it works out the income the holdings
 * produce per year, the borrowing cost per year, and the difference.
 *
 * Figures only, from the user's own numbers — this view shows the
 * math; it does not suggest borrowing more or less.
 */

const API_BASE = 'http://localhost:3002';

const DISCLOSURE =
  'Educational analytics only. PAE calculates; the user decides. ' +
  'No investment advice.';

interface PositionCarry {
  symbol: string;
  market_value: number;
  yield_pct: number;
  annual_income: number;
  margin_cost: number;
  net_carry: number;
  carry_spread: number;
}

interface CarryResponse {
  total_nav: number;
  total_long_value: number;
  total_margin: number;
  leverage_ratio: number;
  total_annual_income: number;
  total_annual_margin_cost: number;
  net_carry: number;
  income_coverage_ratio: number;
  positions: PositionCarry[];
}

class PaeCarry extends HTMLElement {
  private shadow: ShadowRoot;
  private result: CarryResponse | null = null;
  private portfolioId: string = '';
  private portfolioName: string = '';
  private margin: number = 0;
  private ratePct: number = 5.8;
  private loading: boolean = true;
  private computing: boolean = false;
  private error: string = '';

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    void this.loadPortfolio();
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  private async loadPortfolio(): Promise<void> {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/portfolios`);
      if (!resp.ok) throw new Error(`portfolios: HTTP ${resp.status}`);
      const portfolios = (await resp.json()).portfolios || [];
      if (!portfolios.length) throw new Error('empty');
      this.portfolioId = portfolios[0].id;
      this.portfolioName = portfolios[0].name || '';
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      if (this.error === 'empty') this.error = '';
    }
    this.render();
  }

  private async compute(): Promise<void> {
    const marginInput = this.shadow.querySelector('#carry-margin') as HTMLInputElement | null;
    const rateInput = this.shadow.querySelector('#carry-rate') as HTMLInputElement | null;
    const margin = parseFloat(marginInput?.value ?? '0');
    const ratePct = parseFloat(rateInput?.value ?? '5.8');
    this.margin = Number.isFinite(margin) && margin >= 0 ? margin : 0;
    this.ratePct = Number.isFinite(ratePct) && ratePct >= 0 ? ratePct : 0;
    this.computing = true;
    this.error = '';
    this.render();
    try {
      const resp = await fetch(`${API_BASE}/api/v1/analytics/carry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          portfolio_id: this.portfolioId,
          total_margin: this.margin,
          margin_rate: this.ratePct / 100,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`carry: HTTP ${resp.status} ${detail}`);
      }
      this.result = (await resp.json()) as CarryResponse;
      this.computing = false;
    } catch (e) {
      this.computing = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
    }
    this.render();
  }

  private money(v: number): string {
    return v.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

  private renderResult(r: CarryResponse): string {
    const rows = r.positions
      .map(
        (p) => `<tr>
          <td><strong>${this.esc(p.symbol)}</strong></td>
          <td class="numeric">$${this.money(p.market_value)}</td>
          <td class="numeric">${p.yield_pct.toFixed(2)}%</td>
          <td class="numeric">$${this.money(p.annual_income)}</td>
          <td class="numeric">$${this.money(p.margin_cost)}</td>
          <td class="numeric">$${this.money(p.net_carry)}</td>
        </tr>`,
      )
      .join('');
    return `
      <div class="pae-grid pae-grid-4" style="margin-bottom:var(--space-6)">
        ${this.metricCard('Portfolio value', `$${this.money(r.total_long_value)}`, 'What the holdings are worth')}
        ${this.metricCard('Borrowed on margin', `$${this.money(r.total_margin)}`, `${r.leverage_ratio.toFixed(2)}× leverage`)}
        ${this.metricCard('Income per year', `$${this.money(r.total_annual_income)}`, 'From holding yields')}
        ${this.metricCard('Borrowing cost per year', `$${this.money(r.total_annual_margin_cost)}`, 'Interest on the margin')}
        ${this.metricCard('Net carry per year', `$${this.money(r.net_carry)}`, 'Income minus borrowing cost')}
        ${this.metricCard('Income covers cost', `${r.income_coverage_ratio.toFixed(2)}×`, 'Above 1× means income exceeds interest')}
      </div>
      <div class="pae-card">
        <div class="pae-card-header"><span class="pae-card-title">By holding</span></div>
        <table class="pae-table">
          <thead><tr><th>Holding</th><th style="text-align:right">Value</th><th style="text-align:right">Yield</th><th style="text-align:right">Income / yr</th><th style="text-align:right">Cost / yr</th><th style="text-align:right">Net / yr</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Loading…</div>`;
    } else if (!this.portfolioId) {
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Carry Analysis</h2>
        <div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">Import holdings first — carry is worked out from what you hold. <a href="#import">Go to Import</a></div>`;
    } else {
      const inputStyle =
        'padding:6px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);width:10em';
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-2);color:var(--text-primary)">Carry Analysis</h2>
        <div style="font-size:var(--font-size-sm);color:var(--text-secondary);margin-bottom:var(--space-6)">
          What your holdings earn versus what borrowed money costs${this.portfolioName ? ` — ${this.esc(this.portfolioName)}` : ''}.
        </div>
        <div class="pae-card" style="margin-bottom:var(--space-6)">
          <div style="display:flex;gap:var(--space-6);flex-wrap:wrap;align-items:end">
            <label style="font-size:var(--font-size-sm);color:var(--text-secondary)">Amount borrowed on margin
              <div><input id="carry-margin" type="number" min="0" step="100" value="${this.margin}" style="${inputStyle}" /></div></label>
            <label style="font-size:var(--font-size-sm);color:var(--text-secondary)">Margin interest rate (% per year)
              <div><input id="carry-rate" type="number" min="0" step="0.1" value="${this.ratePct}" style="${inputStyle}" /></div></label>
            <button id="carry-go" ${this.computing ? 'disabled' : ''}
              style="padding:8px 16px;border-radius:6px;background:var(--accent-primary);color:var(--text-inverse);border:none;cursor:pointer">
              ${this.computing ? 'Working…' : 'Work out the carry'}</button>
          </div>
        </div>
        ${this.error ? `<div style="padding:var(--space-4);margin-bottom:var(--space-4);color:var(--color-negative)">Failed: ${this.esc(this.error)}</div>` : ''}
        ${this.result ? this.renderResult(this.result) : ''}
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-6)">${DISCLOSURE}</div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
    this.shadow.querySelector('#carry-go')?.addEventListener('click', () => {
      void this.compute();
    });
  }
}

customElements.define('pae-carry', PaeCarry);

export {};
