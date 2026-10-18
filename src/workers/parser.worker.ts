import { parseXml } from '../parser/xmlStreamParser';
import { serve } from './runtime';
serve(async (_action, p, ctx) =>
  parseXml(p.file, p.file.name, p.fileId, p.persistent, {
    cancelled: ctx.cancelled,
    progress: (bytes, total, elements) => ctx.progress({ bytes, total, elements }),
  }),
);
