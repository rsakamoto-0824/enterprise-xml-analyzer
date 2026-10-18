export type NodeKind = 'element' | 'text' | 'cdata' | 'comment' | 'pi';
export type Status =
  | 'IDLE'
  | 'LOADING'
  | 'PARSING'
  | 'INDEXING'
  | 'READY'
  | 'COMPARING'
  | 'EXPORTING'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'ERROR';
export interface XmlNode {
  fileId: string;
  nodeId: number;
  parentId: number;
  nodeType: NodeKind;
  elementName: string;
  namespaceURI: string;
  localName: string;
  prefix: string;
  depth: number;
  siblingIndex: number;
  documentOrder: number;
  subtreeEndOrder: number;
  childCount: number;
  attributeCount: number;
  subtreeHash: string;
  structureHash: string;
  sourceOffset: number | null;
  textReference: number;
  path: string;
  position: number;
}
export interface Attribute {
  fileId: string;
  nodeId: number;
  name: string;
  localName: string;
  namespaceURI: string;
  value: string;
}
export interface TextSegment {
  fileId: string;
  nodeId: number;
  segment: number;
  value: string;
}
export interface XmlFile {
  fileId: string;
  name: string;
  size: number;
  encoding: string;
  elements: number;
  attributes: number;
  nodes: number;
  maxDepth: number;
  namespaces: Record<string, number>;
  elementCounts: Record<string, number>;
  depthCounts: Record<string, number>;
  childCounts: Record<string, number>;
  parseMs: number;
  indexMs: number;
  status: Status;
  persistent: boolean;
  created: number;
}
export type DiffType = 'ADDED' | 'REMOVED' | 'MODIFIED' | 'MOVED' | 'UNCHANGED' | 'AMBIGUOUS';
export interface DiffOptions {
  mode: 'structure' | 'value' | 'full';
  keyAttribute: string;
  whitespace: 'preserve' | 'trim';
  comments: boolean;
  processingInstructions: boolean;
  ignoreOrder: boolean;
  verifyHashes: boolean;
}
export interface DiffRow {
  comparisonId: string;
  seq: number;
  type: DiffType;
  a: number | null;
  b: number | null;
  path: string;
  name: string;
  attributeName: string;
  oldValue: string;
  newValue: string;
  confidence: string;
  details: string;
  covered: number;
}
export interface Comparison {
  comparisonId: string;
  a: string;
  b: string;
  options: DiffOptions;
  status: Status;
  counts: Record<DiffType, number>;
  rows: number;
  durationMs: number;
}
export interface Column {
  path: string;
  name: string;
  type: 'string' | 'number' | 'date' | 'boolean';
  missing: 'blank' | 'error';
  repeat: 'first' | 'join' | 'rows' | 'sheet';
  enabled: boolean;
}
export interface Filter {
  path: string;
  op: 'equals' | 'contains' | 'range' | 'empty' | 'exists';
  value: string;
  max?: string;
}
export interface ExportTemplate {
  version: 1;
  recordPath: string;
  layout: 'records' | 'nodes' | 'hierarchy';
  columns: Column[];
  filters: Filter[];
  logic: 'AND' | 'OR';
  delimiter: string;
  sourceFile: boolean;
  grouping: 'merged' | 'sheets' | 'files';
  sheetName: string;
  header: boolean;
  autoFilter: boolean;
  freeze: boolean;
  sheetRows: number;
  format: 'xlsx' | 'csv';
  outputName?: string;
  columnWidth?: number;
}
export type Cell = string | number | boolean | Date | null;
export interface ExportRow {
  sheet: string;
  source: string;
  cells: Cell[];
}
export const LIMITS = {
  chunkBytes: 65536,
  batchNodes: 2048,
  maxDepth: 256,
  tokenChars: 1048576,
  textPreview: 65536,
  treeNodes: 10000,
  graphNodes: 500,
  siblingCandidates: 4096,
  queryPage: 1000,
  exportRows: 50000,
  exportCells: 500000,
  exportChars: 16000000,
  csvBlobBytes: 32000000,
  cellChars: 32767,
  sheetRows: 1048575,
  columns: 16384,
  expansionRows: 10000,
  operationMs: 600000,
  queueFiles: 16,
} as const;
export const expandedName = (uri: string, local: string) =>
  uri ? `{${encodeURIComponent(uri)}}${local}` : local;
