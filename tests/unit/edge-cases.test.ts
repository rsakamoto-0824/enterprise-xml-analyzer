import { it, expect } from 'vitest';
import { parseXml } from '../../src/parser/xmlStreamParser';
import { db, node, page, attributes, deleteFile, files } from '../../src/storage/database';
import { compareXml } from '../../src/diff/diffEngine';
import { defaultTemplate } from '../../src/export/templateManager';
import { extract, headers, valuesFor } from '../../src/export/extractionEngine';
import { writeExcel } from '../../src/export/excelWriter';
import type { Column, DiffOptions, ExportRow } from '../../src/models/types';
import { fragment } from '../../src/components/TreeView';
import { clearAll } from '../../src/storage/database';
import { validateTemplate } from '../../src/export/templateManager';
const options: DiffOptions = {
  mode: 'full',
  keyAttribute: 'id',
  whitespace: 'trim',
  comments: false,
  processingInstructions: false,
  ignoreOrder: false,
  verifyHashes: true,
};
const parse = (text: string, id = 'a') =>
  parseXml(new Blob([text]), id + '.xml', id, false, {
    cancelled: () => false,
    progress: () => {},
  });
it('reconstructs standalone fragments with inherited namespace bindings', async () => {
  await parse(
    '<r xmlns:x="urn:one" xmlns:y="urn:two"><x:n y:id="001" xml:lang="ja"><v/></x:n></r>',
  );
  const snippet = await fragment((await node('a', 2))!);
  await parse(snippet, 'b');
  expect((await node('b', 1))!.namespaceURI).toBe('urn:one');
  expect((await node('b', 2))!.namespaceURI).toBe('');
  expect((await attributes('b', 1)).find((a) => a.namespaceURI === 'urn:two')?.value).toBe('001');
});
it('deletes the whole work database and can import again', async () => {
  await parse('<r secret="private"/>');
  await (await db()).put('ExportTemplates', { name: 'last', template: defaultTemplate() });
  await clearAll();
  expect(await files()).toEqual([]);
  expect(await (await db()).count('ExportTemplates')).toBe(0);
  await parse('<new/>', 'b');
  expect((await node('b', 1))?.elementName).toBe('new');
});
it('validates output names and widths while accepting older version 1 templates', () => {
  const t = defaultTemplate();
  t.columns = [
    { path: '.', name: 'value', type: 'string', missing: 'blank', repeat: 'first', enabled: true },
  ];
  expect(() => validateTemplate({ ...t, outputName: '../outside' })).toThrow('TEMPLATE_INVALID');
  expect(() => validateTemplate({ ...t, columnWidth: 101 })).toThrow('TEMPLATE_INVALID');
  delete t.outputName;
  delete t.columnWidth;
  expect(validateTemplate(t).version).toBe(1);
});
const column = (path: string): Column => ({
  name: path,
  path,
  type: 'string',
  missing: 'blank',
  repeat: 'join',
  enabled: true,
});
async function rows(source: AsyncIterable<ExportRow>) {
  const values: ExportRow[] = [];
  for await (const value of source) values.push(value);
  return values;
}
it('rejects oversized XML tokens and cleans indexes', async () => {
  await expect(parse('<r>' + 'x'.repeat(1200000) + '</r>')).rejects.toThrow('TOKEN_LIMIT');
  expect(await (await db()).count('Nodes')).toBe(0);
  expect(await (await db()).count('SubtreeHashes')).toBe(0);
});
it('rejects a token closed just after the size limit and bounds aggregate attributes', async () => {
  await expect(parse('<r>' + 'x'.repeat(1048577) + '</r>')).rejects.toThrow('TOKEN_LIMIT');
  const many = Array.from({ length: 20 }, (_, i) => `a${i}="${'v'.repeat(60000)}"`).join(' ');
  await expect(parse('<r ' + many + '/>')).rejects.toThrow('TOKEN_LIMIT');
  expect(await files()).toHaveLength(0);
});
it('never includes confidential text in syntax errors', async () => {
  await expect(parse('<r><secret>PRIVATE_VALUE</wrong></r>')).rejects.not.toThrow('PRIVATE_VALUE');
});
it('handles default namespace changes and namespace-only attributes', async () => {
  const f = await parse('<r xmlns:x="urn:a" x:a="v"><n xmlns="urn:b"/></r>');
  expect(f.namespaces['urn:a']).toBe(0);
  expect(f.namespaces['urn:b']).toBe(1);
  expect((await attributes('a', 1)).some((a) => a.name === '{urn%3Aa}a')).toBe(true);
});
it('extracts namespace-aware child and attribute paths', async () => {
  await parse('<r xmlns:x="urn:a"><x:n x:id="001">abc</x:n></r>');
  const root = (await node('a', 1))!;
  expect(await valuesFor(root, '{urn%3Aa}n/@{urn%3Aa}id', 'a.xml')).toEqual(['001']);
});
it('adds parent references for separate repeat sheets', async () => {
  await parse('<r><n><v>A</v><v>B</v></n></r>');
  const t = defaultTemplate();
  t.recordPath = '/r/n';
  t.columns = [{ ...column('v'), repeat: 'sheet' }];
  const result = await rows(
    extract(
      ['a'],
      t,
      () => false,
      () => {},
    ),
  );
  expect(headers(t)).toEqual(['SourceFile', 'RecordPath', 'v']);
  expect(result[1].cells).toEqual(['a.xml', '/r[1]/n[1]', 'A']);
  expect(result[2].cells[2]).toBe('B');
});
it('extracts hierarchy, parent name and file metadata', async () => {
  await parse('<r><n>v</n></r>');
  const n = (await node('a', 2))!;
  expect(await valuesFor(n, '$level:0', 'a.xml')).toEqual(['r']);
  expect(await valuesFor(n, '../$name', 'a.xml')).toEqual(['r']);
  expect(await valuesFor(n, '$file', 'a.xml')).toEqual(['a.xml']);
});
it('supports OR, missing attribute and empty filters', async () => {
  await parse('<r><n id="1">a</n><n></n><n id="3">b</n></r>');
  const t = defaultTemplate();
  t.recordPath = '/r/n';
  t.columns = [column('@id')];
  t.filters = [
    { path: '@id', op: 'equals', value: '1' },
    { path: '.', op: 'empty', value: '' },
  ];
  t.logic = 'OR';
  expect(
    await rows(
      extract(
        ['a'],
        t,
        () => false,
        () => {},
      ),
    ),
  ).toHaveLength(2);
});
it('deleting one file removes only related comparisons and all its segments', async () => {
  await parse('<r a="b">text</r>', 'a');
  await parse('<r/>', 'b');
  const c = await compareXml(
    'a',
    'b',
    options,
    () => false,
    () => {},
  );
  expect(c.rows).toBeGreaterThan(0);
  await deleteFile('a');
  expect(await (await db()).count('TextSegments')).toBe(0);
  expect(await (await db()).count('Comparisons')).toBe(0);
  expect(await (await db()).count('DiffResults')).toBe(0);
  expect(await page('b')).toHaveLength(1);
  expect(await files()).toHaveLength(1);
});
it('accepts empty string as a unique key and treats duplicate empties as ambiguous', async () => {
  await parse('<r><n id=""/><n id=""/></r>', 'a');
  await parse('<r><n id=""/><n id=""/></r>', 'b');
  const c = await compareXml(
    'a',
    'b',
    options,
    () => false,
    () => {},
  );
  expect(c.counts.AMBIGUOUS).toBe(2);
});
it('respects whitespace, comment and instruction options', async () => {
  await parse('<r> a <!--x--><?p x?></r>', 'a');
  await parse('<r>a<!--y--><?p y?></r>', 'b');
  const c = await compareXml(
    'a',
    'b',
    options,
    () => false,
    () => {},
  );
  expect(c.counts.MODIFIED).toBe(0);
  const strict = await compareXml(
    'a',
    'b',
    { ...options, whitespace: 'preserve', comments: true, processingInstructions: true },
    () => false,
    () => {},
  );
  expect(strict.counts.MODIFIED).toBe(3);
});
it('rejects too much XLSX data before creating an unbounded workbook', async () => {
  const t = defaultTemplate();
  t.sourceFile = false;
  t.columns = [column('.')];
  async function* input(): AsyncGenerator<ExportRow> {
    for (let i = 0; i < 600; i++) yield { sheet: 's', source: '', cells: ['x'.repeat(32767)] };
  }
  await expect(writeExcel(input(), t, () => false)).rejects.toThrow('XLSX_LIMIT');
});
