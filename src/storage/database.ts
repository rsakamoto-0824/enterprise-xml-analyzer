import { deleteDB, openDB, type IDBPDatabase } from 'idb';
import {
  LIMITS,
  type XmlNode,
  type Attribute,
  type TextSegment,
  type XmlFile,
} from '../models/types';
export const DATABASE = 'enterprise-xml-analyzer-v1';
export const STORES = [
  'Files',
  'Nodes',
  'Attributes',
  'TextSegments',
  'NodeIndexes',
  'SubtreeHashes',
  'Comparisons',
  'DiffResults',
  'ExportTemplates',
] as const;
let connection: Promise<IDBPDatabase> | undefined;
export function db() {
  return (connection ??= openDB(DATABASE, 1, {
    blocking() {
      const previous = connection;
      connection = undefined;
      void previous?.then((database) => database.close());
    },
    terminated() {
      connection = undefined;
    },
    upgrade(database) {
      database.createObjectStore('Files', { keyPath: 'fileId' });
      const nodes = database.createObjectStore('Nodes', { keyPath: ['fileId', 'nodeId'] });
      nodes.createIndex('parent', ['fileId', 'parentId', 'nodeId']);
      nodes.createIndex('path', ['fileId', 'path', 'nodeId']);
      nodes.createIndex('name', ['fileId', 'elementName', 'nodeId']);
      database.createObjectStore('Attributes', { keyPath: ['fileId', 'nodeId', 'name'] });
      database.createObjectStore('TextSegments', { keyPath: ['fileId', 'nodeId', 'segment'] });
      database.createObjectStore('NodeIndexes', { keyPath: ['fileId', 'nodeId'] });
      database.createObjectStore('SubtreeHashes', { keyPath: ['fileId', 'nodeId'] });
      database.createObjectStore('Comparisons', { keyPath: 'comparisonId' });
      const differences = database.createObjectStore('DiffResults', {
        keyPath: ['comparisonId', 'seq'],
      });
      differences.createIndex('type', ['comparisonId', 'type', 'seq']);
      database.createObjectStore('ExportTemplates', { keyPath: 'name' });
    },
  }));
}
export const fileRange = (id: string) => IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]);
export async function node(file: string, id: number): Promise<XmlNode | undefined> {
  return (await db()).get('Nodes', [file, id]);
}
export async function files(): Promise<XmlFile[]> {
  return ((await (await db()).getAll('Files')) as XmlFile[]).sort((a, b) => a.created - b.created);
}
export async function children(
  file: string,
  parent: number,
  after = 0,
  limit: number = LIMITS.queryPage,
): Promise<XmlNode[]> {
  return (await db()).getAllFromIndex(
    'Nodes',
    'parent',
    IDBKeyRange.bound([file, parent, after], [file, parent, Number.MAX_SAFE_INTEGER], true),
    limit,
  );
}
export async function page(
  file: string,
  after = 0,
  end = Number.MAX_SAFE_INTEGER,
  limit: number = LIMITS.queryPage,
): Promise<XmlNode[]> {
  return (await db()).getAll('Nodes', IDBKeyRange.bound([file, after], [file, end], true), limit);
}
export async function attributes(file: string, id: number): Promise<Attribute[]> {
  return (await db()).getAll('Attributes', IDBKeyRange.bound([file, id, ''], [file, id, '\uffff']));
}
export async function textValue(
  file: string,
  id: number,
  max: number = LIMITS.textPreview,
): Promise<string> {
  const database = await db();
  let result = '';
  for await (const cursor of database
    .transaction('TextSegments')
    .store.iterate(IDBKeyRange.bound([file, id, 0], [file, id, Number.MAX_SAFE_INTEGER]))) {
    result += (cursor.value as TextSegment).value;
    if (result.length > max)
      throw new Error('TEXT_LIMIT: テキストの取得上限を超えました。小さい項目を選んでください。');
  }
  return result;
}
export async function directText(n: XmlNode, max: number = LIMITS.textPreview): Promise<string> {
  if (n.nodeType !== 'element') return textValue(n.fileId, n.nodeId, max);
  let result = '',
    after = 0;
  while (true) {
    const batch = await children(n.fileId, n.nodeId, after);
    if (!batch.length) return result;
    for (const child of batch)
      if (child.nodeType === 'text' || child.nodeType === 'cdata') {
        result += await textValue(n.fileId, child.nodeId, max);
        if (result.length > max) throw new Error('TEXT_LIMIT: セル取得上限を超えました。');
      }
    after = batch.at(-1)!.nodeId;
  }
}
export async function instancePath(n: XmlNode): Promise<string> {
  const parts: string[] = [];
  let current: XmlNode | undefined = n;
  while (current) {
    parts.push(`${current.elementName}[${current.siblingIndex}]`);
    current = current.parentId ? await node(n.fileId, current.parentId) : undefined;
  }
  return '/' + parts.reverse().join('/');
}
export async function saveBatch(nodes: XmlNode[], attrs: Attribute[], texts: TextSegment[]) {
  const tx = (await db()).transaction(
    ['Nodes', 'Attributes', 'TextSegments', 'NodeIndexes', 'SubtreeHashes'],
    'readwrite',
  );
  for (const n of nodes) {
    void tx.objectStore('Nodes').put(n);
    void tx
      .objectStore('NodeIndexes')
      .put({ fileId: n.fileId, nodeId: n.nodeId, parentId: n.parentId, end: n.subtreeEndOrder });
    void tx
      .objectStore('SubtreeHashes')
      .put({ fileId: n.fileId, nodeId: n.nodeId, full: n.subtreeHash, structure: n.structureHash });
  }
  for (const a of attrs) void tx.objectStore('Attributes').put(a);
  for (const t of texts) void tx.objectStore('TextSegments').put(t);
  await tx.done;
}
export async function deleteFile(id: string) {
  const database = await db();
  for (const name of ['Nodes', 'Attributes', 'TextSegments', 'NodeIndexes', 'SubtreeHashes']) {
    const tx = database.transaction(name, 'readwrite');
    await tx.store.delete(IDBKeyRange.bound([id], [id, []]));
    await tx.done;
  }
  for (const c of await database.getAll('Comparisons'))
    if (c.a === id || c.b === id) {
      await database.delete('DiffResults', fileRange(c.comparisonId));
      await database.delete('Comparisons', c.comparisonId);
    }
  await database.delete('Files', id);
}
export async function startupCleanup() {
  let removed = 0;
  for (const f of await files())
    if (!f.persistent || f.status !== 'READY') {
      await deleteFile(f.fileId);
      removed++;
    }
  for (const c of await (await db()).getAll('Comparisons'))
    if (c.status !== 'COMPLETED') {
      await (await db()).delete('DiffResults', fileRange(c.comparisonId));
      await (await db()).delete('Comparisons', c.comparisonId);
    }
  return removed;
}
export async function clearAll() {
  // Dropping the dedicated database avoids deleting millions of indexed records one by one.
  const previous = connection;
  connection = undefined;
  (await previous)?.close();
  await deleteDB(DATABASE);
}
