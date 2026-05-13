/**
 * PAE Disclaimer Component.
 * First-class UI component. Rendered on every page.
 * Cannot be permanently dismissed (architectural guardrail).
 *
 * This component is a regulatory and design requirement:
 * PAE is an educational analytics tool, not a financial advisor.
 * The disclaimer must always be visible to reinforce this.
 *
 * @element pae-disclaimer
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
        .disclaimer a {
          color: var(--accent-primary, #2563eb);
          text-decoration: none;
        }
        .disclaimer a:hover {
          text-decoration: underline;
        }
        .disclaimer a:focus-visible {
          outline: 2px solid var(--accent-primary, #2563eb);
          outline-offset: 2px;
        }
      </style>
      <div class="disclaimer" role="contentinfo" aria-label="Legal disclaimer">
        <strong>PAE is an educational analytics tool, not a financial advisor.</strong>
        This tool provides data, calculations, and educational content only.
        The user is responsible for all investment decisions.
        This is not financial advice. Results vary with each use and over time.
        <a href="#methodology">View methodology</a>
      </div>
    `;
  }
}

customElements.define('pae-disclaimer', PaeDisclaimer);
