import { describe, it, expect } from 'vitest';
import ExcelJS from 'exceljs';
import { parseXml, detectEncoding } from '../../src/parser/xmlStreamParser';
import {
  attributes,
  children,
  db,
  directText,
  files,
  node,
  page,
  clearAll,
  startupCleanup,
} from '../../src/storage/database';
import { compareXml } from '../../src/diff/diffEngine';
import { csvCell } from '../../src/export/csvWriter';
import { convert, expand, extract } from '../../src/export/extractionEngine';
import { defaultTemplate, validateTemplate } from '../../src/export/templateManager';
import { writeExcel } from '../../src/export/excelWriter';
import { search } from '../../src/search/searchEngine';
import type { Column, DiffOptions, ExportRow } from '../../src/models/types';
const hooks = { cancelled: () => false, progress: () => {} };
const parse = (xml: string, id = 'a', chunkBytes?: number) =>
  parseXml(new Blob([xml]), id + '.xml', id, false, { ...hooks, chunkBytes });
const options: DiffOptions = {
  mode: 'full',
  keyAttribute: 'id',
  whitespace: 'trim',
  comments: false,
  processingInstructions: false,
  ignoreOrder: false,
  verifyHashes: true,
};
const col = (path: string, name = path): Column => ({
  path,
  name,
  type: 'string',
  missing: 'blank',
  repeat: 'join',
  enabled: true,
});
const products =
  '<Root><Product id="001"><Name>りんご</Name><Price>100</Price><Tag>A</Tag><Tag>B</Tag></Product><Product id="002"><Name>みかん</Name><Price>200</Price></Product></Root>';
