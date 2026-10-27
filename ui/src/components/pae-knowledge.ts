// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Knowledge Base View — serves the #knowledge route.
 *
 * Reads the user's personal knowledge base (the PKE store) through
 * the analytics service's read-only endpoints:
 * - GET /api/v1/knowledge/chunks  -> newest notes first
 * - GET /api/v1/knowledge/search?q= -> keyword search
 *
 * Adding notes (ingest) is not a dashboard action today — it runs
 * through the PKE library outside this view, and this view does not
 * pretend otherwise.
 *
 * If the knowledge base is encrypted, chunk text never reaches this
 * view (the analytics process holds no key): such notes show as
 * locked, with their source and themes still visible — metadata is
 * never encrypted.
 */

// Same-origin (Phase 3D, W5): the engine serves this dashboard and
// proxies the analytics data plane, so relative paths work at any
// configured port pair. Do not hardcode a host:port here again.
const API_BASE = '';

interface KnowledgeChunk {
  chunk_id: string;
  source: string;
  author: string;
  date: string;
  themes: string[];
  text: string | null;
  encrypted: boolean;
  score?: number;
}

interface ChunksResponse {
  total: number;
  chunks: KnowledgeChunk[];
}

interface SearchResponse {
  query: string;
  results: KnowledgeChunk[];
  locked?: boolean;
}

class PaeKnowledge extends HTMLElement {
  private shadow: ShadowRoot;
  private chunks: KnowledgeChunk[] = [];
  private total: number = 0;
  private results: KnowledgeChunk[] | null = null;
  private searchLocked: boolean = false;
  private query: string = '';
  private loading: boolean = true;
  private error: string = '';

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    void this.loadChunks();
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  private async loadChunks(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();
    try {
      const resp = await fetch(`${API_BASE}/api/v1/knowledge/chunks?limit=50`);
      if (!resp.ok) throw new Error(`knowledge: HTTP ${resp.status}`);
      const data = (await resp.json()) as ChunksResponse;
      this.total = data.total;
      this.chunks = data.chunks;
      this.loading = false;
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
    }
    this.render();
  }

  private async runSearch(): Promise<void> {
    const input = this.shadow.querySelector('#kb-q') as HTMLInputElement | null;
    const q = (input?.value ?? '').trim();
    if (!q) {
      this.results = null;
      this.searchLocked = false;
      this.query = '';
      this.render();
      return;
    }
    this.query = q;
    try {
      const resp = await fetch(
        `${API_BASE}/api/v1/knowledge/search?q=${encodeURIComponent(q)}&limit=20`,
      );
      if (!resp.ok) throw new Error(`search: HTTP ${resp.status}`);
      const data = (await resp.json()) as SearchResponse;
      this.results = data.results;
      this.searchLocked = data.locked === true;
      this.error = '';
    } catch (e) {
      this.error = e instanceof Error ? e.message : 'Unknown error';
    }
    this.render();
  }

  private chunkCard(c: KnowledgeChunk): string {
    const themes = c.themes.length
      ? `<span style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${c.themes.map((t) => this.esc(t)).join(' · ')}</span>`
      : '';
    const score =
      c.score !== undefined
        ? `<span style="font-size:var(--font-size-xs);color:var(--text-tertiary)">match ${(c.score * 100).toFixed(0)}%</span>`
        : '';
    const body =
      c.text === null
        ? `<div style="color:var(--text-secondary)">This note is encrypted, so its text cannot be shown here — unlock the vault to read it.</div>`
        : `<div style="color:var(--text-primary);white-space:pre-wrap">${this.esc(c.text)}</div>`;
    return `
      <div class="pae-card" style="margin-bottom:var(--space-3)">
        <div style="display:flex;justify-content:space-between;gap:var(--space-3);margin-bottom:var(--space-2)">
          <span style="font-weight:600;color:var(--text-primary)">${this.esc(c.source || 'Untitled note')}</span>
          ${score}
        </div>
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-bottom:var(--space-2)">
          ${c.date ? `${this.esc(c.date)} · ` : ''}${themes}
        </div>
        ${body}
      </div>`;
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Loading knowledge base…</div>`;
    } else if (this.error && !this.chunks.length && this.results === null) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--color-negative)">Failed to load the knowledge base: ${this.esc(this.error)}</div>`;
    } else {
      let listing: string;
      if (this.results !== null) {
        if (this.searchLocked) {
          listing = `<div style="text-align:center;padding:var(--space-8);color:var(--text-secondary)">
            Your knowledge base is encrypted, so search cannot read it
            here. Your notes below are still listed.</div>`;
        } else if (!this.results.length) {
          listing = `<div style="text-align:center;padding:var(--space-8);color:var(--text-secondary)">
            No notes match “${this.esc(this.query)}”.</div>`;
        } else {
          listing =
            `<div style="font-size:var(--font-size-sm);color:var(--text-secondary);margin-bottom:var(--space-3)">${this.results.length} note(s) match “${this.esc(this.query)}”</div>` +
            this.results.map((c) => this.chunkCard(c)).join('');
        }
      } else if (!this.chunks.length) {
        listing = `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">
          Nothing here yet — this view fills up as documents are added
          to your knowledge base.</div>`;
      } else {
        listing =
          `<div style="font-size:var(--font-size-sm);color:var(--text-secondary);margin-bottom:var(--space-3)">${this.total} note(s), newest first</div>` +
          this.chunks.map((c) => this.chunkCard(c)).join('');
      }
      content = `
        <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0 0 var(--space-6);color:var(--text-primary)">Knowledge Base</h2>
        <div class="pae-card" style="margin-bottom:var(--space-6)">
          <form id="kb-form" style="display:flex;gap:var(--space-3)">
            <input id="kb-q" type="search" value="${this.esc(this.query)}"
              placeholder="Search your notes"
              style="flex:1;padding:8px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color)" />
            <button type="submit"
              style="padding:8px 16px;border-radius:6px;background:var(--accent-primary);color:var(--text-inverse);border:none;cursor:pointer">Search</button>
            ${
              this.results !== null
                ? `<button type="button" id="kb-clear"
                    style="padding:8px 16px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);cursor:pointer">Show all notes</button>`
                : ''
            }
          </form>
        </div>
        ${this.error ? `<div style="padding:var(--space-4);margin-bottom:var(--space-4);color:var(--color-negative)">Something went wrong: ${this.esc(this.error)}</div>` : ''}
        ${listing}
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-6)">
          These are your own notes and documents, stored on this
          machine. Search reads them locally — nothing leaves your
          computer.
        </div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;

    this.shadow.querySelector('#kb-form')?.addEventListener('submit', (e) => {
      e.preventDefault();
      void this.runSearch();
    });
    this.shadow.querySelector('#kb-clear')?.addEventListener('click', () => {
      this.results = null;
      this.searchLocked = false;
      this.query = '';
      this.error = '';
      this.render();
    });
  }
}

customElements.define('pae-knowledge', PaeKnowledge);

export {};
