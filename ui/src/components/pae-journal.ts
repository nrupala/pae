// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE Decision Journal View — serves the #journal route.
 *
 * Journal entries are versioned records in the Rust engine
 * (entity type "decision_entry"), read and written through the
 * engine's versioning API:
 * - POST /api/v1/version/snapshot  -> latest version of every entry
 * - POST /api/v1/version/history   -> every version of one entry
 * - POST /api/v1/version           -> append a new entry (version 1
 *   of a fresh entity id)
 *
 * Since Phase 3B the engine persists those records to its SQLite
 * database, so entries written here survive engine restarts.
 *
 * Content encryption (Phase 3C): entries written from this view are
 * encrypted IN THE BROWSER before they are saved. The user unlocks
 * the vault with their passphrase (vault-client's unlockVault —
 * Argon2id derivation and DEK unwrap happen client-side; the
 * passphrase never leaves this page and the key lives only in page
 * memory), and each new entry is sealed with encryptWithDEK
 * (AES-256-GCM, WebCrypto) so the engine stores only opaque
 * ciphertext + a real random nonce. Reading an encrypted entry
 * likewise requires the unlocked vault.
 *
 * Compatibility, stated plainly: entries written by the Phase 3B
 * version of this view (and by scripts/seed_demo.py) were stored as
 * base64-encoded JSON with a zero nonce — NOT encrypted. Those
 * still decode and display, and the detail view labels them as
 * written before encryption was wired. An entry this view can
 * neither decrypt nor decode was encrypted under a different key;
 * the view says so and shows its (never-encrypted) summary, date,
 * and tags instead of pretending to read it.
 *
 * The journal records the user's own reasoning. It never scores,
 * judges, or advises.
 */

import {
  decryptWithDEK,
  encryptWithDEK,
  unlockVault,
  type VaultSession,
} from '../crypto/vault-client.js';

const ENGINE_BASE = 'http://localhost:3001';

interface VersionRecord {
  version_hash: string;
  entity_id: string;
  entity_type: string;
  version: number;
  created_at: string;
  change_summary: string | null;
  tags: string[];
  content_encrypted_b64: string;
  nonce_b64: string;
  parent_hash: string | null;
}

interface SnapshotResponse {
  entities: VersionRecord[];
}

interface HistoryResponse {
  entity_id: string;
  total_versions: number;
  versions: VersionRecord[];
}

/** Journal fields the detail view knows how to label, in read order. */
const FIELD_LABELS: Array<{ key: string; label: string }> = [
  { key: 'action', label: 'The decision' },
  { key: 'rationale', label: 'Why' },
  { key: 'thesis', label: 'Thinking behind it' },
  { key: 'alternatives_considered', label: 'Other options considered' },
  { key: 'symbols_affected', label: 'Holdings involved' },
  { key: 'time_horizon', label: 'Time horizon' },
  { key: 'what_could_go_wrong', label: 'What could go wrong' },
  { key: 'emotional_state', label: 'Mindset at the time' },
  { key: 'market_context', label: 'The market at the time' },
  { key: 'trigger', label: 'What prompted it' },
];

/** A decoded entry plus how it was stored (for honest labelling). */
interface DecodedEntry {
  data: Record<string, unknown>;
  encrypted: boolean;
}

