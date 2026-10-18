import ExcelJS from 'exceljs';
import { LIMITS, type ExportRow, type ExportTemplate } from '../models/types';
import { headers } from './extractionEngine';
export async function writeExcel(
  rows: AsyncIterable<ExportRow>,
  template: ExportTemplate,
  cancelled: () => boolean,
): Promise<Uint8Array> {
  const book = new ExcelJS.Workbook();
  book.creator = 'Enterprise XML Analyzer';
  const sheets = new Map<string, { sheet: ExcelJS.Worksheet; count: number; part: number }>();
  let total = 0,
    cells = 0,
    characters = 0;
  function create(name: string, part: number) {
    const suffix = part > 1 ? `-${part}` : '';
    let clean = name.replace(/[\\/*?:\[\]]/g, '_').slice(0, 31 - suffix.length) + suffix;
    while (book.getWorksheet(clean))
      clean = clean.slice(0, 24) + '-' + (book.worksheets.length + 1);
    const sheet = book.addWorksheet(clean, {
      views: template.freeze && template.header ? [{ state: 'frozen', ySplit: 1 }] : [],
    });
    sheet.columns = headers(template).map((header) => ({
      header: template.header ? header : undefined,
      width: template.columnWidth ?? 24,
    }));
    if (template.header) {
      sheet.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' }, name: 'Arial' };
      sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF244B62' } };
    }
    return { sheet, count: 0, part };
  }
  for await (const row of rows) {
    if (cancelled()) throw new Error('CANCELLED: Excel出力を中断しました。');
    total++;
    cells += row.cells.length;
    characters += row.cells.reduce<number>(
      (n, c) => n + (typeof c === 'string' ? c.length : 16),
      0,
    );
    if (total > LIMITS.exportRows || cells > LIMITS.exportCells || characters > LIMITS.exportChars)
      throw new Error(
        'XLSX_LIMIT: XLSXの安全上限（5万行・50万セル・1600万文字）を超えました。逐次CSV保存を使用してください。',
      );
    let state = sheets.get(row.sheet);
    if (!state) state = create(row.sheet, 1);
    if (state.count >= template.sheetRows) state = create(row.sheet, state.part + 1);
    sheets.set(row.sheet, state);
    const output = state.sheet.addRow(row.cells);
    output.font = { name: 'Arial', size: 11 };
    output.alignment = { vertical: 'top', wrapText: true };
    output.eachCell((cell) => {
      if (cell.value instanceof Date) cell.numFmt = 'yyyy-mm-dd';
    });
    state.count++;
  }
  if (!book.worksheets.length) create(template.sheetName, 1);
  if (template.header && template.autoFilter)
    for (const sheet of book.worksheets)
      sheet.autoFilter = {
        from: { row: 1, column: 1 },
        to: { row: Math.max(1, sheet.rowCount), column: headers(template).length },
      };
  return new Uint8Array(await book.xlsx.writeBuffer());
}
