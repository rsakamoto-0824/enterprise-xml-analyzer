import { serve } from './runtime';
import { extract, headers } from '../export/extractionEngine';
import { validateTemplate, defaultTemplate } from '../export/templateManager';
import { writeExcel } from '../export/excelWriter';
import { csvLine } from '../export/csvWriter';
import { db, fileRange } from '../storage/database';
import { LIMITS, type ExportRow, type ExportTemplate, type DiffRow } from '../models/types';
let stream: AsyncGenerator<ExportRow> | undefined;
let csvHeader = '';
async function* diffRows(id: string, filter: string): AsyncGenerator<ExportRow> {
  let after = 0;
  const database = await db(),
    comparison = await database.get('Comparisons', id);
  if (!comparison || comparison.status !== 'COMPLETED')
    throw new Error('COMPARISON_MISSING: 比較結果がありません。');
  const a = await database.get('Files', comparison.a),
    b = await database.get('Files', comparison.b);
  while (true) {
    const rows: DiffRow[] = await database.getAll(
      'DiffResults',
      IDBKeyRange.bound([id, after], [id, Number.MAX_SAFE_INTEGER], true),
      1000,
    );
    if (!rows.length) break;
    for (const row of rows)
      if (!filter || row.type === filter)
        yield {
          sheet: 'XML差分',
          source: '',
          cells: [
            row.type,
            row.path,
            row.name,
            row.attributeName,
            row.oldValue,
            row.newValue,
            a.name,
            b.name,
            row.confidence,
            row.details,
            row.covered,
          ],
        };
    after = rows.at(-1)!.seq;
  }
}
serve(async (action, p, ctx) => {
  if (action === 'diff') {
    const t = defaultTemplate();
    t.sourceFile = false;
    t.columns = [
      'DiffType',
      'ElementPath',
      'ElementName',
      'AttributeName',
      'OldValue',
      'NewValue',
      'SourceFileA',
      'SourceFileB',
      'Confidence',
      'Details',
      'CoveredNodes',
    ].map((name) => ({
      name,
      path: '',
      type: 'string',
      missing: 'blank',
      repeat: 'join',
      enabled: true,
    }));
    return writeExcel(diffRows(p.id, p.filter), t, ctx.cancelled);
  }
  if (action === 'csv-next') {
    if (!stream) throw new Error('CSV_STATE: 出力処理が開始されていません。');
    let chunk = csvHeader;
    csvHeader = '';
    let done = false;
    for (let i = 0; i < 500; i++) {
      if (ctx.cancelled()) {
        await stream.return(undefined);
        stream = undefined;
        throw new Error('CANCELLED: CSVを中断しました。');
      }
      const next = await stream.next();
      if (next.done) {
        done = true;
        stream = undefined;
        break;
      }
      chunk += csvLine(next.value.cells);
      if (chunk.length > LIMITS.chunkBytes) break;
    }
    return { chunk, done };
  }
  if (action === 'csv-close') {
    await stream?.return(undefined);
    stream = undefined;
    return true;
  }
  const t = validateTemplate(p.template);
  if (action === 'csv-open') {
    if (t.columns.some((c) => c.enabled && c.repeat === 'sheet'))
      throw new Error('CSV_SHEETS: 別シート出力はXLSX形式で使用してください。');
    stream = extract(p.fileIds, t, ctx.cancelled, ctx.progress);
    csvHeader = '\uFEFF' + (t.header ? csvLine(headers(t)) : '');
    return true;
  }
  if (action === 'preview') {
    const rows: ExportRow[] = [];
    let more = false;
    for await (const row of extract(p.fileIds, t, ctx.cancelled, ctx.progress)) {
      if (rows.length === 100) {
        more = true;
        break;
      }
      rows.push(row);
    }
    let baseRecords = 0;
    for (const id of p.fileIds)
      baseRecords +=
        t.layout === 'records'
          ? await (
              await db()
            ).countFromIndex(
              'Nodes',
              'path',
              IDBKeyRange.bound([id, t.recordPath, 0], [id, t.recordPath, Number.MAX_SAFE_INTEGER]),
            )
          : await (await db()).count('Nodes', fileRange(id));
    return { headers: headers(t), rows, more, baseRecords };
  }
  return writeExcel(extract(p.fileIds, t, ctx.cancelled, ctx.progress), t, ctx.cancelled);
});
