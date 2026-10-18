import { attributes, children, db, directText, fileRange } from '../storage/database';
import {
  LIMITS,
  type Comparison,
  type DiffOptions,
  type DiffRow,
  type DiffType,
  type XmlNode,
} from '../models/types';
import { matchSiblings, type Match } from './nodeMatcher';
export const diffTypes: DiffType[] = [
  'ADDED',
  'REMOVED',
  'MODIFIED',
  'MOVED',
  'UNCHANGED',
  'AMBIGUOUS',
];
export async function compareXml(
  a: string,
  b: string,
  options: DiffOptions,
  cancelled: () => boolean,
  progress: (v: unknown) => void,
): Promise<Comparison> {
  const comparisonId = crypto.randomUUID(),
    start = performance.now();
  const result: Comparison = {
    comparisonId,
    a,
    b,
    options,
    status: 'COMPARING',
    counts: { ADDED: 0, REMOVED: 0, MODIFIED: 0, MOVED: 0, UNCHANGED: 0, AMBIGUOUS: 0 },
    rows: 0,
    durationMs: 0,
  };
  await (await db()).put('Comparisons', result);
  let buffer: DiffRow[] = [];
  const check = () => {
    if (cancelled()) throw new Error('CANCELLED: 比較を中断しました。');
    if (performance.now() - start > LIMITS.operationMs)
      throw new Error('TIME_LIMIT: 比較が10分を超えました。対象を小さくしてください。');
  };
  async function flush() {
    if (!buffer.length) return;
    const tx = (await db()).transaction('DiffResults', 'readwrite');
    for (const row of buffer) void tx.store.put(row);
    await tx.done;
    buffer = [];
    progress({ rows: result.rows, counts: result.counts });
  }
  async function record(
    type: DiffType,
    m: Match,
    details: string,
    oldValue = '',
    newValue = '',
    covered = 1,
  ) {
    let attributeName = '';
    if (type === 'MODIFIED' && m.a?.nodeType === 'element' && m.b?.nodeType === 'element') {
      const left = new Map(
        (await attributes(m.a.fileId, m.a.nodeId))
          .filter((v) => v.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
          .map((v) => [v.name, v.value]),
      );
      const right = new Map(
        (await attributes(m.b.fileId, m.b.nodeId))
          .filter((v) => v.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
          .map((v) => [v.name, v.value]),
      );
      attributeName = [...new Set([...left.keys(), ...right.keys()])]
        .filter((name) =>
          options.mode === 'structure'
            ? left.has(name) !== right.has(name)
            : left.get(name) !== right.get(name),
        )
        .join(', ');
    }
    const n = m.b ?? m.a!;
    result.counts[type] += covered;
    buffer.push({
      comparisonId,
      seq: ++result.rows,
      type,
      a: m.a?.nodeId ?? null,
      b: m.b?.nodeId ?? null,
      path: n.path,
      name: n.elementName,
      attributeName,
      oldValue: oldValue.slice(0, 4000),
      newValue: newValue.slice(0, 4000),
      confidence: m.confidence,
      details,
      covered,
    });
    if (buffer.length >= 500) await flush();
  }
  async function filtered(file: string, parent: number, after: number) {
    const list = await children(file, parent, after, LIMITS.siblingCandidates);
    const accepted: XmlNode[] = [];
    for (const n of list) {
      if (
        (n.nodeType === 'comment' && !options.comments) ||
        (n.nodeType === 'pi' && !options.processingInstructions)
      )
        continue;
      if (options.mode === 'structure' && (n.nodeType === 'text' || n.nodeType === 'cdata'))
        continue;
      if (
        options.whitespace === 'trim' &&
        (n.nodeType === 'text' || n.nodeType === 'cdata') &&
        !(await directText(n, LIMITS.tokenChars)).trim()
      )
        continue;
      accepted.push(n);
    }
    return { list, accepted };
  }
  async function signature(n: XmlNode) {
    const attrs = (await attributes(n.fileId, n.nodeId))
      .filter((v) => v.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
      .sort((x, y) => x.name.localeCompare(y.name));
    const text = n.nodeType === 'element' ? '' : await directText(n, LIMITS.tokenChars);
    return JSON.stringify([
      n.namespaceURI,
      n.localName,
      n.nodeType,
      options.mode === 'structure' ? attrs.map((v) => v.name) : attrs.map((v) => [v.name, v.value]),
      options.mode === 'structure' ? '' : options.whitespace === 'trim' ? text.trim() : text,
    ]);
  }
  const parents: { a: number; b: number; afterA: number; afterB: number }[] = [
    { a: 0, b: 0, afterA: 0, afterB: 0 },
  ];
  try {
    while (parents.length) {
      check();
      const parent = parents.pop()!;
      const [left, right] = await Promise.all([
        filtered(a, parent.a, parent.afterA),
        filtered(b, parent.b, parent.afterB),
      ]);
      const paginated =
        left.list.length === LIMITS.siblingCandidates ||
        right.list.length === LIMITS.siblingCandidates ||
        parent.afterA > 0 ||
        parent.afterB > 0;
      if (
        left.list.length === LIMITS.siblingCandidates ||
        right.list.length === LIMITS.siblingCandidates
      )
        parents.push({
          ...parent,
          afterA: left.list.at(-1)?.nodeId ?? Number.MAX_SAFE_INTEGER - 1,
          afterB: right.list.at(-1)?.nodeId ?? Number.MAX_SAFE_INTEGER - 1,
        });
      const keys = new Map<string, string>();
      if (options.keyAttribute && !paginated)
        for (const n of [...left.accepted, ...right.accepted]) {
          if (n.nodeType !== 'element') continue;
          const attr = (await attributes(n.fileId, n.nodeId)).find(
            (v) => v.name === options.keyAttribute,
          );
          if (attr) keys.set(`${n.fileId}:${n.nodeId}`, attr.value);
        }
      const orderA = new Map(left.accepted.map((n, index) => [n.nodeId, index]));
      const orderB = new Map(right.accepted.map((n, index) => [n.nodeId, index]));
      const matches = matchSiblings(
        left.accepted,
        right.accepted,
        keys,
        options.mode === 'structure',
        options.ignoreOrder,
      );
      for (const m of matches) {
        check();
        if (!m.a || !m.b) {
          await record(
            m.a ? 'REMOVED' : 'ADDED',
            m,
            'サブツリー単位（coveredは文書ノード数）',
            '',
            '',
            (m.a ?? m.b!).subtreeEndOrder - (m.a ?? m.b!).nodeId + 1,
          );
          continue;
        }
        const x = m.a,
          y = m.b;
        const hashUsable =
          (options.mode === 'structure' &&
            !options.comments &&
            !options.processingInstructions &&
            !options.ignoreOrder) ||
          (options.mode === 'full' &&
            options.whitespace === 'preserve' &&
            options.comments &&
            options.processingInstructions &&
            !options.ignoreOrder);
        const equalHash =
          options.mode === 'structure'
            ? x.structureHash === y.structureHash
            : x.subtreeHash === y.subtreeHash;
        const uncertain = m.confidence === 'ambiguous' || paginated;
        if (uncertain)
          await record(
            'AMBIGUOUS',
            m,
            paginated
              ? '兄弟数が候補上限を超えるため、ページ内の暫定対応'
              : '識別キーの重複、または同名要素の対応が一意ではありません',
          );
        else if (hashUsable && equalHash && !options.verifyHashes && !m.moved) {
          await record(
            'UNCHANGED',
            m,
            'SHA-256一致によりサブツリーを省略（厳密照合で再確認可）',
            '',
            '',
            Math.max(x.subtreeEndOrder - x.nodeId + 1, y.subtreeEndOrder - y.nodeId + 1),
          );
          continue;
        } else {
          const [sx, sy] = await Promise.all([signature(x), signature(y)]);
          const reordered =
            options.mode !== 'value' &&
            !options.ignoreOrder &&
            orderA.get(x.nodeId) !== orderB.get(y.nodeId);
          await record(
            m.moved && options.mode !== 'value'
              ? 'MOVED'
              : sx !== sy || reordered
                ? 'MODIFIED'
                : 'UNCHANGED',
            m,
            m.moved
              ? '同じ親の一意キーで順序変更を確認'
              : reordered
                ? '要素順序が異なります'
                : '対応ノードの直接値を比較',
            sx,
            sy,
          );
        }
        if (x.nodeType === 'element' && y.nodeType === 'element')
          parents.push({ a: x.nodeId, b: y.nodeId, afterA: 0, afterB: 0 });
        if (parents.length > 100000)
          throw new Error('CANDIDATE_LIMIT: 比較の待ち行列が上限を超えました。');
      }
    }
    await flush();
    result.status = 'COMPLETED';
    result.durationMs = performance.now() - start;
    await (await db()).put('Comparisons', result);
    return result;
  } catch (error) {
    await (await db()).delete('DiffResults', fileRange(comparisonId));
    await (await db()).delete('Comparisons', comparisonId);
    throw error;
  }
}
