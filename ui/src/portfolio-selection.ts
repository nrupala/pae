// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Shared portfolio selection for the dashboard (Phase 3D, W1).
 *
 * Before 3D every data component fetched the portfolio list and
 * silently used the FIRST entry. Analytics auto-creates an empty
 * "Default" portfolio, so on a seeded install the first entry was
 * the empty one and the seeded "Demo Portfolio" was unreachable in
 * the UI — the dashboard opened on zeros.
 *
 * The rule now lives in exactly one place:
 * - the app shell (pae-app) renders ONE selector and owns the choice;
 * - the choice persists in localStorage and is shared by every view;
 * - the default (and the fallback when a stored choice no longer
 *   exists) is the first portfolio WITH holdings, else the first
 *   portfolio — a fresh install with only the empty Default still
 *   opens on Default, and a seeded install opens on the demo.
 *
 * Components keep their existing data calls; only the source of the
 * portfolio id changes (this module instead of `portfolios[0]`).
 */

export interface PortfolioListItem {
  id: string;
  name: string;
  holding_count?: number;
}

const STORAGE_KEY = 'pae-selected-portfolio-id';

/** Fetch the portfolio list from the analytics data plane (same-origin). */
export async function fetchPortfolios(): Promise<PortfolioListItem[]> {
  const resp = await fetch('/api/v1/portfolios');
  if (!resp.ok) throw new Error(`portfolios: HTTP ${resp.status}`);
  const data = await resp.json();
  return (data.portfolios || []) as PortfolioListItem[];
}

/** The default choice: first portfolio with holdings, else the first. */
export function defaultPortfolioId(portfolios: PortfolioListItem[]): string {
  const withHoldings = portfolios.find((p) => (p.holding_count ?? 0) > 0);
  return (withHoldings ?? portfolios[0]).id;
}

/**
 * Resolve the effective selection for a fetched list: the stored
 * choice when it still exists, otherwise the default. The resolved
 * id is stored back, so every view agrees on the same portfolio.
 * Callers must pass a non-empty list.
 */
export function resolveSelectedPortfolioId(
  portfolios: PortfolioListItem[],
): string {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch {
    // localStorage may be unavailable (private browsing); the
    // default rule still applies for this view.
  }
  const id =
    stored !== null && portfolios.some((p) => p.id === stored)
      ? stored
      : defaultPortfolioId(portfolios);
  storeSelectedPortfolioId(id);
  return id;
}

/** Persist the user's choice (the app shell calls this on change). */
export function storeSelectedPortfolioId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Unavailable storage: selection lasts for the view only.
  }
}

/**
 * Fetch the list and resolve the selected portfolio id.
 * Throws Error('empty') when no portfolio exists — the same signal
 * the components' previous inline list fetches produced, so their
 * empty-state handling is unchanged.
 */
export async function selectedPortfolioId(): Promise<string> {
  const portfolios = await fetchPortfolios();
  if (!portfolios.length) throw new Error('empty');
  return resolveSelectedPortfolioId(portfolios);
}
