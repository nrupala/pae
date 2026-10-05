/**
 * PAE Chart Component — SVG renderer, zero dependencies.
 *
 * Renders line, bar, pie/donut, area, and fan (percentile band) charts as
 * inline SVG: crisp on retina, styleable via CSS, and serializable so
 * rendered output can be captured for docs and the product site.
 *
 * @element pae-chart
 * @attr {string} type - 'line' | 'bar' | 'pie' | 'area' | 'fan'
 * @attr {string} title - accessible chart title (rendered as <title>)
 *
 * Data is supplied via methods:
 *   setSeries({labels, datasets, yLabel})   for line | bar | area
 *   setPie(slices)                          for pie (donut)
 *   setFan({labels, p5, p25, p50, p75, p95}) for fan
 * getSVG() returns the current SVG markup string.
 */

type ChartType = 'line' | 'bar' | 'pie' | 'area' | 'fan';

interface ChartDataset {
  label: string;
  data: number[];
  color: string;
}

interface ChartData {
  labels: string[];
  datasets: ChartDataset[];
  yLabel?: string;
}

interface PieSlice {
  label: string;
  value: number;
  color: string;
}

interface FanData {
  labels: string[];
  p5: number[];
  p25: number[];
  p50: number[];
  p75: number[];
  p95: number[];
  color: string;
  yLabel?: string;
}

interface ScatterPoint {
  x: number;
  y: number;
}

interface ScatterMarker {
  x: number;
  y: number;
  label: string;
  color: string;
}

interface ScatterData {
  points: ScatterPoint[];
  markers: ScatterMarker[];
  xLabel?: string;
  yLabel?: string;
  lineColor: string;
}

