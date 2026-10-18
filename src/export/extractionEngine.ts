import {
  attributes,
  children,
  db,
  directText,
  instancePath,
  node,
  page,
} from '../storage/database';
import {
  LIMITS,
  type Cell,
  type Column,
  type ExportRow,
  type ExportTemplate,
  type XmlNode,
} from '../models/types';
export async function valuesFor(
  record: XmlNode,
  path: string,
  fileName: string,
): Promise<string[]> {
  if (path.startsWith('$level:')) {
    const level = Number(path.slice(7));
    let current: XmlNode | undefined = record;
    while (current && current.depth > level) current = await node(record.fileId, current.parentId);
    return current?.depth === level ? [current.elementName] : [];
  }
  if (path === '$name') return [record.elementName];
  if (path === '$path') return [await instancePath(record)];
  if (path === '$namespace') return [record.namespaceURI];
  if (path === '$depth') return [String(record.depth)];
  if (path === '$file') return [fileName];
  if (path === '$attributes') {
    return (await attributes(record.fileId, record.nodeId)).map((a) => `${a.name}=${a.value}`);
  }
  if (path.startsWith('../')) {
    const parent = await node(record.fileId, record.parentId);
    return parent ? valuesFor(parent, path.slice(3), fileName) : [];
  }
  const parts = path
    .replace(/^\.\//, '')
    .split('/')
    .filter((v) => v && v !== '.');
  let current = [record];
  for (const part of parts) {
    if (part === 'text()') break;
    if (part.startsWith('@')) {
      const result: string[] = [];
      for (const n of current)
        for (const a of await attributes(n.fileId, n.nodeId))
          if (a.name === part.slice(1)) result.push(a.value);
      return result;
    }
    const next: XmlNode[] = [];
    for (const parent of current) {
      let after = 0;
      while (true) {
        const batch = await children(parent.fileId, parent.nodeId, after);
        if (!batch.length) break;
        for (const n of batch)
          if (n.nodeType === 'element' && (part === '*' || n.path.split('/').at(-1) === part))
            next.push(n);
        if (next.length > LIMITS.expansionRows)
          throw new Error('EXPANSION_LIMIT: 1レコードの繰り返し値が1万件を超えました。');
        after = batch.at(-1)!.nodeId;
      }
    }
    current = next;
  }
  const result: string[] = [];
  for (const n of current) result.push(await directText(n, LIMITS.tokenChars));
  return result;
}
export function convert(value: string | undefined, c: Column): Cell {
  if (value === undefined) {
    if (c.missing === 'error') throw new Error('MISSING_VALUE: 必須の抽出項目が存在しません。');
    return null;
  }
  if (value.length > LIMITS.cellChars)
    throw new Error('CELL_LIMIT: 1セル32767文字を超えました。抽出項目を変更してください。');
  if (c.type === 'string') return value;
  if (c.type === 'number') {
    if (
      !value.trim() ||
      !Number.isFinite(Number(value)) ||
      (Number.isInteger(Number(value)) && !Number.isSafeInteger(Number(value)))
    )
      throw new Error(
        'TYPE_NUMBER: 数値変換ができない値、または安全な整数範囲を超えています。文字列を選択してください。',
      );
    return Number(value);
  }
  if (c.type === 'boolean') {
    if (/^(true|1)$/i.test(value)) return true;
    if (/^(false|0)$/i.test(value)) return false;
    throw new Error('TYPE_BOOLEAN: true/false/1/0以外は真偽値に変換できません。');
  }
  if (
    !/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?$/.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    throw new Error('TYPE_DATE: 日付は YYYY-MM-DD またはUTCのISO形式で指定してください。');
  const date = new Date(value);
  if (date.toISOString().slice(0, 10) !== value.slice(0, 10))
    throw new Error('TYPE_DATE: 存在しない日付です。');
  return date;
}
export function expand(values: Cell[][], max: number = LIMITS.expansionRows): Cell[][] {
  const size = values.reduce((n, v) => n * Math.max(1, v.length), 1);
  if (size > max)
    throw new Error(
      'EXPANSION_LIMIT: 行展開の組み合わせが1万行を超えます。結合を選択してください。',
    );
  let rows: Cell[][] = [[]];
  for (const list of values) {
    const next: Cell[][] = [];
    for (const row of rows)
      for (const value of list.length ? list : [null]) next.push([...row, value]);
    rows = next;
  }
  return rows;
}
export function headers(t: ExportTemplate): string[] {
  return [
    ...(t.sourceFile ? ['SourceFile'] : []),
    ...(t.columns.some((c) => c.enabled && c.repeat === 'sheet') ? ['RecordPath'] : []),
    ...t.columns.filter((c) => c.enabled).map((c) => c.name),
  ];
}
export async function* extract(
  fileIds: string[],
  t: ExportTemplate,
  cancelled: () => boolean,
  progress: (v: unknown) => void,
): AsyncGenerator<ExportRow> {
  let scanned = 0,
    rows = 0;
  const columns = t.columns.filter((c) => c.enabled);
  for (const fileId of fileIds) {
    const f = await (await db()).get('Files', fileId);
    if (!f || f.status !== 'READY')
      throw new Error('FILE_NOT_READY: 解析済みファイルを選択してください。');
    let after = 0;
    while (true) {
      const batch: XmlNode[] =
        t.layout === 'records'
          ? await (
              await db()
            ).getAllFromIndex(
              'Nodes',
              'path',
              IDBKeyRange.bound(
                [fileId, t.recordPath, after],
                [fileId, t.recordPath, Number.MAX_SAFE_INTEGER],
                true,
              ),
              LIMITS.queryPage,
            )
          : await page(fileId, after);
      if (!batch.length) break;
      for (const record of batch) {
        if (cancelled()) throw new Error('CANCELLED: 出力を中断しました。');
        if (record.nodeType !== 'element') continue;
        scanned++;
        const matches: boolean[] = [];
        for (const filter of t.filters) {
          const values = await valuesFor(record, filter.path, f.name);
          matches.push(
            filter.op === 'exists'
              ? values.length > 0
              : filter.op === 'empty'
                ? values.length === 0 || values.every((v) => !v.trim())
                : values.some((v) =>
                    filter.op === 'equals'
                      ? v === filter.value
                      : filter.op === 'contains'
                        ? v.includes(filter.value)
                        : v.trim() !== '' &&
                          Number.isFinite(Number(v)) &&
                          Number(v) >= Number(filter.value) &&
                          Number(v) <= Number(filter.max),
                  ),
          );
        }
        if (matches.length && !(t.logic === 'AND' ? matches.every(Boolean) : matches.some(Boolean)))
          continue;
        const cells: Cell[][] = [];
        const extra: { column: number; values: Cell[] }[] = [];
        for (let i = 0; i < columns.length; i++) {
          const c = columns[i],
            raw = await valuesFor(record, c.path, f.name);
          const values =
            c.repeat === 'join'
              ? [convert(raw.length ? raw.join(t.delimiter) : undefined, c)]
              : c.repeat === 'first'
                ? [convert(raw[0], c)]
                : raw.length
                  ? raw.map((v) => convert(v, c))
                  : [convert(undefined, c)];
          if (c.repeat === 'sheet') {
            extra.push({ column: i, values });
            cells.push([null]);
          } else cells.push(values);
        }
        const sheet =
          t.grouping === 'merged' ? t.sheetName : `${t.sheetName}-${fileIds.indexOf(fileId) + 1}`;
        const recordPath = columns.some((c) => c.repeat === 'sheet')
          ? [await instancePath(record)]
          : [];
        for (const row of expand(cells)) {
          rows++;
          yield {
            sheet,
            source: f.name,
            cells: [...(t.sourceFile ? [f.name] : []), ...recordPath, ...row],
          };
        }
        for (const child of extra)
          for (const value of child.values) {
            const row: Cell[] = new Array(columns.length).fill(null);
            row[child.column] = value;
            yield {
              sheet: `子${child.column + 1}-${fileIds.indexOf(fileId) + 1}`,
              source: f.name,
              cells: [...(t.sourceFile ? [f.name] : []), ...recordPath, ...row],
            };
          }
        if (scanned % 100 === 0) progress({ scanned, rows });
      }
      after = batch.at(-1)!.nodeId;
      progress({ scanned, rows });
    }
  }
}
