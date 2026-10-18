import { attributes, children, directText, instancePath, node } from '../storage/database';
import { LIMITS, type XmlNode } from '../models/types';
import { button, el, table } from './dom';
import { VirtualList } from './VirtualList';
type TreeEntry = XmlNode & { more?: { parent: XmlNode; after: number } };
export class TreeView {
  readonly element = el('div', 'split');
  readonly detail = el('aside', 'detail');
  private list: VirtualList<TreeEntry>;
  private visible: TreeEntry[] = [];
  private expanded = new Set<number>();
  private selected = 0;
  private fileId = '';
  constructor(
    private onSelect: (n: XmlNode) => void,
    private onRecord: (n: XmlNode) => void,
    private report: (e: unknown) => void,
  ) {
    this.list = new VirtualList((n) => this.row(n));
    const pane = el('div', 'tree-pane');
    pane.append(this.list.element);
    this.detail.append(el('p', 'muted', '要素を選択すると、属性やテキストを確認できます。'));
    this.element.append(pane, this.detail);
  }
  private row(n: TreeEntry) {
    const row = el('div', 'tree-row' + (n.nodeId === this.selected ? ' selected' : ''));
    row.style.paddingLeft = `${12 + Math.min(n.depth, 30) * 20}px`;
    row.setAttribute('role', 'treeitem');
    row.setAttribute('aria-level', String(n.depth + 1));
    if (n.more) {
      row.append(
        button('次の250件を表示…', () =>
          this.loadChildren(n.more!.parent, n.more!.after).catch(this.report),
        ),
      );
      return row;
    }
    const toggle = button(
      n.childCount ? (this.expanded.has(n.nodeId) ? '▾' : '▸') : '·',
      () => this.toggle(n).catch(this.report),
      'toggle',
    );
    toggle.setAttribute(
      'aria-label',
      `${n.elementName} ${this.expanded.has(n.nodeId) ? '折りたたむ' : '展開'}`,
    );
    if (n.childCount) row.setAttribute('aria-expanded', String(this.expanded.has(n.nodeId)));
    else toggle.disabled = true;
    const choose = button(n.elementName, () => this.selectNode(n).catch(this.report), 'node-name');
    row.append(
      toggle,
      choose,
      el(
        'span',
        'node-meta',
        n.nodeType === 'element'
          ? `${n.attributeCount ? '@' + n.attributeCount + '  ' : ''}${n.childCount ? '子 ' + n.childCount : ''}`
          : n.nodeType,
      ),
    );
    row.onkeydown = (e) => {
      if (e.key === 'ArrowRight' && !this.expanded.has(n.nodeId))
        void this.toggle(n).catch(this.report);
      if (e.key === 'ArrowLeft' && this.expanded.has(n.nodeId))
        void this.toggle(n).catch(this.report);
    };
    return row;
  }
  async load(fileId: string) {
    if (this.fileId !== fileId) {
      this.selected = 0;
      this.detail.replaceChildren(
        el('p', 'muted', '要素を選択すると、属性やテキストを確認できます。'),
      );
    }
    this.fileId = fileId;
    this.expanded.clear();
    this.visible = await children(fileId, 0, 0, 500);
    this.list.element.setAttribute('role', 'tree');
    for (let depth = 0; depth < 2; depth++)
      for (const n of [...this.visible])
        if (!n.more && n.depth === depth && n.childCount && this.visible.length < 1000)
          await this.toggle(n, false);
    this.list.set(this.visible);
  }
  private async toggle(n: XmlNode, paint = true) {
    const index = this.visible.findIndex((v) => v.nodeId === n.nodeId);
    if (index < 0) return;
    if (this.expanded.has(n.nodeId)) {
      let end = index + 1;
      while (end < this.visible.length && this.visible[end].depth > n.depth) {
        this.expanded.delete(this.visible[end].nodeId);
        end++;
      }
      this.visible.splice(index + 1, end - index - 1);
      this.expanded.delete(n.nodeId);
    } else {
      await this.loadChildren(n, 0, false);
      this.expanded.add(n.nodeId);
    }
    if (paint) this.list.set(this.visible);
  }
  private async loadChildren(parent: XmlNode, after: number, paint = true) {
    const kids = await children(parent.fileId, parent.nodeId, after, 251);
    const more = kids.length > 250;
    kids.splice(250);
    if (kids.length + this.visible.length + 1 > LIMITS.treeNodes)
      throw new Error('TREE_LIMIT: 表示合計1万件の上限です。折りたたむか検索を使用してください。');
    const insert: TreeEntry[] = [...kids];
    if (more)
      insert.push({
        ...parent,
        nodeId: -parent.nodeId,
        depth: parent.depth + 1,
        more: { parent, after: kids.at(-1)!.nodeId },
      });
    const index = after
      ? this.visible.findIndex((n) => n.more?.parent.nodeId === parent.nodeId)
      : this.visible.findIndex((n) => n.nodeId === parent.nodeId) + 1;
    if (index < 0) return;
    this.visible.splice(index, after ? 1 : 0, ...insert);
    if (paint) this.list.set(this.visible);
  }
  async jump(id: number) {
    const n = await node(this.fileId, id);
    if (!n) return;
    const ancestors: XmlNode[] = [];
    let current = n;
    while (current.parentId) {
      const p = await node(this.fileId, current.parentId);
      if (!p) break;
      ancestors.unshift(p);
      current = p;
    }
    for (const p of ancestors)
      if (!this.expanded.has(p.nodeId)) {
        try {
          await this.toggle(p, false);
        } catch {
          this.visible = [...ancestors, n];
          break;
        }
      }
    if (!this.visible.some((v) => v.nodeId === id)) this.visible = [...ancestors, n];
    this.list.set(this.visible);
    this.list.jump(this.visible.findIndex((v) => v.nodeId === id));
    await this.selectNode(n);
  }
  async selectNode(n: XmlNode) {
    this.selected = n.nodeId;
    this.list.set(this.visible);
    this.onSelect(n);
    const path = await instancePath(n),
      a = await attributes(n.fileId, n.nodeId);
    this.detail.replaceChildren(
      el('span', 'eyebrow', 'NODE DETAILS'),
      el('h2', '', n.elementName),
      el('code', 'path', path),
      table(
        ['項目', '値'],
        [
          ['Namespace', n.namespaceURI || 'なし'],
          ['構造パス', n.path],
          ['深さ', n.depth],
          ['子ノード', n.childCount],
          ['ID', n.nodeId],
        ],
      ),
    );
    if (n.nodeType === 'element')
      this.detail.append(
        button('この要素を出力の1行にする', () => this.onRecord(n), 'primary full-width'),
      );
    if (a.length)
      this.detail.append(
        el('h3', '', '属性'),
        table(
          ['名前', '値'],
          a.map((v) => [v.name, v.value]),
        ),
      );
    const text = el('pre', 'text-preview');
    try {
      text.textContent = await directText(n);
    } catch {
      text.textContent = 'テキストが詳細表示の上限（64Ki文字）を超えています。';
    }
    this.detail.append(el('h3', '', 'テキスト'), text);
    this.detail.append(
      button('XML断片を生成して表示', async () => {
        try {
          const snippet = el('pre', 'text-preview');
          snippet.textContent = await fragment(n);
          this.detail.append(
            el(
              'p',
              'muted',
              '解析結果から再構成した断片です。元ファイルの字句表現とは異なります。',
            ),
            snippet,
          );
        } catch (e) {
          this.report(e);
        }
      }),
    );
  }
}
const escapeXml = (s: string) =>
  s
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
export async function fragment(root: XmlNode): Promise<string> {
  let size = 0,
    count = 0;
  async function visit(n: XmlNode): Promise<string> {
    if (++count > 500) throw new Error('FRAGMENT_LIMIT: 断片表示は500ノード以内です。');
    let result = '';
    if (n.nodeType === 'element') {
      const a = await attributes(n.fileId, n.nodeId);
      // Each element carries its own bindings, so a fragment also works outside its original parent.
      const name = n.namespaceURI ? 'e:' + n.localName : n.localName;
      const declarations = n.namespaceURI ? ` xmlns:e="${escapeXml(n.namespaceURI)}"` : '';
      const attrs = a.filter((v) => v.namespaceURI !== 'http://www.w3.org/2000/xmlns/');
      result =
        '<' +
        name +
        declarations +
        attrs
          .map((v, i) => {
            if (!v.namespaceURI) return ` ${v.localName}="${escapeXml(v.value)}"`;
            if (v.namespaceURI === 'http://www.w3.org/XML/1998/namespace')
              return ` xml:${v.localName}="${escapeXml(v.value)}"`;
            return ` xmlns:a${i}="${escapeXml(v.namespaceURI)}" a${i}:${v.localName}="${escapeXml(v.value)}"`;
          })
          .join('') +
        '>';
      let after = 0;
      while (true) {
        const batch = await children(n.fileId, n.nodeId, after, 501);
        if (!batch.length) break;
        for (const child of batch) result += await visit(child);
        after = batch.at(-1)!.nodeId;
      }
      result += '</' + name + '>';
    } else {
      const text = await directText(n);
      result =
        n.nodeType === 'comment'
          ? `<!--${text}-->`
          : n.nodeType === 'pi'
            ? `<?${n.elementName} ${text}?>`
            : escapeXml(text);
    }
    size += result.length;
    if (size > LIMITS.textPreview) throw new Error('FRAGMENT_LIMIT: 断片が64Ki文字を超えます。');
    return result;
  }
  return visit(root);
}
