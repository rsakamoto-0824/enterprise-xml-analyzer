import { compareXml } from '../diff/diffEngine';
import { serve } from './runtime';
serve(async (_action, p, ctx) => compareXml(p.a, p.b, p.options, ctx.cancelled, ctx.progress));
