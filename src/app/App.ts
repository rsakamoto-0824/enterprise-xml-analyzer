import {
  attributes,
  children,
  clearAll,
  db,
  deleteFile,
  files,
  node,
  startupCleanup,
} from '../storage/database';
import { LIMITS, type XmlFile, type XmlNode } from '../models/types';
import { WorkerClient } from '../workers/client';
import {
  button,
  checkbox,
  download,
  el,
  field,
  formatBytes,
  input,
  select,
  table,
} from '../components/dom';
import { TreeView } from '../components/TreeView';
import { GraphView } from '../visualization/graphRenderer';
import { ExportBuilder } from '../components/ExportBuilder';
import { CompareView } from '../components/CompareView';

const routes = [
  ['load', 'XML読込'],
  ['tree', '構造ビュー'],
  ['graph', 'グラフビュー'],
  ['compare', 'XML比較'],
  ['export', 'Excel出力'],
  ['stats', '構造統計'],
  ['settings', '設定'],
] as const;
export class App {
  private fileList: XmlFile[] = [];
  private selectedFile = '';
  private selectedNode?: XmlNode;
  private currentRoute = 'load';
  private active = false;
  private abort?: () => void;
  private queueCancelled = false;
  private parser = new WorkerClient('parser');
  private searcher = new WorkerClient('index');
  private root = el('main', 'workspace');
  private nav = el('nav', 'nav');
  private status = el('span', 'status-text', 'READY · ファイルを読み込めます');
  private progress = el('progress');
  private toast = el('div', 'toast');
  private context = el('div', 'file-context');
  private stats = el('div', 'header-stats');
  private picker = select('表示中のXML', []);
  private cancelButton = button('中断', () => this.abort?.(), 'danger');
  private tree = new TreeView(
    (n) => {
      this.selectedNode = n;
    },
    (n) => void this.useRecord(n),
    (e) => this.report(e),
  );
  private graph = new GraphView(
    (n) => {
      this.selectedNode = n;
      void this.tree.selectNode(n);
      this.notify(`選択: ${n.elementName}。構造ビューで詳細を確認できます。`);
    },
    (e) => this.report(e),
  );
  private exporter = new ExportBuilder(
    this.run.bind(this),
    this.report.bind(this),
    this.notify.bind(this),
  );
  private compare = new CompareView(
    this.run.bind(this),
    this.report.bind(this),
    (f, id) => void this.jump(f, id),
  );
  private logs: {
    time: string;
    event: string;
    durationMs: number;
    result: string;
    code?: string;
  }[] = [];
  private fileHandles = new Map<string, File>();
  private lockRelease?: () => void;
  async start() {
    const container = document.querySelector('#app')!;
    const header = el('header', 'app-header'),
      brand = el('div', 'brand');
    brand.append(
      el('div', 'brand-mark', '⌘'),
      el('div', 'brand-name', 'Enterprise XML Analyzer'),
      el('span', 'edition', 'LOCAL WORKSPACE'),
    );
    header.append(
      brand,
      button('◐ テーマ', () => {
        document.documentElement.dataset.theme =
          document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        localStorage.setItem('xml-theme', document.documentElement.dataset.theme);
      }),
    );
    document.documentElement.dataset.theme = localStorage.getItem('xml-theme') ?? 'light';
    this.nav.setAttribute('aria-label', 'メインメニュー');
    for (const [id, label] of routes) {
      const a = el('a', 'nav-item', label);
      a.href = '#/' + id;
      this.nav.append(a);
    }
    this.context.append(this.picker, this.stats);
    this.picker.onchange = () => {
      this.selectedFile = this.picker.value;
      this.selectedNode = undefined;
      void this.navigate().catch((e) => this.report(e));
    };
    const footer = el('footer', 'statusbar');
    this.progress.max = 100;
    this.progress.value = 0;
    this.cancelButton.hidden = true;
    this.status.setAttribute('role', 'status');
    this.toast.setAttribute('role', 'alert');
    footer.append(
      el('span', 'privacy-dot', '● ローカル処理 · XML送信なし'),
      this.status,
      this.progress,
      this.cancelButton,
    );
    container.append(header, this.nav, this.context, this.root, this.toast, footer);
    if (!navigator.locks) {
      this.root.append(
        el('p', 'error', '安全なセッション管理にはHTTPSまたはlocalhostのEdgeが必要です。'),
      );
      return;
    }
    const acquired = await new Promise<boolean>((resolve) => {
      void navigator.locks.request(
        'xml-analyzer-exclusive',
        { ifAvailable: true },
        async (lock) => {
          if (!lock) {
            resolve(false);
            return;
          }
          resolve(true);
          await new Promise<void>((release) => (this.lockRelease = release));
        },
      );
    });
    if (!acquired) {
      this.root.append(
        el(
          'p',
          'error',
          'このアプリは別のタブで使用中です。データを保護するため、そのタブを閉じてから再読込してください。',
        ),
      );
      return;
    }
    const removed = await startupCleanup();
    if (removed) this.notify(`前回の一時データ ${removed}件を削除しました。`);
    window.addEventListener('hashchange', () => void this.navigate().catch((e) => this.report(e)));
    window.addEventListener('beforeunload', (e) => {
      if (this.active) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
    window.addEventListener('pagehide', () => {
      this.lockRelease?.();
      void (async () => {
        for (const f of await files()) if (!f.persistent) await deleteFile(f.fileId);
      })();
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
      e.preventDefault();
      if (e.dataTransfer?.files.length) void this.importFiles([...e.dataTransfer.files]);
    });
    await this.refresh();
    await this.navigate();
  }
  notify(message: string) {
    this.toast.textContent = message;
    this.toast.classList.add('visible');
    setTimeout(() => this.toast.classList.remove('visible'), 10000);
  }
  report(error: unknown) {
    const message =
      error instanceof Error && /^[A-Z_]+:/.test(error.message)
        ? error.message
        : 'PROCESS_ERROR: 操作に失敗しました。入力とブラウザ保存領域を確認してください。';
    this.status.textContent = message;
    this.notify(message);
  }
  async run<T>(label: string, fn: () => Promise<T>, cancel?: () => void): Promise<T | undefined> {
    if (this.active) {
      this.notify('処理中です。完了または中断後に操作してください。');
      return;
    }
    this.active = true;
    this.abort = cancel;
    this.cancelButton.hidden = !cancel;
    this.status.textContent = label + '…';
    const start = performance.now();
    this.progress.removeAttribute('value');
    try {
      const result = await fn();
      this.status.textContent = label + ' · 完了';
      this.logs.push({
        time: new Date().toISOString(),
        event: label,
        durationMs: performance.now() - start,
        result: 'COMPLETED',
      });
      return result;
    } catch (e) {
      this.report(e);
      this.logs.push({
        time: new Date().toISOString(),
        event: label,
        durationMs: performance.now() - start,
        result: 'ERROR',
        code: e instanceof Error ? e.message.split(':')[0] : 'PROCESS_ERROR',
      });
      return undefined;
    } finally {
      this.active = false;
      this.abort = undefined;
      this.cancelButton.hidden = true;
      this.progress.value = 0;
      if (this.logs.length > 1000) this.logs.shift();
      await this.refresh();
    }
  }
  private async refresh() {
    this.fileList = await files();
    if (!this.fileList.some((f) => f.fileId === this.selectedFile && f.status === 'READY'))
      this.selectedFile = this.fileList.find((f) => f.status === 'READY')?.fileId ?? '';
    this.picker.replaceChildren(
      ...this.fileList
        .filter((f) => f.status === 'READY')
        .map((f) => {
          const o = el('option', '', f.name);
          o.value = f.fileId;
          return o;
        }),
    );
    this.picker.value = this.selectedFile;
    const storage = await navigator.storage?.estimate();
    this.stats.textContent = `${this.fileList.length} ファイル  /  ${this.fileList.reduce((n, f) => n + f.elements, 0).toLocaleString()} 要素  /  保存領域 ${formatBytes(storage?.usage ?? 0)}${storage?.quota ? ' / ' + formatBytes(storage.quota) : ''}`;
  }
  private async navigate() {
    const route = location.hash.replace('#/', '') || 'load';
    this.currentRoute = routes.some(([id]) => id === route) ? route : 'load';
    for (const a of this.nav.querySelectorAll('a')) {
      const active = a.hash === '#/' + this.currentRoute;
      a.classList.toggle('active', active);
      if (active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
    this.root.replaceChildren();
    const title = el('div', 'page-heading');
    title.append(
      el('span', 'eyebrow', 'ENTERPRISE XML ANALYZER'),
      el('h1', '', routes.find(([id]) => id === this.currentRoute)![1]),
    );
    this.root.append(title);
    if (this.currentRoute === 'load') {
      this.renderLoad();
      return;
    }
    if (this.currentRoute === 'settings') {
      this.renderSettings();
      return;
    }
    if (!this.selectedFile) {
      this.root.append(el('div', 'empty', 'XML読込からファイルを選択してください。'));
      return;
    }
    if (this.currentRoute === 'tree') {
      this.root.append(this.searchBar(), this.tree.element);
      await this.tree.load(this.selectedFile);
      if (this.selectedNode?.fileId === this.selectedFile)
        await this.tree.jump(this.selectedNode.nodeId);
    } else if (this.currentRoute === 'graph') {
      this.root.append(
        el(
          'p',
          'muted',
          'クリックで選択、ダブルクリックで展開・折りたたみ。ホイールでズーム、ドラッグで移動。',
        ),
        button('選択ノードにフォーカス', async () => {
          if (this.selectedNode) await this.graph.focus(this.selectedNode);
        }),
        this.graph.element,
      );
      const roots = await children(this.selectedFile, 0);
      const root = roots.find((n) => n.nodeType === 'element');
      if (root) await this.graph.load(root);
    } else if (this.currentRoute === 'export') {
      this.exporter.render(this.fileList.filter((f) => f.status === 'READY'));
      this.root.append(this.exporter.element);
    } else if (this.currentRoute === 'compare') {
      this.compare.render(this.fileList.filter((f) => f.status === 'READY'));
      this.root.append(this.compare.element);
    } else await this.renderStatistics();
  }
  private renderLoad() {
    const hero = el('section', 'load-hero');
    hero.append(
      el('span', 'eyebrow', 'PRIVATE BY DESIGN'),
      el('h2', '', 'XMLの構造を、見えるかたちに。'),
      el(
        'p',
        'muted',
        '読み込み、比較、必要な項目の抽出まで。ファイルはこのPCのブラウザ内で処理されます。',
      ),
    );
    const drop = el('div', 'drop-zone');
    drop.tabIndex = 0;
    drop.setAttribute('role', 'button');
    drop.setAttribute('aria-label', 'XMLファイルを選択');
    drop.append(
      el('div', 'drop-icon', '↥'),
      el('h3', '', 'XMLファイルをここにドロップ'),
      el('p', 'muted', '複数ファイル対応 · UTF-8 / UTF-16 · 100MB以上も分割処理'),
      button('ファイルを選択', () => this.pickFiles(), 'primary'),
    );
    drop.onclick = (e) => {
      if (!(e.target instanceof HTMLButtonElement)) this.pickFiles();
    };
    drop.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        this.pickFiles();
      }
    };
    hero.append(drop);
    this.root.append(hero);
    const metrics = el('div', 'metric-grid');
    for (const [value, label] of [
      [this.fileList.length, '読み込んだファイル'],
      [this.fileList.reduce((n, f) => n + f.elements, 0), 'XML要素'],
      [this.fileList.reduce((n, f) => n + f.attributes, 0), '属性'],
    ]) {
      const card = el('div', 'metric');
      card.append(
        el('span', 'muted', String(label)),
        el('strong', '', Number(value).toLocaleString()),
      );
      metrics.append(card);
    }
    this.root.append(metrics);
    const heading = el('div', 'toolbar');
    heading.append(
      el('h2', '', 'ワークスペースのXML'),
      el('span', 'muted', '一時データは終了時に削除を試み、残留分は次回起動時に削除します。'),
    );
    this.root.append(heading);
    if (!this.fileList.length) {
      this.root.append(el('div', 'empty', 'ファイルを読み込むと、解析情報がここに表示されます。'));
      return;
    }
    const t = table(
      [
        'ファイル名',
        'サイズ / 文字コード',
        '要素 / 属性',
        '最大深さ / Namespace',
        '解析 / 保存時間',
        '状態',
        '操作',
      ],
      [],
    );
    for (const f of this.fileList) {
      const tr = el('tr');
      for (const v of [
        f.name,
        `${formatBytes(f.size)} / ${f.encoding}`,
        `${f.elements.toLocaleString()} / ${f.attributes.toLocaleString()}`,
        `${f.maxDepth} / ${Object.keys(f.namespaces).filter((k) => k !== '(なし)').length}`,
        `${(f.parseMs / 1000).toFixed(2)}s / ${(f.indexMs / 1000).toFixed(2)}s`,
        f.status,
      ])
        tr.append(el('td', '', v));
      const actions = el('td', 'row-actions');
      actions.append(
        button('開く', () => {
          this.selectedFile = f.fileId;
          location.hash = '#/tree';
        }),
        button('再読込', () => this.reload(f.fileId)),
        button('削除', () =>
          this.run('XML削除', async () => {
            await deleteFile(f.fileId);
            this.fileHandles.delete(f.fileId);
            await this.refresh();
            await this.navigate();
          }),
        ),
      );
      tr.append(actions);
      t.querySelector('tbody')!.append(tr);
    }
    const scroll = el('div', 'table-scroll');
    scroll.append(t);
    this.root.append(scroll);
  }
  private pickFiles() {
    const picker = input('XMLファイル', '', 'file');
    picker.accept = '.xml,text/xml,application/xml';
    picker.multiple = true;
    picker.onchange = () => void this.importFiles([...(picker.files ?? [])]);
    picker.click();
  }
  private async reload(id: string) {
    const file = this.fileHandles.get(id);
    if (!file) {
      this.notify('元ファイルを再選択してください。ブラウザは元のパスへ自動アクセスできません。');
      this.pickFiles();
      return;
    }
    await this.importFiles([file]);
  }
  private async importFiles(selected: File[]) {
    if (selected.length > LIMITS.queueFiles) {
      this.notify('一度に読み込めるファイルは16件です。');
      return;
    }
    await this.run(
      'XML解析',
      async () => {
        this.queueCancelled = false;
        for (const file of selected) {
          if (this.queueCancelled) break;
          const fileId = crypto.randomUUID();
          this.status.textContent = `解析中 · ${file.name}`;
          await this.parser.call(
            'parse',
            { file, fileId, persistent: localStorage.getItem('xml-persistent') === 'true' },
            (p) => {
              this.progress.value = p.total ? (p.bytes / p.total) * 100 : 0;
              this.status.textContent = `解析中 · ${file.name} · ${Math.round((p.bytes / p.total) * 100)}% · ${p.elements.toLocaleString()}要素`;
            },
          );
          this.fileHandles.set(fileId, file);
          this.selectedFile = fileId;
        }
        await this.refresh();
        await this.navigate();
      },
      () => {
        this.queueCancelled = true;
        this.parser.cancel();
      },
    );
  }
  private searchBar() {
    const bar = el('div', 'search-area'),
      row = el('div', 'toolbar'),
      query = input('XML内を検索'),
      target = select('検索対象', [
        ['name', '要素名'],
        ['attributeName', '属性名'],
        ['attributeValue', '属性値'],
        ['text', 'テキスト'],
        ['path', 'パス'],
      ]);
    let exact = false,
      caseSensitive = false;
    const results = el('div', 'search-results');
    const search = () =>
      this.run(
        '検索',
        async () => {
          const r = await this.searcher.call('search', {
            fileId: this.selectedFile,
            options: { query: query.value, target: target.value, exact, caseSensitive },
          });
          results.replaceChildren(
            el('span', 'muted', `${r.count}件${r.truncated ? '（先頭1万件を保持）' : ''}`),
          );
          for (const id of r.ids.slice(0, 100))
            results.append(button(`#${id}`, () => this.tree.jump(id).catch((e) => this.report(e))));
          if (r.ids.length > 100)
            results.append(el('span', 'muted', '移動ボタンは先頭100件。条件を絞ってください。'));
        },
        () => this.searcher.cancel(),
      );
    query.onkeydown = (e) => {
      if (e.key === 'Enter') void search();
    };
    row.append(
      query,
      target,
      checkbox('完全一致', false, (v) => (exact = v)),
      checkbox('大小文字を区別', false, (v) => (caseSensitive = v)),
      button('検索', search),
    );
    bar.append(row, results);
    return bar;
  }
  private async useRecord(n: XmlNode) {
    await this.exporter.setRecord(n);
    location.hash = '#/export';
  }
  private async jump(file: string, id: number) {
    this.selectedFile = file;
    this.selectedNode = await node(file, id);
    if (location.hash === '#/tree') await this.navigate();
    else location.hash = '#/tree';
  }
  private async renderStatistics() {
    const f = this.fileList.find((v) => v.fileId === this.selectedFile)!;
    const metrics = el('div', 'metric-grid');
    for (const [label, value] of [
      ['要素数', f.elements],
      ['属性数', f.attributes],
      ['最大階層', f.maxDepth],
      ['Namespace数', Object.keys(f.namespaces).filter((k) => k !== '(なし)').length],
    ]) {
      const card = el('div', 'metric');
      card.append(
        el('span', 'muted', String(label)),
        el('strong', '', Number(value).toLocaleString()),
      );
      metrics.append(card);
    }
    this.root.append(metrics);
    const panels = el('div', 'statistics-grid');
    for (const [label, counts] of [
      ['要素名別出現数', f.elementCounts],
      ['階層別分布', f.depthCounts],
      ['Namespace別', f.namespaces],
      ['子ノード数の分布', f.childCounts],
    ] as const) {
      const panel = el('section', 'panel');
      panel.append(el('h2', '', label));
      const values = Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 30),
        max = Math.max(...values.map((v) => v[1]), 1);
      for (const [name, count] of values) {
        const row = el('div', 'bar-row'),
          bar = el('div', 'bar-track'),
          fill = el('div', 'bar-fill');
        fill.style.width = `${(count / max) * 100}%`;
        bar.append(fill);
        row.append(el('span', 'bar-label', name), bar, el('strong', '', count.toLocaleString()));
        panel.append(row);
      }
      panels.append(panel);
    }
    this.root.append(panels);
    const comparisons = (await (await db()).getAll('Comparisons')).filter(
      (c) => c.status === 'COMPLETED' && (c.a === f.fileId || c.b === f.fileId),
    );
    const comparison = comparisons.at(-1);
    if (comparison) {
      const panel = el('section', 'panel');
      let after = 0;
      const byName = new Map<string, number>(),
        byDepth = new Map<number, number>();
      while (true) {
        const rows = await (
          await db()
        ).getAll(
          'DiffResults',
          IDBKeyRange.bound(
            [comparison.comparisonId, after],
            [comparison.comparisonId, Number.MAX_SAFE_INTEGER],
            true,
          ),
          1000,
        );
        if (!rows.length) break;
        for (const row of rows)
          if (row.type !== 'UNCHANGED' && row.type !== 'AMBIGUOUS') {
            byName.set(row.name, (byName.get(row.name) ?? 0) + 1);
            const depth = row.path.split('/').length - 2;
            byDepth.set(depth, (byDepth.get(depth) ?? 0) + 1);
          }
        after = rows.at(-1).seq;
      }
      const counts = comparison.counts;
      const changed = counts.ADDED + counts.REMOVED + counts.MODIFIED + counts.MOVED;
      const total = changed + counts.UNCHANGED + counts.AMBIGUOUS;
      panel.append(
        el('h2', '', '直近の比較の統計'),
        table(
          ['追加', '削除', '変更', '移動', '変更なし', '曖昧'],
          [
            [
              counts.ADDED,
              counts.REMOVED,
              counts.MODIFIED,
              counts.MOVED,
              counts.UNCHANGED,
              counts.AMBIGUOUS,
            ],
          ],
        ),
        el(
          'p',
          'muted',
          `差分率 ${total ? ((changed / total) * 100).toFixed(2) : '0'}% =（追加 + 削除 + 変更 + 移動）÷ 全分類の合計。曖昧は分母に含め、分子から除きます。省略サブツリーは文書ノード数で加重します。`,
        ),
        el('h3', '', '差分の多い要素・深さ（差分行数）'),
        table(['要素', '行数'], [...byName].sort((a, b) => b[1] - a[1]).slice(0, 20)),
        table(['深さ', '行数'], [...byDepth].sort((a, b) => b[1] - a[1]).slice(0, 20)),
      );
      this.root.append(panel);
    }
  }
  private renderSettings() {
    const panel = el('section', 'panel');
    panel.append(
      el('h2', '', '保存とプライバシー'),
      checkbox(
        'これから読み込むXMLの解析結果を次回も保持する',
        localStorage.getItem('xml-persistent') === 'true',
        (v) => {
          localStorage.setItem('xml-persistent', String(v));
          if (v)
            void navigator.storage
              .persist()
              .then((ok) =>
                this.notify(
                  ok
                    ? '永続ストレージが許可されました。'
                    : '自動消去を防ぐ権限は許可されていません。',
                ),
              );
        },
      ),
      el(
        'p',
        'muted',
        '既存ファイルの保存方針は変わりません。保存データは同じブラウザプロファイル・同じ配信元で共有されます。ブラウザの異常終了時は即時消去を保証できません。OSの共有アカウントで機密XMLを扱わないでください。',
      ),
      button(
        'すべての作業データとテンプレートを削除',
        async () => {
          const removed = await this.run('全データ削除', async () => {
            await clearAll();
            this.fileHandles.clear();
            this.selectedNode = undefined;
            this.notify('ブラウザ内の作業データを削除しました。');
            return true;
          });
          // Reload also releases detached previews and Worker memory containing XML values.
          if (removed) location.reload();
        },
        'danger',
      ),
      button('技術ログをJSON保存', () =>
        download(JSON.stringify(this.logs, null, 2), 'xml-analyzer-log.json', 'application/json'),
      ),
    );
    const memory = (performance as any).memory;
    panel.append(
      el('h3', '', '環境'),
      table(
        ['項目', '状態'],
        [
          ['安全なコンテキスト', isSecureContext ? '有効' : '無効'],
          [
            '逐次ファイル保存',
            'showSaveFilePicker' in window ? '利用可能' : 'Blob保存（最大32MB）',
          ],
          [
            '画面のJavaScriptヒープ',
            memory
              ? formatBytes(memory.usedJSHeapSize) + '（Worker分を含まない）'
              : 'ブラウザが情報を公開していません',
          ],
          ['外部通信', 'XML送信API・テレメトリ・CDNは使用しません'],
          ['XML対応', 'UTF-8 / UTF-16、DTDは禁止'],
          ['安全上限', '深さ256、単一トークン1Mi文字、グラフ500ノード'],
        ],
      ),
      el(
        'p',
        'muted',
        'HTTPS、Windows認証、アクセス許可はIIS側で設定してください。ネットワーク共有のXMLは、Windowsのファイル選択ダイアログから手動で指定します。',
      ),
    );
    this.root.append(panel);
  }
}