class PaeJournal extends HTMLElement {
  private shadow: ShadowRoot;
  private entries: VersionRecord[] = [];
  private selected: HistoryResponse | null = null;
  private showForm: boolean = false;
  private loading: boolean = true;
  private saving: boolean = false;
  private error: string = '';
  /** Unlocked vault session (DEK in page memory only; never stored). */
  private session: VaultSession | null = null;
  private unlocking: boolean = false;
  private vaultError: string = '';
  /** version_hash -> decoded content, filled by decodeVisible(). */
  private decoded = new Map<string, DecodedEntry>();

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
    void this.loadEntries();
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  private async postJson<T>(path: string, body: unknown): Promise<T> {
    const resp = await fetch(`${ENGINE_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`HTTP ${resp.status} ${detail}`);
    }
    return (await resp.json()) as T;
  }

  private async loadEntries(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.render();
    try {
      const snap = await this.postJson<SnapshotResponse>('/api/v1/version/snapshot', {
        entity_types: ['decision_entry'],
        entity_ids: [],
      });
      this.entries = [...snap.entities].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      );
      this.loading = false;
      await this.decodeVisible();
    } catch (e) {
      this.loading = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
    }
    this.render();
  }

  private async openEntry(entityId: string): Promise<void> {
    try {
      this.selected = await this.postJson<HistoryResponse>('/api/v1/version/history', {
        entity_id: entityId,
      });
      await this.decodeVisible();
    } catch (e) {
      this.error = e instanceof Error ? e.message : 'Unknown error';
    }
    this.render();
  }

  /**
   * Fill the decoded-content cache for everything currently loaded.
   * Legacy base64-JSON entries (Phase 3B stand-in, see header) decode
   * synchronously; genuinely encrypted entries need the unlocked
   * vault's DEK and WebCrypto, hence the async pass.
   */
  private async decodeVisible(): Promise<void> {
    const records = [...this.entries, ...(this.selected?.versions ?? [])];
    for (const rec of records) {
      if (this.decoded.has(rec.version_hash)) continue;
      const legacy = this.decodeLegacy(rec);
      if (legacy) {
        this.decoded.set(rec.version_hash, { data: legacy, encrypted: false });
        continue;
      }
      if (this.session) {
        try {
          const text = await decryptWithDEK(
            this.session.dek,
            rec.content_encrypted_b64,
            rec.nonce_b64,
          );
          const parsed: unknown = JSON.parse(text);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            this.decoded.set(rec.version_hash, {
              data: parsed as Record<string, unknown>,
              encrypted: true,
            });
          }
        } catch {
          // Wrong key or tampered content — stays undecoded; the
          // detail view explains instead of pretending to read it.
        }
      }
    }
  }

  /** Decode a pre-3C entry: base64-encoded JSON, not ciphertext. */
  private decodeLegacy(rec: VersionRecord): Record<string, unknown> | null {
    try {
      const bin = atob(rec.content_encrypted_b64);
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Not decodable JSON — genuinely encrypted content.
    }
    return null;
  }

  private decode(rec: VersionRecord): DecodedEntry | null {
    return this.decoded.get(rec.version_hash) ?? null;
  }

  private titleOf(rec: VersionRecord): string {
    const decoded = this.decode(rec);
    const title = decoded?.data['title'];
    if (typeof title === 'string' && title.trim()) return title;
    if (rec.change_summary?.trim()) return rec.change_summary;
    return rec.entity_id;
  }

  /** Unlock the vault with the passphrase typed in the vault bar. */
  private async unlock(): Promise<void> {
    const input = this.shadow.querySelector('#j-pass') as HTMLInputElement | null;
    const passphrase = input?.value ?? '';
    if (!passphrase) {
      this.vaultError = 'Type your vault passphrase first.';
      this.render();
      return;
    }
    this.unlocking = true;
    this.vaultError = '';
    this.render();
    try {
      this.session = await unlockVault(ENGINE_BASE, passphrase);
      this.unlocking = false;
      await this.decodeVisible();
    } catch {
      this.unlocking = false;
      this.session = null;
      this.vaultError =
        'Could not unlock — wrong passphrase, or the engine is not reachable.';
    }
    this.render();
  }

  /** Lock the vault: the key leaves page memory with the session. */
  private lock(): void {
    this.session = null;
    this.decoded.clear();
    void this.decodeVisible().then(() => this.render());
    this.render();
  }

  private async saveEntry(): Promise<void> {
    const get = (id: string): string =>
      (this.shadow.querySelector(id) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null)?.value.trim() ?? '';
    const title = get('#j-title');
    const action = get('#j-action');
    if (!title && !action) {
      this.error = 'Give the entry a title or say what you decided.';
      this.render();
      return;
    }
    if (!this.session) {
      // Entries are genuinely encrypted (Phase 3C) — that needs the
      // unlocked vault. No silent plaintext fallback.
      this.error =
        'Unlock your vault above first — entries are encrypted in your browser before they are saved.';
      this.render();
      return;
    }
    const confidenceRaw = parseInt(get('#j-confidence'), 10);
    const content: Record<string, unknown> = {
      kind: 'decision_entry',
      title: title || action,
      action,
      rationale: get('#j-rationale'),
      alternatives_considered: get('#j-alternatives')
        .split('\n')
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
      confidence: Number.isFinite(confidenceRaw) ? confidenceRaw : 5,
      what_could_go_wrong: get('#j-wrong'),
      timestamp: new Date().toISOString(),
    };
    const tags = get('#j-tags')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const suffix = Math.random().toString(36).slice(2, 8);
    const entityId = `journal-${Date.now().toString(36)}-${suffix}`;

    this.saving = true;
    this.error = '';
    this.render();
    try {
      const sealed = await encryptWithDEK(
        this.session.dek,
        JSON.stringify(content),
      );
      await this.postJson('/api/v1/version', {
        entity_id: entityId,
        entity_type: 'decision_entry',
        content_encrypted_b64: sealed.ciphertext_b64,
        nonce_b64: sealed.nonce_b64,
        change_summary: title || action,
        tags,
      });
      this.showForm = false;
      this.saving = false;
      await this.loadEntries();
      await this.openEntry(entityId);
    } catch (e) {
      this.saving = false;
      this.error = e instanceof Error ? e.message : 'Unknown error';
      this.render();
    }
  }

  private renderList(): string {
    if (!this.entries.length) {
      return `<div style="text-align:center;padding:var(--space-16);color:var(--text-secondary)">
        Nothing written yet. Your first entry is one click away — use
        <strong>Write a new entry</strong> above.</div>`;
    }
    const rows = this.entries
      .map((rec) => {
        const date = new Date(rec.created_at).toLocaleString();
        const tags = rec.tags.length
          ? `<div style="margin-top:4px">${rec.tags
              .map(
                (t) =>
                  `<span style="font-size:var(--font-size-xs);background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:var(--radius-sm);padding:1px 6px;margin-right:4px">${this.esc(t)}</span>`,
              )
              .join('')}</div>`
          : '';
        return `
        <div class="pae-card" style="margin-bottom:var(--space-3)">
          <button class="journal-open" data-entity="${this.esc(rec.entity_id)}"
            style="all:unset;display:block;width:100%;cursor:pointer">
            <div style="font-weight:600;color:var(--text-primary)">${this.esc(this.titleOf(rec))}</div>
            <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">${this.esc(date)} · version ${rec.version}</div>
            ${tags}
          </button>
        </div>`;
      })
      .join('');
    return rows;
  }

  private renderDetail(): string {
    const sel = this.selected;
    if (!sel || !sel.versions.length) return '';
    const latest = sel.versions[sel.versions.length - 1];
    const decoded = this.decode(latest);
    let body: string;
    if (decoded) {
      const data = decoded.data;
      const fields = FIELD_LABELS.map(({ key, label }) => {
        const v = data[key];
        if (v === undefined || v === null || v === '') return '';
        const text = Array.isArray(v) ? v.map((x) => String(x)).join(', ') : String(v);
        if (!text.trim()) return '';
        return `<div style="margin-bottom:var(--space-4)">
          <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em">${label}</div>
          <div style="color:var(--text-primary);white-space:pre-wrap">${this.esc(text)}</div>
        </div>`;
      }).join('');
      const confidence = data['confidence'];
      const confidenceLine =
        typeof confidence === 'number'
          ? `<div style="margin-bottom:var(--space-4)">
               <div style="font-size:var(--font-size-xs);font-weight:600;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.05em">Confidence at the time</div>
               <div style="color:var(--text-primary)">${confidence} / 10</div>
             </div>`
          : '';
      const provenanceLine = decoded.encrypted
        ? ''
        : `<div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-3)">
             Written before journal encryption was wired (Phase 3C) — this
             entry was stored as readable text, not encrypted.</div>`;
      body = fields + confidenceLine + provenanceLine;
    } else if (this.session) {
      body = `<div style="color:var(--text-secondary)">
        This entry is encrypted with a different key than the vault you
        unlocked, so its text cannot be shown here. Its summary, date,
        and tags above are never encrypted.</div>`;
    } else {
      body = `<div style="color:var(--text-secondary)">
        This entry is encrypted. Unlock your vault above to read it
        here. Its summary, date, and tags above are never encrypted.</div>`;
    }
    const prior =
      sel.total_versions > 1
        ? `<div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-3)">
             ${sel.total_versions} versions of this entry are kept — nothing is ever overwritten.</div>`
        : '';
    return `
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div class="pae-card-header">
          <span class="pae-card-title">${this.esc(this.titleOf(latest))}</span>
          <button id="journal-back" style="padding:6px 12px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);cursor:pointer">Back to all entries</button>
        </div>
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-bottom:var(--space-4)">
          ${this.esc(new Date(latest.created_at).toLocaleString())}
          ${latest.change_summary ? ` · ${this.esc(latest.change_summary)}` : ''}
        </div>
        ${body}
        ${prior}
      </div>`;
  }

  private renderForm(): string {
    const inputStyle =
      'width:100%;box-sizing:border-box;padding:8px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);margin-top:4px';
    const labelStyle =
      'display:block;font-size:var(--font-size-sm);color:var(--text-secondary);margin-bottom:var(--space-4)';
    const confidenceOptions = Array.from({ length: 10 }, (_, i) => i + 1)
      .map((n) => `<option value="${n}"${n === 5 ? ' selected' : ''}>${n}</option>`)
      .join('');
    return `
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div class="pae-card-header"><span class="pae-card-title">New journal entry</span></div>
        <label style="${labelStyle}">Title
          <input id="j-title" type="text" style="${inputStyle}" placeholder="A few words to find this entry later" /></label>
        <label style="${labelStyle}">What did you decide?
          <textarea id="j-action" rows="2" style="${inputStyle}" placeholder="The decision, in your own words"></textarea></label>
        <label style="${labelStyle}">Why?
          <textarea id="j-rationale" rows="3" style="${inputStyle}" placeholder="Your reasoning, before you act on it"></textarea></label>
        <label style="${labelStyle}">What else did you consider? (one per line)
          <textarea id="j-alternatives" rows="2" style="${inputStyle}"></textarea></label>
        <label style="${labelStyle}">How sure are you? (1–10)
          <select id="j-confidence" style="${inputStyle}">${confidenceOptions}</select></label>
        <label style="${labelStyle}">What could go wrong?
          <textarea id="j-wrong" rows="2" style="${inputStyle}"></textarea></label>
        <label style="${labelStyle}">Tags (comma separated)
          <input id="j-tags" type="text" style="${inputStyle}" placeholder="e.g. rebalance, taxes" /></label>
        <div style="display:flex;gap:var(--space-3)">
          <button id="journal-save" ${this.saving ? 'disabled' : ''}
            style="padding:8px 16px;border-radius:6px;background:var(--accent-primary);color:var(--text-inverse);border:none;cursor:pointer">
            ${this.saving ? 'Saving…' : 'Save entry'}</button>
          <button id="journal-cancel"
            style="padding:8px 16px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);cursor:pointer">
            Cancel</button>
        </div>
      </div>`;
  }

  private renderVaultBar(): string {
    const inputStyle =
      'box-sizing:border-box;padding:8px 10px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color)';
    if (this.session) {
      return `
        <div class="pae-card" style="margin-bottom:var(--space-6);display:flex;align-items:center;gap:var(--space-4)">
          <div style="flex:1;color:var(--text-secondary);font-size:var(--font-size-sm)">
            Vault unlocked — entries you write are encrypted in your
            browser before they are saved, and encrypted entries can be
            read here. The key stays in this page's memory only.
          </div>
          <button id="journal-lock" style="padding:8px 16px;border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border-color);cursor:pointer">Lock vault</button>
        </div>`;
    }
    return `
      <div class="pae-card" style="margin-bottom:var(--space-6)">
        <div style="color:var(--text-secondary);font-size:var(--font-size-sm);margin-bottom:var(--space-3)">
          Your journal entries are encrypted in your browser. Unlock
          your vault to write new entries and read encrypted ones —
          your passphrase never leaves this page, and the key is kept
          only in this page's memory.
        </div>
        <div style="display:flex;gap:var(--space-3);align-items:center">
          <input id="j-pass" type="password" autocomplete="current-password"
            style="${inputStyle};flex:1" placeholder="Vault passphrase" />
          <button id="journal-unlock" ${this.unlocking ? 'disabled' : ''}
            style="padding:8px 16px;border-radius:6px;background:var(--accent-primary);color:var(--text-inverse);border:none;cursor:pointer">
            ${this.unlocking ? 'Unlocking…' : 'Unlock vault'}</button>
        </div>
        ${
          this.vaultError
            ? `<div style="margin-top:var(--space-3);color:var(--color-negative)">${this.esc(this.vaultError)}</div>`
            : ''
        }
      </div>`;
  }

  private render(): void {
    let content: string;
    if (this.loading) {
      content = `<div style="text-align:center;padding:var(--space-16);color:var(--text-tertiary)">Loading journal…</div>`;
    } else {
      const detail = this.selected ? this.renderDetail() : '';
      const list = this.selected ? '' : this.renderList();
      const form = this.showForm && !this.selected ? this.renderForm() : '';
      const errorLine = this.error
        ? `<div style="padding:var(--space-4);margin-bottom:var(--space-4);color:var(--color-negative)">Something went wrong: ${this.esc(this.error)}</div>`
        : '';
      content = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-6)">
          <h2 style="font-size:var(--font-size-xl);font-weight:700;margin:0;color:var(--text-primary)">Decision Journal</h2>
          ${
            this.selected
              ? ''
              : `<button id="journal-new" style="padding:8px 16px;border-radius:6px;background:var(--accent-primary);color:var(--text-inverse);border:none;cursor:pointer">Write a new entry</button>`
          }
        </div>
        ${errorLine}
        ${this.renderVaultBar()}
        ${form}
        ${detail}
        ${list}
        <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:var(--space-6)">
          Your journal records your reasoning in your own words — it is
          kept, encrypted, in your local PAE database and survives
          restarts. PAE stores what you wrote; it does not score it or
          act on it.
        </div>`;
    }
    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <div>${content}</div>`;

    this.shadow.querySelector('#journal-new')?.addEventListener('click', () => {
      this.showForm = true;
      this.render();
    });
    this.shadow.querySelector('#journal-unlock')?.addEventListener('click', () => {
      void this.unlock();
    });
    this.shadow.querySelector('#journal-lock')?.addEventListener('click', () => {
      this.lock();
    });
    this.shadow.querySelector('#j-pass')?.addEventListener('keydown', (e) => {
      if ((e as KeyboardEvent).key === 'Enter') void this.unlock();
    });
    this.shadow.querySelector('#journal-cancel')?.addEventListener('click', () => {
      this.showForm = false;
      this.error = '';
      this.render();
    });
    this.shadow.querySelector('#journal-save')?.addEventListener('click', () => {
      void this.saveEntry();
    });
    this.shadow.querySelector('#journal-back')?.addEventListener('click', () => {
      this.selected = null;
      this.render();
    });
    this.shadow.querySelectorAll('.journal-open').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-entity');
        if (id) void this.openEntry(id);
      });
    });
  }
}

customElements.define('pae-journal', PaeJournal);

export {};
