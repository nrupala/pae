// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Holdings View — serves the #holdings route.
 *
 * A plain read-only table of what the user holds, from
 * GET /api/v1/holdings?portfolio_id= (analytics service). Follows
 * the dashboard convention of showing the first portfolio. Editing
 * happens through Import; this view only shows.
 */

// Same-origin (Phase 3D, W5): the engine serves this dashboard and
// proxies the analytics data plane, so relative paths work at any
// configured port pair. Do not hardcode a host:port here again.
import {
  fetchPortfolios,
  resolveSelectedPortfolioId,
} from '../portfolio-selection.js';

const API_BASE = '';

interface HoldingRow {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  quantity: number;
  market_value: number;
  cost_basis: number;
  weight_pct: number;
  yield_pct: number;
  currency: string;
  unrealized_pnl: number;
}

interface HoldingsResponse {
  holdings: HoldingRow[];
  total_market_value: number;
  count: number;
}

class PaeHoldings extends HTMLElement {
  private shadow: ShadowRoot;
  private data: HoldingsResponse | null = null;
  private portfolioName: string = '';
  private loading: boolean = true;
  private error: string = '';

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    void this.loadData();
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  private async loadData(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();
    try {
      const portfolios = await fetchPortfolios();
      if (!portfolios.length) throw new Error('empty');
      const selectedId = resolveSelectedPortfolioId(portfolios);
      const selected = portfolios.find((p) => p.id === selectedId);
      this.portfolioName = selected?.name || '';
      const resp = await fetch(
        `${API_BASE}/api/v1/holdings?portfolio_id=${encodeURIComponent(selectedId)}`,
      );
      if (!resp.ok) throw new Error(`holdings: HTTP ${resp.status}`);
      this.data = (await resp.json()) as HoldingsResponse;
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      if (this.error === 'empty') this.error = '';
    }
    this.render();
  }

  private money(v: number): string {
    return v.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Loading holdings…</div>`;
    } else if (this.error) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--color-negative)">Failed to load holdings: ${this.esc(this.error)}</div>`;
    } else if (!this.data || !this.data.count) {
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Holdings</h2>
        <div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">No holdings yet. <a href="#import">Go to Import</a> to add some.</div>`;
    } else {
      const rows = this.data.holdings
        .map((h) => {
          const pnlColor =
            h.unrealized_pnl > 0
              ? 'var(--color-positive)'
              : h.unrealized_pnl < 0
                ? 'var(--color-negative)'
                : 'var(--text-primary)';
          const pnlText = `${h.unrealized_pnl >= 0 ? '+' : '−'}$${this.money(Math.abs(h.unrealized_pnl))}`;
          return `<tr>
            <td><strong>${this.esc(h.symbol)}</strong>${h.name ? `<div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${this.esc(h.name)}</div>` : ''}</td>
            <td>${this.esc(h.asset_class.replace(/_/g, ' '))}</td>
            <td class="numeric">${this.money(h.quantity)}</td>
            <td class="numeric">$${this.money(h.market_value)}</td>
            <td class="numeric">$${this.money(h.cost_basis)}</td>
            <td class="numeric" style="color:${pnlColor}">${pnlText}</td>
            <td class="numeric">${h.weight_pct.toFixed(2)}%</td>
          </tr>`;
        })
        .join('');
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-2);color:var(--text-primary)">Holdings</h2>
        <div style="font-size:var(--font-size-sm);color:var(--text-secondary);margin-bottom:var(--space-6)">
          ${this.portfolioName ? `${this.esc(this.portfolioName)} · ` : ''}${this.data.count} holding(s) · total value $${this.money(this.data.total_market_value)}
        </div>
        <div class="pae-card">
          <table class="pae-table">
            <thead><tr>
              <th>Holding</th><th>Type</th><th style="text-align:right">Quantity</th>
              <th style="text-align:right">Market value</th><th style="text-align:right">Cost</th>
              <th style="text-align:right">Gain / loss</th><th style="text-align:right">Share</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-4)">
          To change holdings, use <a href="#import">Import</a>. Figures are as stored — PAE shows your numbers, it does not update prices.
        </div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;
  }
}

customElements.define('pae-holdings', PaeHoldings);

export {};
