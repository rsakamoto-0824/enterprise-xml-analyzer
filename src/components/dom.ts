export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text = '',
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  n.className = className;
  n.textContent = text;
  return n;
}
export function button(label: string, action: () => unknown, className = ''): HTMLButtonElement {
  const n = el('button', className, label);
  n.type = 'button';
  n.onclick = () => {
    void action();
  };
  return n;
}
export function input(label: string, value = '', type = 'text'): HTMLInputElement {
  const n = el('input');
  n.type = type;
  n.value = value;
  n.setAttribute('aria-label', label);
  return n;
}
export function select(
  label: string,
  items: [string, string][],
  value?: string,
): HTMLSelectElement {
  const n = el('select');
  n.setAttribute('aria-label', label);
  for (const [v, text] of items) {
    const option = el('option', '', text);
    option.value = v;
    n.append(option);
  }
  if (value !== undefined) n.value = value;
  return n;
}
export function field(label: string, control: HTMLElement): HTMLLabelElement {
  const n = el('label', 'field');
  n.append(el('span', 'field-label', label), control);
  return n;
}
export function checkbox(
  label: string,
  checked: boolean,
  change: (value: boolean) => void,
): HTMLLabelElement {
  const n = el('label', 'check'),
    c = input(label, '', 'checkbox');
  c.checked = checked;
  c.onchange = () => change(c.checked);
  n.append(c, document.createTextNode(label));
  return n;
}
export function table(headers: string[], rows: unknown[][]): HTMLTableElement {
  const t = el('table'),
    head = el('thead'),
    tr = el('tr');
  for (const h of headers) tr.append(el('th', '', h));
  head.append(tr);
  const body = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    for (const c of row)
      tr.append(
        el(
          'td',
          '',
          c === null || c === undefined ? '' : c instanceof Date ? c.toISOString() : String(c),
        ),
      );
    body.append(tr);
  }
  t.append(head, body);
  return t;
}
export function download(data: BlobPart, name: string, type = 'application/octet-stream') {
  const url = URL.createObjectURL(new Blob([data], { type })),
    a = el('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
export const formatBytes = (n: number) =>
  n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(2)} MB`;
