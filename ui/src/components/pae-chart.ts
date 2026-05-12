/**
 * PAE Chart Component.
 * Renders charts using HTML5 Canvas. Zero dependencies.
 * Supports: line, pie/donut.
 */

type ChartType = 'line' | 'bar' | 'pie';

interface ChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color: string;
  }[];
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

  private render(): void {
    const width = parseInt(this.getAttribute('width') || '400');
    const height = parseInt(this.getAttribute('height') || '300');

    this.shadow.innerHTML = `
      <style>
        :host { display: block; }
        canvas {
          width: 100%;
          height: auto;
          max-width: ${width}px;
        }
      </style>
      <canvas width="${width}" height="${height}"></canvas>
    `;

    this.canvas = this.shadow.querySelector('canvas');
    this.ctx = this.canvas?.getContext('2d') || null;
  }

  public drawLine(data: ChartData): void {
    if (!this.ctx || !this.canvas) return;

    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const padding = { top: 20, right: 20, bottom: 40, left: 60 };

    ctx.clearRect(0, 0, w, h);

    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    const allValues = data.datasets.flatMap(d => d.data);
    const minVal = Math.min(...allValues);
    const maxVal = Math.max(...allValues);
    const range = maxVal - minVal || 1;

    ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(w - padding.right, y);
      ctx.stroke();
    }

    for (const dataset of data.datasets) {
      ctx.strokeStyle = dataset.color;
      ctx.lineWidth = 2;
      ctx.beginPath();

      for (let i = 0; i < dataset.data.length; i++) {
        const x = padding.left + (i / Math.max(dataset.data.length - 1, 1)) * chartW;
        const y = padding.top + chartH - ((dataset.data[i] - minVal) / range) * chartH;

        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }
  }

  public drawPie(data: { label: string; value: number; color: string }[]): void {
    if (!this.ctx || !this.canvas) return;

    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 20;
    const innerRadius = radius * 0.6;

    ctx.clearRect(0, 0, w, h);

    const total = data.reduce((sum, d) => sum + d.value, 0);
    let startAngle = -Math.PI / 2;

    for (const slice of data) {
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

export { PaeChart, ChartData, ChartType };
