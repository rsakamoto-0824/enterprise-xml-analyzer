import type { Cell } from '../models/types';
export function csvCell(value: Cell): string {
  let text = value instanceof Date ? value.toISOString() : value === null ? '' : String(value);
  if (typeof value === 'string' && /^[\s\u0000-\u001f]*[=+\-@]/u.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
export const csvLine = (cells: Cell[]) => cells.map(csvCell).join(',') + '\r\n';
