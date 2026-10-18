import { LIMITS, type ExportTemplate } from '../models/types';
export const defaultTemplate = (): ExportTemplate => ({
  version: 1,
  recordPath: '',
  layout: 'records',
  columns: [],
  filters: [],
  logic: 'AND',
  delimiter: ' | ',
  sourceFile: true,
  grouping: 'merged',
  sheetName: '抽出結果',
  header: true,
  autoFilter: true,
  freeze: true,
  sheetRows: LIMITS.sheetRows,
  format: 'xlsx',
  outputName: 'xml-export',
  columnWidth: 24,
});
export function validateTemplate(input: unknown): ExportTemplate {
  if (!input || typeof input !== 'object')
    throw new Error('TEMPLATE_INVALID: JSONオブジェクトが必要です。');
  const t = input as ExportTemplate;
  const one = (value: unknown, choices: string[]) =>
    typeof value === 'string' && choices.includes(value);
  const str = (value: unknown, max = 2048) => typeof value === 'string' && value.length <= max;
  if (
    (t.outputName !== undefined &&
      (!str(t.outputName, 120) ||
        !t.outputName.trim() ||
        /[\\/<>:"|?*\x00-\x1f]/.test(t.outputName))) ||
    (t.columnWidth !== undefined &&
      (!Number.isFinite(t.columnWidth) || t.columnWidth < 1 || t.columnWidth > 100))
  )
    throw new Error('TEMPLATE_INVALID: ファイル名または列幅が不正です。');
  if (
    t.version !== 1 ||
    !str(t.recordPath) ||
    !one(t.layout, ['records', 'nodes', 'hierarchy']) ||
    !Array.isArray(t.columns) ||
    !t.columns.length ||
    t.columns.length > 256 ||
    !Array.isArray(t.filters) ||
    t.filters.length > 50 ||
    !one(t.logic, ['AND', 'OR']) ||
    !str(t.delimiter, 32) ||
    !str(t.sheetName, 31) ||
    !t.sheetName.trim() ||
    /[\\/*?:\[\]]/.test(t.sheetName) ||
    !one(t.grouping, ['merged', 'sheets', 'files']) ||
    !one(t.format, ['xlsx', 'csv']) ||
    !Number.isInteger(t.sheetRows) ||
    t.sheetRows < 1 ||
    t.sheetRows > LIMITS.sheetRows
  )
    throw new Error('TEMPLATE_INVALID: 設定形式・件数・シート名を確認してください。');
  for (const k of ['sourceFile', 'header', 'autoFilter', 'freeze'] as const)
    if (typeof t[k] !== 'boolean') throw new Error('TEMPLATE_INVALID: 真偽値の設定が不正です。');
  for (const c of t.columns)
    if (
      !c ||
      !str(c.path) ||
      !str(c.name, 128) ||
      !c.name.trim() ||
      !one(c.type, ['string', 'number', 'date', 'boolean']) ||
      !one(c.missing, ['blank', 'error']) ||
      !one(c.repeat, ['first', 'join', 'rows', 'sheet']) ||
      typeof c.enabled !== 'boolean'
    )
      throw new Error('TEMPLATE_INVALID: 列の定義が不正です。');
  if (!t.columns.some((c) => c.enabled))
    throw new Error('TEMPLATE_INVALID: 出力項目を1つ以上選択してください。');
  for (const f of t.filters)
    if (
      !f ||
      !str(f.path) ||
      !str(f.value) ||
      !one(f.op, ['equals', 'contains', 'range', 'empty', 'exists']) ||
      (f.max !== undefined && !str(f.max)) ||
      (f.op === 'range' &&
        (!Number.isFinite(Number(f.value)) ||
          !Number.isFinite(Number(f.max)) ||
          !f.value.trim() ||
          !f.max?.trim()))
    )
      throw new Error('TEMPLATE_INVALID: フィルターが不正です。');
  return structuredClone(t);
}