const PALETTE = [
  '#38bdf8', '#a78bfa', '#f472b6', '#fbbf24', '#34d399',
  '#fb7185', '#60a5fa', '#f97316', '#2dd4bf', '#e879f9',
];

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtTick(v: number): string {
  if (!Number.isFinite(v)) return '–';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (abs >= 1_000) return (v / 1_000).toFixed(1) + 'K';
  if (abs >= 100) return v.toFixed(0);
  if (abs >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

class PaeChart extends HTMLElement {
  private shadow: ShadowRoot;
  private svgMarkup: string = '';

  static get observedAttributes(): string[] {
    return ['type', 'title'];
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

  /** Current SVG markup (empty string until data is set). */
  public getSVG(): string {
    return this.svgMarkup;
  }

  private get type(): ChartType {
    const t = this.getAttribute('type');
    return t === 'bar' || t === 'pie' || t === 'area' || t === 'fan' ? t : 'line';
  }

  private get chartTitle(): string {
    return this.getAttribute('title') || 'Chart';
  }

  private render(): void {
    this.shadow.innerHTML = `
      <style>
        :host { display: block; }
        .pae-chart-wrap { width: 100%; }
        .pae-chart-wrap svg { width: 100%; height: auto; display: block; }
        .pae-chart-legend { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 8px;
          font-size: 12px; color: var(--text-secondary, #94a3b8); }
        .pae-chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
        .pae-chart-legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
      </style>
      <div class="pae-chart-wrap" role="img" aria-label="${esc(this.chartTitle)}">
        ${this.svgMarkup}
        ${this.legendMarkup}
      </div>
    `;
  }

  private legendMarkup: string = '';

  private finish(svg: string, legend: string): void {
    this.svgMarkup = svg;
    this.legendMarkup = legend;
    this.render();
  }

  private axesFrame(
    w: number, h: number,
    pad: { top: number; right: number; bottom: number; left: number },
    minVal: number, maxVal: number,
    yLabel?: string,
  ): { inner: string; x: (i: number, n: number) => number; y: (v: number) => number } {
    const range = maxVal - minVal || 1;
    const y = (v: number) =>
      pad.top + (h - pad.top - pad.bottom) * (1 - (v - minVal) / range);
    const x = (i: number, n: number) =>
      pad.left + (i / Math.max(n - 1, 1)) * (w - pad.left - pad.right);

    let inner = '';
    for (let i = 0; i <= 4; i++) {
      const v = minVal + (range / 4) * i;
      const yy = y(v);
      inner += `<line x1="${pad.left}" y1="${yy.toFixed(1)}" x2="${w - pad.right}" y2="${yy.toFixed(1)}" stroke="rgba(148,163,184,0.25)" stroke-width="1"/>`;
      inner += `<text x="${pad.left - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#94a3b8">${fmtTick(v)}</text>`;
    }
    if (yLabel) {
      inner += `<text x="12" y="${pad.top - 6}" font-size="10" fill="#94a3b8">${esc(yLabel)}</text>`;
    }
    return { inner, x, y };
  }

  private legendFor(items: Array<{ label: string; color: string }>): string {
    if (!items.length) return '';
    return `<div class="pae-chart-legend">${items
      .map(i => `<span><i style="background:${i.color}"></i>${esc(i.label)}</span>`)
      .join('')}</div>`;
  }

  /** Line / bar / area chart from labelled datasets. */
  public setSeries(data: ChartData): void {
    const type = this.type === 'pie' || this.type === 'fan' ? 'line' : this.type;
    const w = 640, h = 360;
    const pad = { top: 28, right: 16, bottom: 44, left: 64 };

    const allValues = data.datasets.flatMap(d => d.data).filter(Number.isFinite);
    if (!allValues.length || !data.datasets.length) {
      this.finish(
        `<svg viewBox="0 0 ${w} ${h}"><title>${esc(this.chartTitle)}</title>` +
        `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="13" fill="#94a3b8">No data</text></svg>`,
        '',
      );
      return;
    }

    let minVal = Math.min(...allValues);
    let maxVal = Math.max(...allValues);
    if (type === 'bar' && minVal > 0) minVal = 0;
    if (type === 'area' && minVal > 0) minVal = 0;

    const { inner, x, y } = this.axesFrame(w, h, pad, minVal, maxVal, data.yLabel);
    let body = inner;
    const n = data.labels.length;

    // Zero line when the range straddles it
    if (minVal < 0 && maxVal > 0) {
      body += `<line x1="${pad.left}" y1="${y(0).toFixed(1)}" x2="${w - pad.right}" y2="${y(0).toFixed(1)}" stroke="#64748b" stroke-width="1" stroke-dasharray="4 3"/>`;
    }

    data.datasets.forEach((ds) => {
      const pts = ds.data
        .map((v, i) => ({ v, i }))
        .filter(p => Number.isFinite(p.v));
      if (!pts.length) return;

      if (type === 'bar') {
        const slotW = (w - pad.left - pad.right) / Math.max(n, 1);
        const bw = Math.min(slotW * 0.6 / data.datasets.length, 40);
        const di = data.datasets.indexOf(ds);
        for (const p of pts) {
          const cx = x(p.i, n) - (data.datasets.length * bw) / 2 + di * bw + bw / 2;
          const y0 = y(Math.max(0, Math.min(p.v, maxVal)));
          const y1 = y(Math.min(0, Math.max(p.v, minVal)));
          const top = Math.min(y0, y1);
          const bh = Math.max(Math.abs(y1 - y0), 1.5);
          body += `<rect x="${(cx - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${ds.color}"><title>${esc(ds.label)}: ${fmtTick(p.v)}</title></rect>`;
        }
      } else {
        const path = pts.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i, n).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
        if (type === 'area') {
          const base = y(0) > h - pad.bottom ? h - pad.bottom : y(Math.max(minVal, 0));
          const areaPath = `${path} L${x(pts[pts.length - 1].i, n).toFixed(1)},${base.toFixed(1)} L${x(pts[0].i, n).toFixed(1)},${base.toFixed(1)} Z`;
          body += `<path d="${areaPath}" fill="${ds.color}" opacity="0.25"/>`;
        }
        body += `<path d="${path}" fill="none" stroke="${ds.color}" stroke-width="2"><title>${esc(ds.label)}</title></path>`;
      }
    });

    // X labels (sparse)
    const step = Math.max(1, Math.ceil(n / 8));
    for (let i = 0; i < n; i += step) {
      body += `<text x="${x(i, n).toFixed(1)}" y="${h - 14}" text-anchor="middle" font-size="10" fill="#94a3b8">${esc(String(data.labels[i]))}</text>`;
    }

    this.finish(
      `<svg viewBox="0 0 ${w} ${h}" role="img"><title>${esc(this.chartTitle)}</title>${body}</svg>`,
      this.legendFor(data.datasets.map(d => ({ label: d.label, color: d.color }))),
    );
  }

  /** Donut chart. */
  public setPie(slices: PieSlice[]): void {
    const w = 640, h = 360;
    const valid = slices.filter(s => Number.isFinite(s.value) && s.value > 0);
    if (!valid.length) {
      this.finish(
        `<svg viewBox="0 0 ${w} ${h}"><title>${esc(this.chartTitle)}</title>` +
        `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="13" fill="#94a3b8">No data</text></svg>`,
        '',
      );
      return;
    }
    const cx = w / 2, cy = h / 2;
    const radius = Math.min(w, h) / 2 - 30;
    const innerR = radius * 0.58;
    const total = valid.reduce((s, d) => s + d.value, 0);
    let angle = -Math.PI / 2;
    let body = '';
    for (const s of valid) {
      const a2 = angle + (s.value / total) * Math.PI * 2;
      const large = a2 - angle > Math.PI ? 1 : 0;
      const x1 = cx + radius * Math.cos(angle), y1 = cy + radius * Math.sin(angle);
      const x2 = cx + radius * Math.cos(a2), y2 = cy + radius * Math.sin(a2);
      const x3 = cx + innerR * Math.cos(a2), y3 = cy + innerR * Math.sin(a2);
      const x4 = cx + innerR * Math.cos(angle), y4 = cy + innerR * Math.sin(angle);
      body += `<path d="M${x1.toFixed(1)},${y1.toFixed(1)} A${radius},${radius} 0 ${large} 1 ${x2.toFixed(1)},${y2.toFixed(1)} L${x3.toFixed(1)},${y3.toFixed(1)} A${innerR},${innerR} 0 ${large} 0 ${x4.toFixed(1)},${y4.toFixed(1)} Z" fill="${s.color}"><title>${esc(s.label)}: ${(s.value / total * 100).toFixed(1)}%</title></path>`;
      angle = a2;
    }
    this.finish(
      `<svg viewBox="0 0 ${w} ${h}" role="img"><title>${esc(this.chartTitle)}</title>${body}</svg>`,
      this.legendFor(valid.map(s => ({ label: `${s.label} (${(s.value / total * 100).toFixed(1)}%)`, color: s.color }))),
    );
  }

  /** Monte Carlo percentile fan: shaded p5–p95 / p25–p75 bands + p50 line. */
  public setFan(data: FanData): void {
    const w = 640, h = 360;
    const pad = { top: 28, right: 16, bottom: 44, left: 64 };
    const all = [...data.p5, ...data.p25, ...data.p50, ...data.p75, ...data.p95].filter(Number.isFinite);
    if (!all.length) {
      this.finish(
        `<svg viewBox="0 0 ${w} ${h}"><title>${esc(this.chartTitle)}</title>` +
        `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="13" fill="#94a3b8">No data</text></svg>`,
        '',
      );
      return;
    }
    const minVal = Math.min(...all), maxVal = Math.max(...all);
    const { inner, x, y } = this.axesFrame(w, h, pad, minVal, maxVal, data.yLabel);
    const n = data.labels.length;
    const band = (lo: number[], hi: number[]): string => {
      const fwd = lo.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i, n).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
      const back = hi.map((_v, i) => `L${x(hi.length - 1 - i, n).toFixed(1)},${y(hi[hi.length - 1 - i]).toFixed(1)}`).join(' ');
      return `${fwd} ${back} Z`;
    };
    let body = inner;
    body += `<path d="${band(data.p5, data.p95)}" fill="${data.color}" opacity="0.18"/>`;
    body += `<path d="${band(data.p25, data.p75)}" fill="${data.color}" opacity="0.28"/>`;
    body += `<path d="${data.p50.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i, n).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${data.color}" stroke-width="2.5"/>`;
    const step = Math.max(1, Math.ceil(n / 6));
    for (let i = 0; i < n; i += step) {
      body += `<text x="${x(i, n).toFixed(1)}" y="${h - 14}" text-anchor="middle" font-size="10" fill="#94a3b8">${esc(String(data.labels[i]))}</text>`;
    }
    this.finish(
      `<svg viewBox="0 0 ${w} ${h}" role="img"><title>${esc(this.chartTitle)}</title>${body}</svg>`,
      this.legendFor([
        { label: 'Median (p50)', color: data.color },
        { label: 'p25–p75', color: data.color },
        { label: 'p5–p95', color: data.color },
      ]),
    );
  }
  /** Scatter plot with an (x, y) polyline plus labelled markers.

  Used for the efficient frontier: x = volatility, y = expected return,
  markers = the optimal mixes. */
  public setScatter(data: ScatterData): void {
    const w = 640, h = 360;
    const pad = { top: 28, right: 16, bottom: 44, left: 64 };
    const xs = [...data.points.map(p => p.x), ...data.markers.map(m => m.x)]
      .filter(Number.isFinite);
    const ys = [...data.points.map(p => p.y), ...data.markers.map(m => m.y)]
      .filter(Number.isFinite);
    if (!xs.length || !ys.length) {
      this.finish(
        `<svg viewBox="0 0 ${w} ${h}"><title>${esc(this.chartTitle)}</title>` +
        `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="13" fill="#94a3b8">No data</text></svg>`,
        '',
      );
      return;
    }
    let minX = Math.min(...xs), maxX = Math.max(...xs);
    let minY = Math.min(...ys), maxY = Math.max(...ys);
    if (minX === maxX) { minX -= 1; maxX += 1; }
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const px = (v: number): number =>
      pad.left + ((v - minX) / (maxX - minX)) * (w - pad.left - pad.right);
    const py = (v: number): number =>
      pad.top + (h - pad.top - pad.bottom) * (1 - (v - minY) / (maxY - minY));

    let body = '';
    for (let i = 0; i <= 4; i++) {
      const vx = minX + ((maxX - minX) / 4) * i;
      const vy = minY + ((maxY - minY) / 4) * i;
      const xx = px(vx), yy = py(vy);
      body += `<line x1="${xx.toFixed(1)}" y1="${pad.top}" x2="${xx.toFixed(1)}" y2="${h - pad.bottom}" stroke="rgba(148,163,184,0.25)" stroke-width="1"/>`;
      body += `<line x1="${pad.left}" y1="${yy.toFixed(1)}" x2="${w - pad.right}" y2="${yy.toFixed(1)}" stroke="rgba(148,163,184,0.25)" stroke-width="1"/>`;
      body += `<text x="${xx.toFixed(1)}" y="${h - 26}" text-anchor="middle" font-size="10" fill="#94a3b8">${fmtTick(vx)}</text>`;
      body += `<text x="${pad.left - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#94a3b8">${fmtTick(vy)}</text>`;
    }
    if (data.xLabel) {
      body += `<text x="${w - pad.right}" y="${h - 8}" text-anchor="end" font-size="10" fill="#94a3b8">${esc(data.xLabel)}</text>`;
    }
    if (data.yLabel) {
      body += `<text x="12" y="${pad.top - 6}" font-size="10" fill="#94a3b8">${esc(data.yLabel)}</text>`;
    }

    const ordered = [...data.points].sort((a, b) => a.x - b.x);
    const path = ordered
      .map((p, k) => `${k === 0 ? 'M' : 'L'}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`)
      .join(' ');
    body += `<path d="${path}" fill="none" stroke="${data.lineColor}" stroke-width="2"/>`;

    for (const m of data.markers) {
      if (!Number.isFinite(m.x) || !Number.isFinite(m.y)) continue;
      body += `<circle cx="${px(m.x).toFixed(1)}" cy="${py(m.y).toFixed(1)}" r="6" fill="${m.color}" stroke="#0f172a" stroke-width="1.5"><title>${esc(m.label)}</title></circle>`;
    }

    this.finish(
      `<svg viewBox="0 0 ${w} ${h}" role="img"><title>${esc(this.chartTitle)}</title>${body}</svg>`,
      this.legendFor(data.markers.map(m => ({ label: m.label, color: m.color }))),
    );
  }
}

customElements.define('pae-chart', PaeChart);

export { PaeChart, ChartData, ChartDataset, ChartType, PieSlice, FanData, PALETTE, ScatterData, ScatterMarker, ScatterPoint };