async function collect<T>(source: AsyncIterable<T>) {
  const values: T[] = [];
  for await (const v of source) values.push(v);
  return values;
}
describe('streaming parser and storage', () => {
  it('parses arbitrary XML with namespaces, Unicode, CDATA, comments and instructions at one-byte boundaries', async () => {
    const xml =
      '<?xml version="1.0" encoding="UTF-8"?><r xmlns="urn:r" xmlns:x="urn:x"><x:a id="01">日本😀<![CDATA[<&]]><!--note--><?p body?></x:a><x:a/></r>';
    const f = await parse(xml, 'a', 1);
    expect(f.elements).toBe(3);
    expect(f.maxDepth).toBe(1);
    const root = await node('a', 1);
    expect(root?.namespaceURI).toBe('urn:r');
    const kids = await children('a', 1);
    expect(kids[1].siblingIndex).toBe(2);
    expect(await directText(kids[0])).toBe('日本😀<&');
    expect((await page('a')).map((n) => n.nodeType)).toContain('pi');
    expect((await attributes('a', kids[0].nodeId))[0].value).toBe('01');
  });
  it.each(['<r><a></r>', '<r a="1" a="2"/>', '<r>&unknown;</r>', '<r/>trailing', '<r><x:y/></r>'])(
    'rejects malformed XML and deletes incomplete data: %s',
    async (xml) => {
      await expect(parse(xml)).rejects.toThrow('XML_SYNTAX');
      expect(await files()).toHaveLength(0);
      expect(await page('a')).toHaveLength(0);
    },
  );
  it.each([
    '<!DOCTYPE r SYSTEM "https://example.invalid/a"><r/>',
    '<!DOCTYPE r [<!ENTITY x "boom">]><r>&x;</r>',
  ])('rejects DTD and entities', async (xml) => {
    await expect(parse(xml)).rejects.toThrow('DTD_FORBIDDEN');
  });
  it('decodes UTF-16 LE/BE with BOM', async () => {
    for (const be of [false, true]) {
      const xml = '<?xml version="1.0" encoding="UTF-16"?><r>日本</r>';
      const bytes = new Uint8Array(2 + xml.length * 2);
      bytes.set(be ? [254, 255] : [255, 254]);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < xml.length; i++) view.setUint16(2 + i * 2, xml.charCodeAt(i), !be);
      const id = be ? 'be' : 'le';
      const f = await parseXml(new Blob([bytes]), id, id, false, { ...hooks, chunkBytes: 3 });
      expect(f.encoding).toBe(be ? 'utf-16be' : 'utf-16le');
      expect(await directText((await node(id, 1))!)).toBe('日本');
    }
  });
  it('rejects unsupported encoding', async () => {
    await expect(
      detectEncoding(new Blob(['<?xml version="1.0" encoding="Shift_JIS"?><r/>'])),
    ).rejects.toThrow('ENCODING');
  });
  it('cancels and cleans all stores', async () => {
    let cancel = false;
    await expect(
      parseXml(new Blob([products]), 'x', 'a', false, {
        chunkBytes: 20,
        cancelled: () => cancel,
        progress: () => (cancel = true),
      }),
    ).rejects.toThrow('CANCELLED');
    expect(await files()).toHaveLength(0);
    expect(await (await db()).count('Attributes')).toBe(0);
  });
  it('computes boundary-independent hashes and ignores attribute order/prefix spelling', async () => {
    await parse('<r a="1" b="2"><n>abc</n></r>', 'a', 1);
    await parse('<r b="2" a="1"><n>abc</n></r>', 'b', 64);
    expect((await node('a', 1))?.subtreeHash).toBe((await node('b', 1))?.subtreeHash);
  });
  it('retains explicit persistence and removes residual session data', async () => {
    await parse('<r/>');
    await parseXml(new Blob(['<r/>']), 'saved', 'b', true, hooks);
    expect(await startupCleanup()).toBe(1);
    expect((await files()).map((f) => f.fileId)).toEqual(['b']);
    await clearAll();
    expect(await files()).toHaveLength(0);
  });
  it('stops excessive depth', async () => {
    await expect(parse('<r>'.repeat(258) + '</r>'.repeat(258))).rejects.toThrow('DEPTH_LIMIT');
  });
});
describe('comparison', () => {
  async function diff(a: string, b: string, change: Partial<DiffOptions> = {}) {
    await parse(a, 'a');
    await parse(b, 'b');
    return compareXml(
      'a',
      'b',
      { ...options, ...change },
      () => false,
      () => {},
    );
  }
  it('finds identical documents', async () => {
    const c = await diff(products, products);
    expect(c.counts.MODIFIED).toBe(0);
    expect(c.counts.AMBIGUOUS).toBe(0);
    expect(c.counts.UNCHANGED).toBeGreaterThan(0);
  });
  it('identifies additions and removals by key', async () => {
    const c = await diff('<r><n id="a"/><n id="b"/></r>', '<r><n id="b"/><n id="c"/></r>');
    expect(c.counts.ADDED).toBe(1);
    expect(c.counts.REMOVED).toBe(1);
  });
  it('detects attribute and text changes', async () => {
    const c = await diff('<r a="1">before</r>', '<r a="2">after</r>');
    expect(c.counts.MODIFIED).toBe(2);
  });
  it('ignores values in structure mode', async () => {
    const c = await diff('<r a="1">before</r>', '<r a="2">after</r>', { mode: 'structure' });
    expect(c.counts.MODIFIED).toBe(0);
  });
  it('detects namespace changes', async () => {
    const c = await diff('<r xmlns="a"/>', '<r xmlns="b"/>');
    expect(c.counts.ADDED).toBe(1);
    expect(c.counts.REMOVED).toBe(1);
  });
  it('reports reliable sibling moves with scoped unique keys', async () => {
    const c = await diff('<r><n id="a"/><n id="b"/></r>', '<r><n id="b"/><n id="a"/></r>');
    expect(c.counts.MOVED).toBe(2);
  });
  it('makes duplicate keys ambiguous', async () => {
    const c = await diff('<r><n id="a"/><n id="a"/></r>', '<r><n id="a"/><n id="a"/></r>');
    expect(c.counts.AMBIGUOUS).toBe(2);
  });
  it('marks repeated modified nodes without keys ambiguous', async () => {
    const c = await diff('<r><n>1</n><n>2</n></r>', '<r><n>3</n><n>4</n></r>');
    expect(c.counts.AMBIGUOUS).toBeGreaterThan(0);
  });
  it('value mode ignores keyed reordering', async () => {
    const c = await diff('<r><n id="a"/><n id="b"/></r>', '<r><n id="b"/><n id="a"/></r>', {
      mode: 'value',
    });
    expect(c.counts.MODIFIED + c.counts.MOVED).toBe(0);
  });
  it('cleans cancelled comparisons', async () => {
    await parse('<r/>', 'a');
    await parse('<r/>', 'b');
    await expect(
      compareXml(
        'a',
        'b',
        options,
        () => true,
        () => {},
      ),
    ).rejects.toThrow('CANCELLED');
    expect(await (await db()).count('Comparisons')).toBe(0);
  });
});
describe('extract/export/security', () => {
  it('extracts repeat records, preserves IDs and filters numeric ranges', async () => {
    await parse(products);
    const t = defaultTemplate();
    t.recordPath = '/Root/Product';
    t.columns = [col('@id', 'id'), col('Name'), col('Price'), col('Tag')];
    t.filters = [{ path: 'Price', op: 'range', value: '150', max: '300' }];
    const rows = await collect(
      extract(
        ['a'],
        t,
        () => false,
        () => {},
      ),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].cells).toEqual(['a.xml', '002', 'みかん', '200', null]);
  });
  it('supports missing paths across multiple files', async () => {
    await parse(products);
    await parse('<Root><Product id="003"/></Root>', 'b');
    const t = defaultTemplate();
    t.recordPath = '/Root/Product';
    t.columns = [col('@id'), col('Name')];
    const rows = await collect(
      extract(
        ['a', 'b'],
        t,
        () => false,
        () => {},
      ),
    );
    expect(rows).toHaveLength(3);
    expect(rows[2].cells).toEqual(['b.xml', '003', null]);
  });
  it('expands repeated children and limits Cartesian products', async () => {
    expect(
      expand([
        [1, 2],
        ['a', 'b'],
      ]),
    ).toHaveLength(4);
    expect(() => expand([new Array(101).fill(1), new Array(101).fill(1)])).toThrow(
      'EXPANSION_LIMIT',
    );
    await parse(products);
    const t = defaultTemplate();
    t.recordPath = '/Root/Product';
    t.columns = [col('Tag')];
    t.columns[0].repeat = 'rows';
    expect(
      await collect(
        extract(
          ['a'],
          t,
          () => false,
          () => {},
        ),
      ),
    ).toHaveLength(3);
  });
  it('rejects unsafe conversions and dates', () => {
    expect(convert('0001', col('.'))).toBe('0001');
    expect(() => convert('9007199254740993', { ...col('.'), type: 'number' })).toThrow(
      'TYPE_NUMBER',
    );
    expect(() => convert('2026-02-30', { ...col('.'), type: 'date' })).toThrow('TYPE_DATE');
    expect(() => convert(undefined, { ...col('.'), missing: 'error' })).toThrow('MISSING_VALUE');
    expect(convert('true', { ...col('.'), type: 'boolean' })).toBe(true);
  });
  it.each(['=SUM(1,1)', '+1', '-1', '@SUM(1)', ' \t=CMD()'])(
    'neutralizes CSV formulas %s',
    (value) => {
      expect(csvCell(value)).toBe('"\'' + value + '"');
    },
  );
  it('escapes CSV quotes and multiline strings', () => {
    expect(csvCell('a,"b"\nc')).toBe('"a,""b""\nc"');
  });
  it('validates versioned template JSON', () => {
    expect(() => validateTemplate({ version: 7 })).toThrow('TEMPLATE_INVALID');
    const t = defaultTemplate();
    t.columns = [col('.')];
    expect(validateTemplate(JSON.parse(JSON.stringify(t)))).toEqual(t);
    expect(() => validateTemplate({ ...t, sheetRows: 1048576 })).toThrow();
    expect(() => validateTemplate({ ...t, columns: [{ ...col('.'), type: 'formula' }] })).toThrow();
  });
  it('writes and reopens XLSX with safe strings, types, headers and split sheets', async () => {
    const t = defaultTemplate();
    t.sourceFile = false;
    t.columns = [col('id'), col('value')];
    t.sheetRows = 2;
    async function* rows(): AsyncGenerator<ExportRow> {
      for (let i = 0; i < 3; i++) yield { sheet: '結果', source: '', cells: ['001', '=1+2'] };
    }
    const bytes = await writeExcel(rows(), t, () => false);
    const book = new ExcelJS.Workbook();
    await book.xlsx.load(bytes as any);
    expect(book.worksheets).toHaveLength(2);
    expect(book.worksheets[0].getCell('A2').value).toBe('001');
    expect(book.worksheets[0].getCell('B2').value).toBe('=1+2');
    expect(book.worksheets[0].getCell('B2').type).toBe(ExcelJS.ValueType.String);
    expect(book.worksheets[0].views[0].state).toBe('frozen');
    expect(book.worksheets[0].rowCount).toBe(3);
  });
  it('searches names, attributes, text and paths', async () => {
    await parse(products);
    for (const [target, query] of [
      ['name', 'Product'],
      ['attributeValue', '001'],
      ['text', 'りんご'],
      ['path', 'Product[1]'],
    ] as const) {
      const r = await search(
        'a',
        { target, query, exact: false, caseSensitive: true },
        () => false,
        () => {},
      );
      expect(r.count).toBeGreaterThan(0);
    }
  });
});
