import type { XmlNode } from '../models/types';
export interface Match {
  a?: XmlNode;
  b?: XmlNode;
  confidence: 'key' | 'hash' | 'unique' | 'order' | 'ambiguous';
  moved?: boolean;
}
export function matchSiblings(
  a: XmlNode[],
  b: XmlNode[],
  keys: Map<string, string>,
  structure: boolean,
  ignoreOrder: boolean,
): Match[] {
  const identity = (n: XmlNode) => JSON.stringify([n.nodeType, n.namespaceURI, n.localName]);
  const key = (n: XmlNode) => keys.get(`${n.fileId}:${n.nodeId}`);
  const orderA = new Map(a.map((n, index) => [n.nodeId, index]));
  const orderB = new Map(b.map((n, index) => [n.nodeId, index]));
  const matches: Match[] = [],
    usedA = new Set<number>(),
    usedB = new Set<number>();
  const group = (list: XmlNode[], selector: (n: XmlNode) => string | undefined) => {
    const result = new Map<string, XmlNode[]>();
    for (const n of list) {
      const k = selector(n);
      if (k !== undefined) {
        const items = result.get(k) ?? [];
        items.push(n);
        result.set(k, items);
      }
    }
    return result;
  };
  const bind = (left: XmlNode, right: XmlNode, confidence: Match['confidence']) => {
    usedA.add(left.nodeId);
    usedB.add(right.nodeId);
    matches.push({
      a: left,
      b: right,
      confidence,
      moved:
        !ignoreOrder &&
        confidence === 'key' &&
        orderA.get(left.nodeId) !== orderB.get(right.nodeId),
    });
  };
  const ka = group(a, (n) => (key(n) !== undefined ? identity(n) + '|' + key(n) : undefined)),
    kb = group(b, (n) => (key(n) !== undefined ? identity(n) + '|' + key(n) : undefined));
  for (const [k, aa] of ka) {
    const bb = kb.get(k);
    if (!bb) continue;
    if (aa.length === 1 && bb.length === 1) bind(aa[0], bb[0], 'key');
    else for (let i = 0; i < Math.min(aa.length, bb.length); i++) bind(aa[i], bb[i], 'ambiguous');
  }
  const hash = (n: XmlNode) => identity(n) + '|' + (structure ? n.structureHash : n.subtreeHash);
  const ha = group(
      a.filter((n) => !usedA.has(n.nodeId)),
      hash,
    ),
    hb = group(
      b.filter((n) => !usedB.has(n.nodeId)),
      hash,
    );
  for (const [k, aa] of ha) {
    const bb = hb.get(k);
    if (aa.length === 1 && bb?.length === 1 && key(aa[0]) === undefined && key(bb[0]) === undefined)
      bind(aa[0], bb[0], 'hash');
  }
  const na = group(
      a.filter((n) => !usedA.has(n.nodeId)),
      identity,
    ),
    nb = group(
      b.filter((n) => !usedB.has(n.nodeId)),
      identity,
    );
  for (const [k, aa] of na) {
    const bb = nb.get(k) ?? [];
    const left = aa.filter((n) => key(n) === undefined),
      right = bb.filter((n) => key(n) === undefined);
    for (let i = 0; i < Math.min(left.length, right.length); i++)
      bind(
        left[i],
        right[i],
        left.length === 1 && right.length === 1
          ? 'unique'
          : hash(left[i]) === hash(right[i])
            ? 'order'
            : 'ambiguous',
      );
  }
  for (const n of a) if (!usedA.has(n.nodeId)) matches.push({ a: n, confidence: 'unique' });
  for (const n of b) if (!usedB.has(n.nodeId)) matches.push({ b: n, confidence: 'unique' });
  return matches;
}
