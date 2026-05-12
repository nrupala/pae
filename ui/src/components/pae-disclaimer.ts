/**
 * PAE Disclaimer Component.
 * First-class UI component. Rendered on every page.
 * Cannot be permanently dismissed (architectural guardrail).
 */

class PaeDisclaimer extends HTMLElement {
  private shadow: ShadowRoot;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
  }

  private render(): void {
    this.shadow.innerHTML = `
      <style>
        :host {
          display: block;
          position: fixed;
          bottom: 0;
          left: 0;
          right: 0;
          z-index: 200;
        }
        .disclaimer {
          background: var(--bg-tertiary, #f1f5f9);
          border-top: 1px solid var(--border-secondary, #cbd5e1);
          padding: 6px 24px;
          font-size: 11px;
          color: var(--text-tertiary, #94a3b8);
          text-align: center;
          font-family: ui-sans-serif, system-ui, sans-serif;
          line-height: 1.4;
        }
        .disclaimer strong {
          color: var(--text-secondary, #475569);
        }
      </style>
      <div class="disclaimer">
        <strong>PAE is an educational analytics tool, not a financial advisor.</strong>
        This tool provides data, calculations, and educational content only.
        The user is responsible for all investment decisions.
        This is not financial advice. Results vary with each use and over time.
        <a href="#methodology" style="color:var(--accent-primary, #2563eb)">View methodology</a>
      </div>
    `;
  }
}

customElements.define('pae-disclaimer', PaeDisclaimer);
