import { attributes, db, node, page } from '../storage/database';
import { defaultTemplate, validateTemplate } from '../export/templateManager';
import { WorkerClient } from '../workers/client';
import { LIMITS, type ExportTemplate, type XmlFile, type XmlNode } from '../models/types';
import { button, checkbox, download, el, field, input, select, table } from './dom';

export class ExportBuilder {
  readonly element = el('div', 'export-builder');
  template: ExportTemplate = defaultTemplate();
  private worker = new WorkerClient('export');
  private selectedFiles = new Set<string>();
  private preview = el('div', 'preview');
  private fileList: XmlFile[] = [];
  private cancelled = false;
  constructor(
    private run: <T>(
      label: string,
      fn: () => Promise<T>,
      cancel?: () => void,
    ) => Promise<T | undefined>,
    private report: (e: unknown) => void,
    private notify: (s: string) => void,
  ) {}
  async setRecord(n: XmlNode) {
    this.template.recordPath = n.path;
    this.template.columns = [];
    this.selectedFiles.add(n.fileId);
    const descendants = await page(n.fileId, n.nodeId - 1, n.subtreeEndOrder, 2000);
    const paths = new Set<string>();
    for (const child of descendants)
      if (child.nodeType === 'element') {
        const path = child.path === n.path ? '.' : child.path.slice(n.path.length + 1);
        if (child.nodeId !== n.nodeId) paths.add(path);
        for (const a of await attributes(n.fileId, child.nodeId))
          if (a.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
            paths.add((path === '.' ? '' : path + '/') + '@' + a.name);
      }
    if (
      descendants.some(
        (child) =>
          child.parentId === n.nodeId && (child.nodeType === 'text' || child.nodeType === 'cdata'),
      )
    )
      paths.add('.');
    if (!paths.size) paths.add('.');
    for (const path of [...paths].slice(0, 256))
      this.template.columns.push({
        path,
        name: path.split('/').at(-1)!.replace(/^@/, ''),
        type: 'string',
        missing: 'blank',
        repeat: 'join',
        enabled: true,
      });
    this.render(this.fileList);
  }
  render(files: XmlFile[]) {
    this.fileList = files;
    if (!this.selectedFiles.size) for (const f of files) this.selectedFiles.add(f.fileId);
    this.element.replaceChildren();
    const title = el('div', 'section-title');
    title.append(
      el('div', '', 'XMLから、使いたい項目だけを。'),
      el('p', 'muted', '構造ビューで基準要素を選ぶと、属性・子要素を列として設定できます。'),
    );
    this.element.append(title);
    const filesBox = el('div', 'toolbar');
    for (const f of files)
      filesBox.append(
        checkbox(f.name, this.selectedFiles.has(f.fileId), (v) =>
          v ? this.selectedFiles.add(f.fileId) : this.selectedFiles.delete(f.fileId),
        ),
      );
    this.element.append(filesBox);
    const controls = el('div', 'form-grid');
    const record = input('レコード基準パス', this.template.recordPath);
    record.placeholder = '/Root/Product';
    record.onchange = () => (this.template.recordPath = record.value);
    const layout = select(
      'レコード形式',
      [
        ['records', '繰り返し要素を1行'],
        ['nodes', '要素1個を1行'],
        ['hierarchy', '階層を列へ展開'],
      ],
      this.template.layout,
    );
    layout.onchange = () => {
      this.template.layout = layout.value as ExportTemplate['layout'];
      if (layout.value === 'hierarchy') {
        this.template.columns = Array.from({ length: 8 }, (_, i) => ({
          path: `$level:${i}`,
          name: `階層${i}`,
          type: 'string',
          missing: 'blank',
          repeat: 'first',
          enabled: true,
        }));
        this.render(files);
      }
    };
    const grouping = select(
      '複数ファイルの出力',
      [
        ['merged', '1シートに統合'],
        ['sheets', 'XMLごとに別シート'],
        ['files', 'XMLごとに別ファイル'],
      ],
      this.template.grouping,
    );
    grouping.onchange = () =>
      (this.template.grouping = grouping.value as ExportTemplate['grouping']);
    controls.append(
      field('レコード基準パス', record),
      field('1行の単位', layout),
      field('複数XMLのまとめ方', grouping),
    );
    this.element.append(controls);
    const columnBar = el('div', 'toolbar');
    columnBar.append(
      el('h3', '', '出力列'),
      button('列を追加', () => {
        this.template.columns.push({
          path: '.',
          name: '新しい列',
          type: 'string',
          missing: 'blank',
          repeat: 'join',
          enabled: true,
        });
        this.render(files);
      }),
      button('ファイル名', () => this.add('$file', 'XMLファイル名')),
      button('要素名', () => this.add('$name', '要素名')),
      button('パス', () => this.add('$path', 'パス')),
      button('Namespace', () => this.add('$namespace', 'Namespace')),
      button('親要素', () => this.add('../$name', '親要素')),
      button('属性一覧', () => this.add('$attributes', '属性一覧')),
    );
    this.element.append(columnBar);
    const columns = el('div', 'table-scroll');
    const t = table(['出力', '列名', '相対パス / @属性', '型', '繰り返し', '欠損', '列順'], []);
    const body = t.querySelector('tbody')!;
    this.template.columns.forEach((c, index) => {
      const row = el('tr'),
        enabled = checkbox(`${c.name}を出力`, c.enabled, (v) => (c.enabled = v)),
        name = input('列名 ' + (index + 1), c.name),
        path = input('抽出パス ' + (index + 1), c.path);
      name.onchange = () => (c.name = name.value);
      path.onchange = () => (c.path = path.value);
      const type = select(
        '型 ' + (index + 1),
        [
          ['string', '文字列'],
          ['number', '数値'],
          ['date', '日付'],
          ['boolean', '真偽値'],
        ],
        c.type,
      );
      type.onchange = () => (c.type = type.value as typeof c.type);
      const repeat = select(
        '繰り返し ' + (index + 1),
        [
          ['join', '結合'],
          ['first', '最初のみ'],
          ['rows', '行展開'],
          ['sheet', '別シート'],
        ],
        c.repeat,
      );
      repeat.onchange = () => (c.repeat = repeat.value as typeof c.repeat);
      const missing = select(
        '欠損 ' + (index + 1),
        [
          ['blank', '空欄'],
          ['error', 'エラー'],
        ],
        c.missing,
      );
      missing.onchange = () => (c.missing = missing.value as typeof c.missing);
      const order = el('div', 'row-actions');
      const up = button('↑', () => {
        if (index > 0) {
          [this.template.columns[index - 1], this.template.columns[index]] = [
            c,
            this.template.columns[index - 1],
          ];
          this.render(files);
        }
      });
      up.setAttribute('aria-label', `${c.name}を上へ`);
      const down = button('↓', () => {
        if (index + 1 < this.template.columns.length) {
          [this.template.columns[index + 1], this.template.columns[index]] = [
            c,
            this.template.columns[index + 1],
          ];
          this.render(files);
        }
      });
      down.setAttribute('aria-label', `${c.name}を下へ`);
      order.append(
        up,
        down,
        button('×', () => {
          this.template.columns.splice(index, 1);
          this.render(files);
        }),
      );
      for (const cell of [enabled, name, path, type, repeat, missing, order]) {
        const td = el('td');
        td.append(cell);
        row.append(td);
      }
      body.append(row);
    });
    columns.append(t);
    this.element.append(columns);
    const filterBar = el('div', 'toolbar');
    const logic = select(
      'フィルターの条件',
      [
        ['AND', 'すべて満たす（AND）'],
        ['OR', 'いずれか（OR）'],
      ],
      this.template.logic,
    );
    logic.onchange = () => (this.template.logic = logic.value as 'AND' | 'OR');
    filterBar.append(
      el('h3', '', 'フィルター'),
      logic,
      button('条件追加', () => {
        this.template.filters.push({ path: '@id', op: 'equals', value: '' });
        this.render(files);
      }),
    );
    this.element.append(filterBar);
    this.template.filters.forEach((f, index) => {
      const bar = el('div', 'toolbar'),
        path = input('フィルターパス', f.path),
        op = select(
          '条件',
          [
            ['equals', '完全一致'],
            ['contains', '部分一致'],
            ['range', '数値範囲'],
            ['empty', '空欄'],
            ['exists', '項目・属性あり'],
          ],
          f.op,
        ),
        value = input('比較値 / 下限', f.value),
        max = input('上限', f.max ?? '');
      path.onchange = () => (f.path = path.value);
      op.onchange = () => (f.op = op.value as typeof f.op);
      value.onchange = () => (f.value = value.value);
      max.onchange = () => (f.max = max.value);
      bar.append(
        path,
        op,
        value,
        max,
        button('削除', () => {
          this.template.filters.splice(index, 1);
          this.render(files);
        }),
      );
      this.element.append(bar);
    });
    const settings = el('details', 'options');
    settings.append(el('summary', '', '出力書式・安全上限'));
    const options = el('div', 'form-grid');
    const outputName = input(
      '保存ファイル名（拡張子なし）',
      this.template.outputName ?? 'xml-export',
    );
    const columnWidth = input('列幅（文字数）', String(this.template.columnWidth ?? 24), 'number');
    outputName.onchange = () => (this.template.outputName = outputName.value);
    columnWidth.onchange = () => (this.template.columnWidth = Number(columnWidth.value));
    const sheet = input('シート名', this.template.sheetName),
      delimiter = input('繰り返しの区切り', this.template.delimiter),
      rows = input('シートあたりのデータ行', String(this.template.sheetRows), 'number');
    sheet.onchange = () => (this.template.sheetName = sheet.value);
    delimiter.onchange = () => (this.template.delimiter = delimiter.value);
    rows.onchange = () => (this.template.sheetRows = Number(rows.value));
    options.append(
      field('保存ファイル名（拡張子なし）', outputName),
      field('列幅（文字数）', columnWidth),
      field('シート名', sheet),
      field('結合の区切り', delimiter),
      field('シート分割行数', rows),
      checkbox('ヘッダー', this.template.header, (v) => (this.template.header = v)),
      checkbox('フィルター', this.template.autoFilter, (v) => (this.template.autoFilter = v)),
      checkbox('ウィンドウ枠固定', this.template.freeze, (v) => (this.template.freeze = v)),
      checkbox('SourceFile列', this.template.sourceFile, (v) => (this.template.sourceFile = v)),
    );
    settings.append(
      options,
      el(
        'p',
        'muted',
        'XLSXは最大5万行・50万セル・1600万文字。1セル32767文字を超える場合は停止します。CSVでは数式に見える文字列に先頭アポストロフィを加えます。',
      ),
    );
    this.element.append(settings);
    const actions = el('div', 'toolbar');
    actions.append(
      button('プレビュー（100行）', () => this.showPreview(), 'primary'),
      button('XLSXを保存', () => this.exportXlsx()),
      button('CSVを保存', () => this.exportCsv()),
      button('設定JSON保存', () => this.saveTemplate()),
      button('設定JSON読込', () => this.loadTemplate()),
    );
    this.element.append(actions, this.preview);
  }
  private add(path: string, name: string) {
    this.template.columns.push({
      path,
      name,
      type: 'string',
      missing: 'blank',
      repeat: 'join',
      enabled: true,
    });
    this.render(this.fileList);
  }
  private payload() {
    const template = validateTemplate(this.template);
    const fileIds = this.fileList
      .filter((f) => this.selectedFiles.has(f.fileId))
      .map((f) => f.fileId);
    if (!fileIds.length) throw new Error('FILES_EMPTY: 対象XMLを選択してください。');
    return { fileIds, template };
  }
  private async showPreview() {
    await this.run(
      '抽出プレビュー',
      async () => {
        const result = await this.worker.call('preview', this.payload());
        this.preview.replaceChildren(
          el('h3', '', `プレビュー ${result.rows.length}行${result.more ? '（続きあり）' : ''}`),
          el(
            'p',
            'muted',
            `抽出前の基準ノード ${result.baseRecords.toLocaleString()}件。フィルター・行展開後の件数は生成時に確定します。`,
          ),
          table(
            result.headers,
            result.rows.map((r: any) => r.cells),
          ),
        );
        if (!result.rows.length)
          this.preview.append(
            el(
              'p',
              'warning',
              '該当するレコードがありません。基準パスとフィルターを確認してください。',
            ),
          );
        else {
          const empty = result.headers.filter((_name: string, index: number) =>
            result.rows.every((r: any) => r.cells[index] === null),
          );
          if (empty.length)
            this.preview.append(
              el('p', 'warning', 'プレビュー内で値が見つからない列: ' + empty.join(', ')),
            );
        }
      },
      () => this.worker.cancel(),
    );
  }
  private async exportXlsx() {
    await this.run(
      'XLSX生成',
      async () => {
        const p = this.payload();
        const groups = p.template.grouping === 'files' ? p.fileIds.map((id) => [id]) : [p.fileIds];
        for (const fileIds of groups) {
          const bytes = await this.worker.call<Uint8Array>('xlsx', { ...p, fileIds });
          download(
            bytes as BlobPart,
            groups.length === 1
              ? `${p.template.outputName ?? 'xml-export'}.xlsx`
              : `${p.template.outputName ?? 'xml-export'}-${groups.indexOf(fileIds) + 1}.xlsx`,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          );
        }
      },
      () => this.worker.cancel(),
    );
  }
  private async exportCsv() {
    let writable: FileSystemWritableFileStream | undefined;
    try {
      this.payload();
      if (typeof (window as any).showSaveFilePicker === 'function') {
        const handle = await (window as any).showSaveFilePicker({
          suggestedName: `${this.template.outputName ?? 'xml-export'}.csv`,
          types: [{ description: 'CSV', accept: { 'text/csv': ['.csv'] } }],
        });
        writable = await handle.createWritable();
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      this.report(e);
      return;
    }
    await this.run(
      'CSV逐次保存',
      async () => {
        this.cancelled = false;
        const p = this.payload();
        if (p.template.grouping === 'files' || p.template.grouping === 'sheets')
          this.notify(
            'CSVは選択したXMLを1ファイルに統合します。シート分割にはXLSXを使用してください。',
          );
        const parts: BlobPart[] = [];
        let bytes = 0;
        try {
          await this.worker.call('csv-open', p);
          while (true) {
            if (this.cancelled) throw new Error('CANCELLED: CSV保存を中断しました。');
            const next = await this.worker.call('csv-next', {});
            const encoded = new TextEncoder().encode(next.chunk);
            bytes += encoded.length;
            if (writable) await writable.write(encoded);
            else {
              if (bytes > LIMITS.csvBlobBytes)
                throw new Error(
                  'CSV_LIMIT: この環境のCSVダウンロードは32MBまでです。HTTPSのEdgeで逐次保存を使用してください。',
                );
              parts.push(encoded);
            }
            if (next.done) break;
          }
          if (writable) await writable.close();
          else
            download(
              new Blob(parts, { type: 'text/csv;charset=utf-8' }),
              `${p.template.outputName ?? 'xml-export'}.csv`,
            );
        } catch (e) {
          await writable?.abort();
          throw e;
        } finally {
          await this.worker.call('csv-close', {});
        }
      },
      () => {
        this.cancelled = true;
        this.worker.cancel();
      },
    );
  }
  private saveTemplate() {
    try {
      const t = validateTemplate(this.template);
      download(JSON.stringify(t, null, 2), 'xml-export-template.json', 'application/json');
      void db().then((database) => database.put('ExportTemplates', { name: 'last', template: t }));
    } catch (e) {
      this.report(e);
    }
  }
  private loadTemplate() {
    const picker = input('JSONテンプレート', '', 'file');
    picker.accept = '.json';
    picker.onchange = () => {
      void (async () => {
        try {
          const f = picker.files?.[0];
          if (!f) return;
          if (f.size > 262144) throw new Error('TEMPLATE_LIMIT: JSONは256KB以内にしてください。');
          this.template = validateTemplate(JSON.parse(await f.text()));
          this.render(this.fileList);
          this.notify('設定を読み込みました。プレビューで項目の有無を確認してください。');
        } catch (e) {
          this.report(e);
        }
      })();
    };
    picker.click();
  }
}
