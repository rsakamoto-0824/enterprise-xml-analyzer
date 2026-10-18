import { page, attributes, directText, instancePath } from '../storage/database';
export interface SearchOptions {
  query: string;
  target: 'name' | 'attributeName' | 'attributeValue' | 'text' | 'path';
  exact: boolean;
  caseSensitive: boolean;
}
export async function search(
  fileId: string,
  options: SearchOptions,
  cancelled: () => boolean,
  progress: (n: number) => void,
) {
  const ids: number[] = [];
  let count = 0,
    after = 0,
    scanned = 0;
  const normalize = (s: string) => (options.caseSensitive ? s : s.toLocaleLowerCase());
  const q = normalize(options.query);
  while (true) {
    const batch = await page(fileId, after);
    if (!batch.length) break;
    for (const n of batch) {
      if (cancelled()) throw new Error('CANCELLED: 検索を中断しました。');
      if (n.nodeType !== 'element') continue;
      const values =
        options.target === 'name'
          ? [n.elementName]
          : options.target === 'path'
            ? [await instancePath(n)]
            : options.target === 'text'
              ? [await directText(n)]
              : (await attributes(fileId, n.nodeId)).map((a) =>
                  options.target === 'attributeName' ? a.name : a.value,
                );
      if (values.some((v) => (options.exact ? normalize(v) === q : normalize(v).includes(q)))) {
        count++;
        if (ids.length < 10000) ids.push(n.nodeId);
      }
    }
    after = batch.at(-1)!.nodeId;
    scanned += batch.length;
    progress(scanned);
  }
  return { ids, count, truncated: count > ids.length };
}
