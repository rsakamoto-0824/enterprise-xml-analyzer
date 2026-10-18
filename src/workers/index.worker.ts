import { search } from '../search/searchEngine';
import { serve } from './runtime';
serve(async (_action, p, ctx) => search(p.fileId, p.options, ctx.cancelled, ctx.progress));
