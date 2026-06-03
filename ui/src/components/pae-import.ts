/**
 * PAE CSV Import Component.
 * Drag-and-drop file upload with preview-then-confirm flow.
 * Vanilla Web Component.
 *
 * Flow:
 * 1. User drags/selects CSV file
 * 2. File sent to POST /api/v1/import/csv for parsing (preview only)
 * 3. Parsed holdings displayed in a review table
 * 4. User clicks "Confirm Import" to save via POST /api/v1/import/confirm
 * 5. Dashboard refreshes with new data
 */

const API_BASE = 'http://localhost:3002';

interface ParsedHolding {
  symbol: string;
  name: string;
  asset_class: string;
  quantity: number;
  market_value: number;
  cost_basis: number;
  yield_pct: number;
  weight_pct: number;
  currency: string;
}

interface ImportPreview {
  format_detected: string;
  rows_parsed: number;
  rows_skipped: number;
  holdings_count: number;
  holdings: ParsedHolding[];
  warnings: Array<{ row: number; field: string; message: string }>;
  errors: Array<{ row: number; message: string }>;
}

class PaeImport extends HTMLElement {
  private shadow: ShadowRoot;
  private file: File | null = null;
  private preview: ImportPreview | null = null;
  private portfolioId: string = '';
  private status: 'idle' | 'parsing' | 'previewing' | 'importing' | 'done' | 'error' = 'idle';
  private errorMsg: string = '';

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.loadPortfolioId();
    this.render();
  }

  private async loadPortfolioId(): Promise<void> {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/portfolios`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.portfolios?.length > 0) {
          this.portfolioId = data.portfolios[0].id;
        }
      }
    } catch {
      // Will be caught when user tries to import
    }
  }

  private handleDragOver(e: DragEvent): void {
    e.preventDefault();
    e.stopPropagation();
    const zone = this.shadow.querySelector('.drop-zone');
    zone?.classList.add('drag-over');
  }

  private handleDragLeave(e: DragEvent): void {
    e.preventDefault();
    const zone = this.shadow.querySelector('.drop-zone');
    zone?.classList.remove('drag-over');
  }

  private handleDrop(e: DragEvent): void {
    e.preventDefault();
    const zone = this.shadow.querySelector('.drop-zone');
    zone?.classList.remove('drag-over');
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      this.file = files[0];
      this.parseFile();
    }
  }

  private handleFileSelect(e: Event): void {
    const input = e.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.file = input.files[0];
      this.parseFile();
    }
  }

  private async parseFile(): Promise<void> {
    if (!this.file || !this.portfolioId) {
      this.errorMsg = 'No file selected or no portfolio available';
      this.status = 'error';
      this.render();
      return;
    }

    this.status = 'parsing';
    this.render();

    const formData = new FormData();
    formData.append('file', this.file);

    try {
      const resp = await fetch(
        `${API_BASE}/api/v1/import/csv?portfolio_id=${this.portfolioId}`,
        { method: 'POST', body: formData }
      );

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || `Server error: ${resp.status}`);
      }

      this.preview = await resp.json();
      this.status = 'previewing';
    } catch (e) {
      this.errorMsg = e instanceof Error ? e.message : 'Parse failed';
      this.status = 'error';
    }

    this.render();
  }

  private async confirmImport(): Promise<void> {
    if (!this.file || !this.portfolioId) return;

    this.status = 'importing';
    this.render();

    const formData = new FormData();
    formData.append('file', this.file);

    try {
      const resp = await fetch(
        `${API_BASE}/api/v1/import/confirm?portfolio_id=${this.portfolioId}`,
        { method: 'POST', body: formData }
      );

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail?.message || `Import failed: ${resp.status}`);
      }

      this.status = 'done';
    } catch (e) {
      this.errorMsg = e instanceof Error ? e.message : 'Import failed';
      this.status = 'error';
    }

    this.render();
  }

  private reset(): void {
    this.file = null;
    this.preview = null;
    this.status = 'idle';
    this.errorMsg = '';
    this.render();
  }

  private fmt(n: number): string {
    return n.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  private render(): void {
    let content = '';

    if (this.status === 'idle') {
      content = `
        <div class="drop-zone" id="dropzone">
          <div class="drop-icon">+</div>
          <div class="drop-text">Drag and drop your CSV file here</div>
          <div class="drop-sub">or click to browse</div>
          <div class="drop-formats">Supports: IBKR, Questrade, Wealthsimple, generic CSV</div>
          <input type="file" id="file-input" accept=".csv,.tsv,.txt" style="display:none">
        </div>
      `;
    } else if (this.status === 'parsing') {
      content = `<div class="status-msg">Parsing ${this.file?.name}...</div>`;
    } else if (this.status === 'previewing' && this.preview) {
      const p = this.preview;
      const rows = p.holdings.map(h => `
        <tr>
          <td><strong>${h.symbol}</strong></td>
          <td>${h.name || '-'}</td>
          <td>${h.asset_class}</td>
          <td class="numeric">${this.fmt(h.quantity)}</td>
          <td class="numeric">$${this.fmt(h.market_value)}</td>
          <td class="numeric">$${this.fmt(h.cost_basis)}</td>
          <td class="numeric">${this.fmt(h.weight_pct)}%</td>
        </tr>
      `).join('');

      const warnings = p.warnings.length > 0
        ? `<div class="warnings">${p.warnings.length} warning(s): ${p.warnings.map(w => `Row ${w.row}: ${w.message}`).join('; ')}</div>`
        : '';

      const errors = p.errors.length > 0
        ? `<div class="errors">${p.errors.length} error(s): ${p.errors.map(e => `Row ${e.row}: ${e.message}`).join('; ')}</div>`
        : '';

      content = `
        <div class="preview-header">
          <h3>Import Preview: ${this.file?.name}</h3>
          <div class="preview-meta">
            Format: <strong>${p.format_detected}</strong> |
            Parsed: <strong>${p.rows_parsed}</strong> rows |
            Holdings: <strong>${p.holdings_count}</strong> |
            Skipped: <strong>${p.rows_skipped}</strong>
          </div>
          ${warnings}${errors}
        </div>
        <table class="pae-table">
          <thead><tr><th>Symbol</th><th>Name</th><th>Class</th><th>Qty</th><th>Value</th><th>Cost</th><th>Weight</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="preview-actions">
          <button class="btn-primary" id="confirm-btn">Confirm Import (${p.holdings_count} holdings)</button>
          <button class="btn-secondary" id="cancel-btn">Cancel</button>
        </div>
      `;
    } else if (this.status === 'importing') {
      content = `<div class="status-msg">Importing ${this.preview?.holdings_count} holdings...</div>`;
    } else if (this.status === 'done') {
      content = `
        <div class="status-done">
          <div class="done-icon">OK</div>
          <div>Successfully imported ${this.preview?.holdings_count} holdings</div>
          <button class="btn-secondary" id="reset-btn">Import Another</button>
        </div>
      `;
    } else if (this.status === 'error') {
      content = `
        <div class="status-error">
          <div>${this.errorMsg}</div>
          <button class="btn-secondary" id="reset-btn">Try Again</button>
        </div>
      `;
    }

    this.shadow.innerHTML = `
      <link rel="stylesheet" href="styles/tokens.css">
      <link rel="stylesheet" href="styles/components.css">
      <link rel="stylesheet" href="styles/themes.css">
      <style>
        :host { display: block; }
        .drop-zone {
          border: 2px dashed var(--border-secondary);
          border-radius: var(--radius-lg);
          padding: var(--space-12) var(--space-8);
          text-align: center;
          cursor: pointer;
          transition: all var(--transition-normal);
          background: var(--bg-secondary);
        }
        .drop-zone:hover, .drop-zone.drag-over {
          border-color: var(--accent-primary);
          background: var(--bg-hover);
        }
        .drop-icon { font-size: 2rem; color: var(--text-tertiary); margin-bottom: var(--space-2); }
        .drop-text { font-size: var(--font-size-lg); color: var(--text-primary); font-weight: 600; }
        .drop-sub { font-size: var(--font-size-sm); color: var(--text-tertiary); margin-top: var(--space-1); }
        .drop-formats { font-size: var(--font-size-xs); color: var(--text-tertiary); margin-top: var(--space-3); }
        .status-msg { text-align: center; padding: var(--space-8); color: var(--text-secondary); }
        .status-done { text-align: center; padding: var(--space-8); color: var(--color-positive); }
        .status-error { text-align: center; padding: var(--space-8); color: var(--color-negative); }
        .done-icon { font-size: 1.5rem; font-weight: 700; margin-bottom: var(--space-2); }
        .preview-header { margin-bottom: var(--space-4); }
        .preview-header h3 { margin: 0 0 var(--space-2); font-size: var(--font-size-lg); }
        .preview-meta { font-size: var(--font-size-sm); color: var(--text-secondary); margin-bottom: var(--space-2); }
        .warnings { font-size: var(--font-size-xs); color: var(--color-warning); margin-top: var(--space-2); }
        .errors { font-size: var(--font-size-xs); color: var(--color-negative); margin-top: var(--space-2); }
        .preview-actions { margin-top: var(--space-4); display: flex; gap: var(--space-3); justify-content: center; }
        .btn-primary {
          background: var(--accent-primary); color: white; border: none;
          padding: var(--space-2) var(--space-6); border-radius: var(--radius-sm);
          font-size: var(--font-size-sm); cursor: pointer; font-weight: 600;
        }
        .btn-primary:hover { background: var(--accent-hover); }
        .btn-secondary {
          background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border-primary);
          padding: var(--space-2) var(--space-6); border-radius: var(--radius-sm);
          font-size: var(--font-size-sm); cursor: pointer;
        }
        .btn-secondary:hover { background: var(--bg-hover); }
        .numeric { text-align: right; font-variant-numeric: tabular-nums; }
      </style>
      <div>${content}</div>
    `;

    // Attach event listeners
    const dropzone = this.shadow.getElementById('dropzone');
    if (dropzone) {
      dropzone.addEventListener('dragover', (e) => this.handleDragOver(e as DragEvent));
      dropzone.addEventListener('dragleave', (e) => this.handleDragLeave(e as DragEvent));
      dropzone.addEventListener('drop', (e) => this.handleDrop(e as DragEvent));
      dropzone.addEventListener('click', () => {
        this.shadow.getElementById('file-input')?.click();
      });
    }

    const fileInput = this.shadow.getElementById('file-input');
    if (fileInput) {
      fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
    }

    const confirmBtn = this.shadow.getElementById('confirm-btn');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', () => this.confirmImport());
    }

    const cancelBtn = this.shadow.getElementById('cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => this.reset());
    }

    const resetBtn = this.shadow.getElementById('reset-btn');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => this.reset());
    }
  }
}

customElements.define('pae-import', PaeImport);
