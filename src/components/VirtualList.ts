import { el } from './dom';
export class VirtualList<T> {
  readonly element = el('div', 'virtual-list');
  private spacer = el('div', 'virtual-spacer');
  private layer = el('div', 'virtual-layer');
  private items: T[] = [];
  private observer: ResizeObserver;
  constructor(
    private render: (item: T, index: number) => HTMLElement,
    private height = 36,
  ) {
    this.element.tabIndex = 0;
    this.element.append(this.spacer, this.layer);
    this.element.onscroll = () => this.paint();
    this.observer = new ResizeObserver(() => this.paint());
    this.observer.observe(this.element);
  }
  destroy() {
    this.observer.disconnect();
    this.element.onscroll = null;
  }
  set(items: T[]) {
    this.items = items;
    this.spacer.style.height = `${items.length * this.height}px`;
    this.paint();
  }
  jump(index: number) {
    this.element.scrollTop = Math.max(0, index * this.height);
    this.paint();
  }
  private paint() {
    const first = Math.max(0, Math.floor(this.element.scrollTop / this.height) - 5),
      count = Math.ceil((this.element.clientHeight || 500) / this.height) + 10;
    this.layer.style.transform = `translateY(${first * this.height}px)`;
    this.layer.replaceChildren(
      ...this.items.slice(first, first + count).map((item, i) => {
        const row = this.render(item, first + i);
        row.style.height = `${this.height}px`;
        return row;
      }),
    );
  }
}
