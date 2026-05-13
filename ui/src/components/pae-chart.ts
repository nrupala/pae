/**
 * PAE Chart Component.
 * Renders charts using HTML5 Canvas. Zero dependencies.
 * Supports: line, pie/donut.
 *
 * @element pae-chart
 * @attr {string} type - Chart type: 'line' | 'bar' | 'pie'.
 * @attr {string} width - Canvas width in pixels (default: 400).
 * @attr {string} height - Canvas height in pixels (default: 300).
 */

type ChartType = 'line' | 'bar' | 'pie';

interface ChartDataset {
  label: string;
  data: number[];
  color: string;
}

interface ChartData {
  labels: string[];
  datasets: ChartDataset[];
}

interface PieSlice {
  label: string;
  value: number;
  color: string;
}

class PaeChart extends HTMLElement {
  private shadow: ShadowRoot;
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;

  static get observedAttributes(): string[] {
    return ['type', 'width', 'height'];
  }

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback(): void {
    this.render();
  }

  attributeChangedCallback(): void {
    this.render();
  }

  /**
   * Parse a numeric attribute with a default fallback.
   * Clamps to [minVal, maxVal] and rejects non-finite values.
   */
  private getNumericAttr(name: string, defaultVal: number, minVal: number, maxVal: number): number {
    const raw = this.getAttribute(name);
    if (raw === null) return defaultVal;
    const parsed = parseInt(raw, 10);
    if (!Number.isFinite(parsed)) return defaultVal;
    return Math.max(minVal, Math.min(maxVal, parsed));
  }

  private render(): void {
    const width = this.getNumericAttr('width', 400, 50, 4000);
    const height = this.getNumericAttr('height', 300, 50, 4000);

    this.shadow.innerHTML = `
      <style>
        :host { display: block; }
        canvas {
          width: 100%;
          height: auto;
          max-width: ${width}px;
        }
      </style>
      <canvas width="${width}" height="${height}" role="img" aria-label="Chart"></canvas>
    `;

    this.canvas = this.shadow.querySelector('canvas');
    this.ctx = this.canvas?.getContext('2d') ?? null;
  }

  /**
   * Draw a line chart.
   *
   * @param data - Chart data with labels and datasets.
   *   Each dataset.data array should be the same length as labels.
   *   Non-finite values are skipped (gaps in the line).
   */
  public drawLine(data: ChartData): void {
    if (!this.ctx || !this.canvas) return;
    if (!data.datasets.length) return;

    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const padding = { top: 20, right: 20, bottom: 40, left: 60 };

    ctx.clearRect(0, 0, w, h);

    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    // Collect all finite values for scale computation
    const allValues = data.datasets
      .flatMap(d => d.data)
      .filter(v => Number.isFinite(v));

    if (allValues.length === 0) return;

    const minVal = Math.min(...allValues);
    const maxVal = Math.max(...allValues);
    const range = maxVal - minVal || 1; // avoid division by zero

    // Draw grid lines
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(w - padding.right, y);
      ctx.stroke();
    }

    // Draw each dataset
    for (const dataset of data.datasets) {
      if (!dataset.data.length) continue;

      ctx.strokeStyle = dataset.color;
      ctx.lineWidth = 2;
      ctx.beginPath();

      const maxIdx = Math.max(dataset.data.length - 1, 1);
      let pathStarted = false;

      for (let i = 0; i < dataset.data.length; i++) {
        const val = dataset.data[i];
        if (!Number.isFinite(val)) continue; // skip NaN/Infinity gaps

        const x = padding.left + (i / maxIdx) * chartW;
        const y = padding.top + chartH - ((val - minVal) / range) * chartH;

        if (!pathStarted) {
          ctx.moveTo(x, y);
          pathStarted = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }
  }

  /**
   * Draw a donut/pie chart.
   *
   * @param data - Array of pie slices with label, value, and color.
   *   Slices with non-positive or non-finite values are skipped.
   *   If all slices are zero, nothing is drawn.
   */
  public drawPie(data: PieSlice[]): void {
    if (!this.ctx || !this.canvas) return;

    // Filter out invalid slices
    const validSlices = data.filter(
      d => Number.isFinite(d.value) && d.value > 0
    );
    if (validSlices.length === 0) return;

    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 20;
    const innerRadius = radius * 0.6;

    ctx.clearRect(0, 0, w, h);

    const total = validSlices.reduce((sum, d) => sum + d.value, 0);
    if (total === 0) return; // avoid division by zero

    let startAngle = -Math.PI / 2;

    for (const slice of validSlices) {
      const sliceAngle = (slice.value / total) * Math.PI * 2;
      const endAngle = startAngle + sliceAngle;

      ctx.beginPath();
      ctx.arc(cx, cy, radius, startAngle, endAngle);
      ctx.arc(cx, cy, innerRadius, endAngle, startAngle, true);
      ctx.closePath();
      ctx.fillStyle = slice.color;
      ctx.fill();

      startAngle = endAngle;
    }
  }
}

customElements.define('pae-chart', PaeChart);

export { PaeChart, ChartData, ChartType, PieSlice, ChartDataset };
