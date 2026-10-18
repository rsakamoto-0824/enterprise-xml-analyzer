import { db, node } from '../storage/database';
import { diffTypes } from '../diff/diffEngine';
import { WorkerClient } from '../workers/client';
import {
  type Comparison,
  type DiffOptions,
  type DiffRow,
  type XmlFile,
  type XmlNode,
} from '../models/types';
import { button, checkbox, download, el, field, select, input, table } from './dom';
import { VirtualList } from './VirtualList';
import { GraphView } from '../visualization/graphRenderer';
export const labels = {
  ADDED: '追加',
  REMOVED: '削除',
  MODIFIED: '変更',
  MOVED: '移動',
  UNCHANGED: '変更なし',
  AMBIGUOUS: '曖昧',
};
export class CompareView {
  readonly element = el('div', 'compare-view');
  private worker = new WorkerClient('diff');
  private exporter = new WorkerClient('export');
  private result?: Comparison;
  private filter = '';
  private offset = 0;
  private rowsHost = el('div');
  private summary = el('div', 'metric-grid');
  private details = el('div', 'diff-details');
  private list?: VirtualList<DiffRow>;
  constructor(
    private run: <T>(
      label: string,
      fn: () => Promise<T>,
      cancel?: () => void,
    ) => Promise<T | undefined>,
    private report: (e: unknown) => void,
    private jump: (fileId: string, id: number) => void,
  ) {}
  render(files: XmlFile[]) {
    if (
      this.result &&
      (!files.some((f) => f.fileId === this.result!.a) ||
        !files.some((f) => f.fileId === this.result!.b))
    ) {
      this.result = undefined;
      this.summary.replaceChildren();
      this.rowsHost.replaceChildren();
      this.details.replaceChildren();
      this.list?.destroy();
    }
    this.element.replaceChildren();
    const bar = el('div', 'form-grid'),
      items = files.map((f) => [f.fileId, f.name] as [string, string]);
    const a = select('比較XML A', items),
      b = select('比較XML B', items, files[1]?.fileId);
    const mode = select(
        '比較モード',
        [
          ['structure', '構造比較'],
          ['value', '値比較'],
          ['full', '完全比較'],
        ],
        'full',
      ),
      key = input('キー属性', 'id');
    bar.append(
      field('XML A', a),
      field('XML B', b),
      field('比較モード', mode),
      field('識別キー属性（同じ親の中で照合）', key),
    );
    const options: DiffOptions = {
      mode: 'full',
      keyAttribute: 'id',
      whitespace: 'trim',
      comments: false,
      processingInstructions: false,
      ignoreOrder: false,
      verifyHashes: false,
    };
    const checks = el('div', 'toolbar');
    checks.append(
      checkbox('前後の空白を無視', true, (v) => (options.whitespace = v ? 'trim' : 'preserve')),
      checkbox('コメントを比較', false, (v) => (options.comments = v)),
      checkbox('処理命令を比較', false, (v) => (options.processingInstructions = v)),
      checkbox('要素順序を無視', false, (v) => (options.ignoreOrder = v)),
      checkbox('ハッシュ一致でも厳密照合', false, (v) => (options.verifyHashes = v)),
    );
    const actions = el('div', 'toolbar');
    actions.append(
      button(
        '比較を開始',
        () =>
          this.run(
            'XML比較',
            async () => {
              if (!a.value || !b.value || a.value === b.value)
                throw new Error('COMPARE_PAIR: 異なる2つのXMLを選択してください。');
              options.mode = mode.value as DiffOptions['mode'];
              options.keyAttribute = key.value;
              this.result = await this.worker.call('compare', { a: a.value, b: b.value, options });
              this.offset = 0;
              await this.refresh();
            },
            () => this.worker.cancel(),
          ),
        'primary',
      ),
    );
    const filter = select(
      '差分種別',
      [['', 'すべて'], ...diffTypes.map((t) => [t, labels[t]] as [string, string])],
      this.filter,
    );
    filter.onchange = () => {
      this.filter = filter.value;
      this.offset = 0;
      void this.refresh().catch(this.report);
    };
    actions.append(
      filter,
      button('前の1000件', () => {
        this.offset = Math.max(0, this.offset - 1000);
        return this.refresh().catch(this.report);
      }),
      button('次の1000件', () => {
        this.offset += 1000;
        return this.refresh().catch(this.report);
      }),
      button('差分XLSX保存', () =>
        this.run(
          '差分Excel生成',
          async () => {
            if (!this.result) throw new Error('COMPARISON_MISSING: 先に比較してください。');
            const bytes = await this.exporter.call<Uint8Array>('diff', {
              id: this.result.comparisonId,
              filter: this.filter,
            });
            download(bytes as BlobPart, 'xml-differences.xlsx');
          },
          () => this.exporter.cancel(),
        ),
      ),
    );
    this.element.append(bar, checks, actions, this.summary, this.rowsHost, this.details);
    if (this.result) void this.refresh().catch(this.report);
  }
  private async refresh() {
    if (!this.result) return;
    const c = this.result;
    this.summary.replaceChildren();
    for (const type of diffTypes) {
      const card = el('div', 'metric');
      card.append(
        el('span', `badge ${type}`, labels[type]),
        el('strong', '', c.counts[type].toLocaleString()),
      );
      this.summary.append(card);
    }
    const database = await db();
    const tx = database.transaction('DiffResults');
    const source = this.filter ? tx.store.index('type') : tx.store;
    const range = this.filter
      ? IDBKeyRange.bound(
          [c.comparisonId, this.filter, 0],
          [c.comparisonId, this.filter, Number.MAX_SAFE_INTEGER],
        )
      : IDBKeyRange.bound([c.comparisonId, 0], [c.comparisonId, Number.MAX_SAFE_INTEGER]);
    const rows: DiffRow[] = [];
    let skip = this.offset;
    for await (const cursor of source.iterate(range)) {
      if (skip) {
        await cursor.advance(skip);
        skip = 0;
      } else {
        rows.push(cursor.value);
        if (rows.length === 1000) break;
      }
    }
    this.list?.destroy();
    const list = new VirtualList<DiffRow>((row) => {
      const n = el('div', 'diff-row');
      n.append(
        el('span', `badge ${row.type}`, labels[row.type]),
        button(
          row.a ? `A #${row.a} ${row.path}` : 'A — 存在しません',
          () => this.show(row),
          'diff-side',
        ),
        button(
          row.b ? `B #${row.b} ${row.path}` : 'B — 存在しません',
          () => this.show(row),
          'diff-side',
        ),
      );
      return n;
    }, 40);
    this.list = list;
    list.set(rows);
    this.rowsHost.replaceChildren(
      el(
        'p',
        'muted',
        `${this.offset + 1}件目から ${rows.length}件を表示。左右は共通の行でスクロールします。省略サブツリーの件数は文書ノード数です。`,
      ),
      list.element,
    );
  }
  private async show(row: DiffRow) {
    if (!this.result) return;
    const c = this.result;
    this.details.replaceChildren(
      el('h3', '', `${labels[row.type]} · ${row.name}`),
      el('p', '', row.details),
      table(
        ['項目', 'XML A', 'XML B'],
        [
          ['値（最大4000文字）', row.oldValue, row.newValue],
          ['対応の根拠', row.confidence, row.confidence],
        ],
      ),
    );
    const bar = el('div', 'toolbar');
    if (row.a) bar.append(button('Aの要素へ移動', () => this.jump(c.a, row.a!)));
    if (row.b) bar.append(button('Bの要素へ移動', () => this.jump(c.b, row.b!)));
    bar.append(
      button('左右のグラフを表示', async () => {
        try {
          const box = el('div', 'graph-comparison');
          for (const [fileId, id] of [
            [c.a, row.a],
            [c.b, row.b],
          ] as const) {
            const n = id ? await node(fileId, id) : undefined;
            if (n) {
              const graph = new GraphView(
                (v: XmlNode) => this.jump(v.fileId, v.nodeId),
                this.report,
              );
              box.append(graph.element);
              await graph.load(n);
            } else box.append(el('div', 'empty', '対応するノードなし'));
          }
          this.details.append(box);
        } catch (e) {
          this.report(e);
        }
      }),
    );
    this.details.append(bar);
  }
}
