import * as d3 from 'd3';
import { children } from '../storage/database';
import { LIMITS, type XmlNode } from '../models/types';
import { button, download, el } from '../components/dom';
interface GraphNode {
  node: XmlNode;
  children?: GraphNode[];
  omitted?: number;
}
export class GraphView {
  readonly element = el('div', 'graph-panel');
  private host = el('div', 'graph-canvas');
  private horizontal = true;
  private root?: XmlNode;
  private documentRoot?: XmlNode;
  private collapsed = new Set<number>();
  private svg?: SVGSVGElement;
  constructor(
    private selectNode: (n: XmlNode) => void,
    private report: (e: unknown) => void,
  ) {
    const bar = el('div', 'toolbar');
    bar.append(
      button('縦 / 横', () => {
        this.horizontal = !this.horizontal;
        return this.render().catch(report);
      }),
      button('全体を表示', () => {
        this.root = this.documentRoot;
        this.collapsed.clear();
        return this.render().catch(report);
      }),
      button('SVG保存', () => this.saveSvg()),
      button('PNG保存', () => this.savePng().catch(report)),
    );
    this.element.append(bar, this.host);
  }
  async load(root: XmlNode) {
    this.documentRoot = root;
    await this.focus(root);
  }
  async focus(root: XmlNode) {
    this.root = root;
    this.collapsed.clear();
    await this.render();
  }
  private async render() {
    if (!this.root) return;
    let count = 1;
    const root: GraphNode = { node: this.root };
    const queue = [root];
    for (let i = 0; i < queue.length; i++) {
      const current = queue[i];
      if (
        count >= LIMITS.graphNodes ||
        this.collapsed.has(current.node.nodeId) ||
        current.node.depth - this.root.depth >= 4
      ) {
        current.omitted = current.node.childCount;
        continue;
      }
      const remaining = LIMITS.graphNodes - count;
      const kids = (await children(current.node.fileId, current.node.nodeId, 0, remaining)).filter(
        (n) => n.nodeType === 'element',
      );
      current.children = kids.map((node) => ({ node }));
      current.omitted = Math.max(0, current.node.childCount - kids.length);
      queue.push(...current.children);
      count += kids.length;
    }
    const hierarchy = d3.hierarchy(root);
    const layout = d3.tree<GraphNode>().nodeSize(this.horizontal ? [38, 210] : [180, 86])(
      hierarchy,
    );
    const svg = d3
      .create('svg')
      .attr('xmlns', 'http://www.w3.org/2000/svg')
      .attr('width', 1200)
      .attr('height', 680)
      .attr('viewBox', '0 0 1200 680')
      .attr('role', 'img')
      .attr('aria-label', 'XMLノードリンク図');
    this.svg = svg.node()!;
    this.host.replaceChildren(this.svg);
    const group = svg.append('g');
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.02, 4])
      .on('zoom', (e) => group.attr('transform', e.transform.toString()));
    svg.call(zoom as any);
    const coords = (n: d3.HierarchyPointNode<GraphNode>) =>
      this.horizontal ? [n.y, n.x] : [n.x, n.y];
    const points = layout.descendants().map(coords);
    const minX = Math.min(...points.map((p) => p[0])),
      maxX = Math.max(...points.map((p) => p[0])) + 180,
      minY = Math.min(...points.map((p) => p[1])),
      maxY = Math.max(...points.map((p) => p[1])) + 30;
    const scale = Math.min(1, 1100 / (maxX - minX || 1), 580 / (maxY - minY || 1));
    svg.call(
      zoom.transform as any,
      d3.zoomIdentity
        .translate(
          (1200 - (maxX - minX) * scale) / 2 - minX * scale,
          (680 - (maxY - minY) * scale) / 2 - minY * scale,
        )
        .scale(scale),
    );
    group
      .selectAll('path')
      .data(layout.links())
      .join('path')
      .attr('d', (d) => {
        const [x1, y1] = coords(d.source),
          [x2, y2] = coords(d.target);
        return `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      })
      .attr('fill', 'none')
      .attr('stroke', '#8da7b7')
      .attr('stroke-width', 1.5);
    const nodes = group
      .selectAll('g')
      .data(layout.descendants())
      .join('g')
      .attr('transform', (n) => `translate(${coords(n)})`)
      .attr('tabindex', 0)
      .attr('role', 'button')
      .attr('aria-label', (n) => n.data.node.elementName)
      .on('click', (_e, n) => this.selectNode(n.data.node))
      .on('keydown', (e, n) => {
        if (e.key === 'Enter') this.selectNode(n.data.node);
      })
      .on('dblclick', (_e, n) => {
        const id = n.data.node.nodeId;
        this.collapsed.has(id) ? this.collapsed.delete(id) : this.collapsed.add(id);
        void this.render().catch(this.report);
      });
    nodes
      .append('circle')
      .attr('r', 6)
      .attr('fill', '#128477')
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 2);
    nodes
      .append('text')
      .attr('x', 12)
      .attr('y', 4)
      .attr('font-size', 12)
      .attr('font-family', 'Yu Gothic, sans-serif')
      .attr('fill', '#476474')
      .text((n) => n.data.node.elementName.slice(0, 26));
    nodes
      .append('text')
      .attr('x', 12)
      .attr('y', 20)
      .attr('font-size', 10)
      .attr('fill', '#876b45')
      .text((n) =>
        n.data.omitted
          ? `+ ${n.data.omitted} 子ノードを省略`
          : this.collapsed.has(n.data.node.nodeId)
            ? '折りたたみ'
            : '',
      );
  }
  private saveSvg() {
    if (this.svg)
      download(new XMLSerializer().serializeToString(this.svg), 'xml-graph.svg', 'image/svg+xml');
  }
  private async savePng() {
    if (!this.svg) return;
    const url = URL.createObjectURL(
      new Blob([new XMLSerializer().serializeToString(this.svg)], { type: 'image/svg+xml' }),
    );
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      const canvas = el('canvas');
      canvas.width = 1200;
      canvas.height = 680;
      const context = canvas.getContext('2d')!;
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, 1200, 680);
      context.drawImage(img, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve));
      if (blob) download(blob, 'xml-graph.png', 'image/png');
    } finally {
      URL.revokeObjectURL(url);
    }
  }
}
