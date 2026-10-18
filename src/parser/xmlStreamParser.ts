import { SaxesParser } from 'saxes';
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';
import { db, deleteFile, saveBatch } from '../storage/database';
import {
  expandedName,
  LIMITS,
  type XmlFile,
  type XmlNode,
  type Attribute,
  type TextSegment,
  type NodeKind,
} from '../models/types';

type Hash = ReturnType<typeof sha256.create>;
interface Frame {
  node: XmlNode;
  names: Map<string, number>;
  structure: Hash;
  full: Hash;
}
const encoder = new TextEncoder();
function feed(hash: Hash, value: unknown) {
  const s = JSON.stringify(value);
  hash.update(encoder.encode(`${s.length}:${s}`));
}
export interface ParseHooks {
  cancelled: () => boolean;
  progress: (bytes: number, total: number, elements: number) => void;
  chunkBytes?: number;
}
export async function detectEncoding(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.slice(0, 512).arrayBuffer());
  let encoding =
    bytes[0] === 0xff && bytes[1] === 0xfe
      ? 'utf-16le'
      : bytes[0] === 0xfe && bytes[1] === 0xff
        ? 'utf-16be'
        : bytes[0] === 0x3c && bytes[1] === 0
          ? 'utf-16le'
          : bytes[0] === 0 && bytes[1] === 0x3c
            ? 'utf-16be'
            : 'utf-8';
  const header = new TextDecoder(encoding).decode(bytes);
  const declared = /^\uFEFF?<\?xml\s[^?]*encoding\s*=\s*['"]([^'"]+)['"]/i
    .exec(header)?.[1]
    .toLowerCase();
  if (declared && !['utf-8', 'utf-16', 'utf-16le', 'utf-16be'].includes(declared))
    throw new Error('ENCODING: UTF-8 または UTF-16 に変換してください。');
  if (declared && declared !== 'utf-16' && declared !== encoding)
    throw new Error('ENCODING: XML宣言と実際の文字コードが一致しません。');
  if (declared === 'utf-16' && encoding === 'utf-8')
    throw new Error('ENCODING: UTF-16 のバイト形式を判定できません。');
  return encoding;
}
export async function parseXml(
  blob: Blob,
  name: string,
  fileId: string,
  persistent: boolean,
  hooks: ParseHooks,
): Promise<XmlFile> {
  const start = performance.now();
  let indexingMs = 0;
  const info: XmlFile = {
    fileId,
    name,
    size: blob.size,
    encoding: '',
    elements: 0,
    attributes: 0,
    nodes: 0,
    maxDepth: 0,
    namespaces: Object.create(null),
    elementCounts: Object.create(null),
    depthCounts: Object.create(null),
    childCounts: Object.create(null),
    parseMs: 0,
    indexMs: 0,
    status: 'PARSING',
    persistent,
    created: Date.now(),
  };
  await (await db()).put('Files', info);
  const parser = new SaxesParser({ xmlns: true, position: true });
  const stack: Frame[] = [];
  const topNames = new Map<string, number>();
  let nodes: XmlNode[] = [],
    attrs: Attribute[] = [],
    texts: TextSegment[] = [];
  const increment = (map: Record<string, number>, key: string) => {
    if (Object.keys(map).length > 10000 && !(key in map))
      throw new Error('VARIETY_LIMIT: 名前の種類が安全上限を超えました。');
    map[key] = (map[key] ?? 0) + 1;
  };
  function create(
    kind: NodeKind,
    elementName: string,
    uri = '',
    local = elementName,
    prefix = '',
  ): XmlNode {
    const parent = stack.at(-1),
      names = parent?.names ?? topNames;
    const identity = expandedName(uri, local),
      sibling = (names.get(identity) ?? 0) + 1;
    names.set(identity, sibling);
    if (names.size > 10000) throw new Error('VARIETY_LIMIT: 兄弟要素名の種類が多すぎます。');
    if (parent) parent.node.childCount++;
    const depth = stack.length;
    if (depth > LIMITS.maxDepth) throw new Error('DEPTH_LIMIT: 階層が256を超えました。');
    const id = ++info.nodes;
    return {
      fileId,
      nodeId: id,
      parentId: parent?.node.nodeId ?? 0,
      nodeType: kind,
      elementName,
      namespaceURI: uri,
      localName: local,
      prefix,
      depth,
      siblingIndex: sibling,
      documentOrder: id,
      subtreeEndOrder: id,
      childCount: 0,
      attributeCount: 0,
      subtreeHash: '',
      structureHash: '',
      sourceOffset: null,
      textReference: 0,
      path: (parent?.node.path ?? '') + '/' + identity,
      position: parent?.node.childCount ?? id,
    };
  }
  parser.on('error', () => {
    throw new Error(`XML_SYNTAX: XML構文エラー（${parser.line}行 ${parser.column}列）。`);
  });
  parser.on('doctype', () => {
    throw new Error('DTD_FORBIDDEN: DTDと独自エンティティは使用できません。');
  });
  let tagCharacters = 0;
  parser.on('opentagstart', (tag) => {
    tagCharacters = tag.name.length;
  });
  parser.on('attribute', (attribute) => {
    tagCharacters += attribute.name.length + attribute.value.length;
    if (tagCharacters > LIMITS.tokenChars)
      throw new Error('TOKEN_LIMIT: タグと属性の合計が1Mi文字を超えました。');
  });
  parser.on('opentag', (tag) => {
    const n = create('element', tag.name, tag.uri, tag.local, tag.prefix);
    const a = Object.values(tag.attributes).map((v) => ({
      fileId,
      nodeId: n.nodeId,
      name: expandedName(v.uri, v.local),
      localName: v.local,
      namespaceURI: v.uri,
      value: v.value,
    }));
    n.attributeCount = a.length;
    info.attributes += a.length;
    attrs.push(...a);
    info.elements++;
    info.maxDepth = Math.max(info.maxDepth, n.depth);
    increment(info.namespaces, tag.uri || '(なし)');
    increment(info.elementCounts, expandedName(tag.uri, tag.local));
    increment(info.depthCounts, String(n.depth));
    for (const attr of a) {
      const uri =
        attr.namespaceURI === 'http://www.w3.org/2000/xmlns/' ? attr.value : attr.namespaceURI;
      if (uri && !(uri in info.namespaces)) info.namespaces[uri] = 0;
    }
    const frame: Frame = {
      node: n,
      names: new Map(),
      structure: sha256.create(),
      full: sha256.create(),
    };
    const ordinary = a
      .filter((v) => v.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
      .sort((a, b) => a.name.localeCompare(b.name));
    feed(frame.structure, [n.namespaceURI, n.localName, ordinary.map((v) => v.name)]);
    feed(frame.full, [n.namespaceURI, n.localName, ordinary.map((v) => [v.name, v.value])]);
    stack.push(frame);
  });
  parser.on('closetag', () => {
    const f = stack.pop()!;
    f.node.subtreeEndOrder = info.nodes;
    f.node.structureHash = bytesToHex(f.structure.digest());
    f.node.subtreeHash = bytesToHex(f.full.digest());
    increment(info.childCounts, String(f.node.childCount));
    nodes.push(f.node);
    const parent = stack.at(-1);
    if (parent) {
      feed(parent.structure, f.node.structureHash);
      feed(parent.full, ['element', f.node.subtreeHash]);
    }
  });
  function leaf(kind: NodeKind, value: string, label = '#' + kind) {
    if (!value.length) return;
    if (value.length > LIMITS.tokenChars)
      throw new Error('TOKEN_LIMIT: 単一テキスト・コメント等が1Mi文字を超えました。');
    const n = create(kind, label);
    n.textReference = Math.ceil(value.length / LIMITS.chunkBytes);
    const h = sha256.create();
    feed(h, [kind, label, value]);
    n.subtreeHash = bytesToHex(h.digest());
    n.structureHash = bytesToHex(sha256(encoder.encode(kind)));
    for (let offset = 0, segment = 0; offset < value.length; offset += LIMITS.chunkBytes, segment++)
      texts.push({
        fileId,
        nodeId: n.nodeId,
        segment,
        value: value.slice(offset, offset + LIMITS.chunkBytes),
      });
    nodes.push(n);
    const parent = stack.at(-1);
    if (parent) feed(parent.full, [kind, n.subtreeHash]);
  }
  parser.on('text', (v) => leaf('text', v));
  parser.on('cdata', (v) => leaf('cdata', v));
  parser.on('comment', (v) => leaf('comment', v));
  parser.on('processinginstruction', (v) => leaf('pi', v.body, v.target));
  async function flush() {
    if (!nodes.length && !attrs.length && !texts.length) return;
    const before = performance.now();
    await saveBatch(nodes, attrs, texts);
    indexingMs += performance.now() - before;
    nodes = [];
    attrs = [];
    texts = [];
  }
  try {
    info.encoding = await detectEncoding(blob);
    const decoder = new TextDecoder(info.encoding, { fatal: true });
    const chunk = hooks.chunkBytes ?? LIMITS.chunkBytes;
    for (let offset = 0; offset < blob.size; offset += chunk) {
      if (hooks.cancelled()) throw new Error('CANCELLED: 解析を中断しました。');
      const data = await blob.slice(offset, offset + chunk).arrayBuffer();
      parser.write(decoder.decode(data, { stream: true }));
      // Saxes keeps incomplete tokens between writes. Bound those buffers before the next read.
      for (const value of Object.values(parser))
        if (typeof value === 'string' && value.length > LIMITS.tokenChars)
          throw new Error('TOKEN_LIMIT: 単一のタグ・テキストが1Mi文字を超えました。');
      await flush();
      hooks.progress(Math.min(offset + chunk, blob.size), blob.size, info.elements);
    }
    parser.write(decoder.decode()).close();
    await flush();
    if (hooks.cancelled()) throw new Error('CANCELLED: 解析を中断しました。');
    info.status = 'READY';
    info.indexMs = indexingMs;
    info.parseMs = performance.now() - start - indexingMs;
    await (await db()).put('Files', info);
    return info;
  } catch (error) {
    await deleteFile(fileId);
    if (error instanceof DOMException && error.name === 'QuotaExceededError')
      throw new Error(
        'STORAGE_FULL: ブラウザ保存領域が不足しています。作業データを削除してください。',
      );
    if (error instanceof TypeError)
      throw new Error('ENCODING: 不正な文字列、またはファイル読込エラーです。');
    throw error;
  }
}
