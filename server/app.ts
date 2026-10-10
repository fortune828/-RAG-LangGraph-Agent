import express from 'express';
import { z } from 'zod';
import { Store } from './store';
import { settings } from './config';
import { manifest, CURRENT_RELEASE_ID, getCoverage } from './knowledge';
import { runAgent, PROMPT_VERSION } from './agent';
import type { Chat } from './llm';
import type { Turn } from '../shared/types';
const questionSchema = z.string().trim().min(1).max(2000);
const uuid = z.string().uuid();
export function createApp(store = new Store(settings.dataDir), chat?: Chat) {
  const app = express(); app.disable('x-powered-by');
  app.use(express.json({ limit: '16kb' }));
  app.use('/api', (req, res, next) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    const origin = req.headers.origin;
    if (origin) {
      try { if (new URL(origin).host !== req.headers.host) return void res.status(403).json({ error: 'ORIGIN_DENIED' }); }
      catch { return void res.status(403).json({ error: 'ORIGIN_DENIED' }); }
    }
    next();
  });
  app.get('/api/health', (_req, res) => res.json({ status: 'ok', model: settings.model,
    configured: Boolean(settings.key), corpusVersion: CURRENT_RELEASE_ID, evidenceCount: manifest.records,
    knowledgeCoverage: getCoverage(CURRENT_RELEASE_ID) }));
  const requests = new Map<string, number[]>();
  app.use('/api', (req, res, next) => {
    if (req.method === 'GET' || req.method === 'DELETE') return next();
    const ip = req.ip || 'local', now = Date.now();
    const recent = (requests.get(ip) || []).filter(t => now - t < 600000);
    if (recent.length >= 40) return void res.status(429).json({ error: 'RATE_LIMITED' });
    requests.set(ip, [...recent, now]); next();
  });
  const token = (req: express.Request) => {
    const value = req.headers.authorization?.replace(/^Bearer /, '') || '';
    if (!uuid.safeParse(value).success) throw new Error('SESSION_ACCESS_DENIED');
    return value;
  };
  const access = (req: express.Request) => {
    const id = uuid.parse(req.params.id);
    if (!store.authorize(id, token(req))) throw new Error('SESSION_ACCESS_DENIED');
    return store.get(id)!;
  };
  app.post('/api/sessions', (req, res, next) => {
    try {
      const body = z.object({ requestId: uuid, question: questionSchema }).strict().parse(req.body);
      res.status(201).json(store.create(body.requestId, token(req), body.question));
    } catch (e) { next(e); }
  });
  app.get('/api/sessions/:id', (req, res, next) => {
    try { res.json(access(req)); } catch (e) { next(e); }
  });
  const busy = new Set<string>();
  app.delete('/api/sessions/:id', (req, res, next) => {
    try { const session = access(req);
      if (busy.has(session.id)) return void res.status(409).json({ error: 'SESSION_BUSY' });
      store.delete(session.id); res.status(204).end();
    } catch (e) { next(e); }
  });
  app.post('/api/sessions/:id/turns', async (req, res, next) => {
    let activeId: string | undefined;
    try {
      const session = access(req);
      const body = z.object({ requestId: uuid, question: questionSchema, retry: z.boolean().optional() }).strict().parse(req.body);
      let turn = session.turns.find(t => t.id === body.requestId);
      if (turn && turn.question !== body.question) throw new Error('IDEMPOTENCY_CONFLICT');
      if (turn && (turn.status !== 'failed' || !body.retry)) return void res.status(turn.status === 'running' ? 202 : 200).json(session);
      if (busy.has(session.id)) return void res.status(409).json({ error: 'SESSION_BUSY' });
      if (busy.size >= 2) return void res.status(503).json({ error: 'SERVER_BUSY' });
      if (session.turns.length >= 60 && !turn) return void res.status(409).json({ error: 'SESSION_LIMIT' });
      busy.add(session.id); activeId = session.id;
      const mode = session.turns.some(t => t.status === 'completed') || body.question !== session.question ? 'followup' : 'initial';
      const fresh: Turn = { id: body.requestId, question: body.question, mode, status: 'running',
        createdAt: new Date().toISOString(), model: settings.model, trace: [], evidence: [], releaseId: session.releaseId };
      if (turn) Object.assign(turn, fresh, { error: undefined, answer: undefined });
      else { turn = fresh; session.turns.push(turn); }
      store.save(session);
      const result = await runAgent(session, body.question, mode, chat);
      Object.assign(turn, result, { status: result.error ? 'failed' : 'completed' });
      store.save(session);
      store.log({ sessionId: session.id, turnId: turn.id, promptVersion: PROMPT_VERSION,
        corpusVersion: session.releaseId, model: settings.model, status: turn.status,
        durationMs: turn.durationMs, trace: turn.trace.map(({ args, ...e }) => e) });
      res.json(session);
    } catch (e) { next(e); } finally { if (activeId) busy.delete(activeId); }
  });
  app.use('/api', (_req, res) => res.status(404).json({ error: 'NOT_FOUND' }));
  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const msg = err instanceof Error ? err.message : '';
    const code = ['SESSION_ACCESS_DENIED','IDEMPOTENCY_CONFLICT'].includes(msg) ? msg : 'INVALID_REQUEST';
    res.status(code === 'SESSION_ACCESS_DENIED' ? 404 : code === 'IDEMPOTENCY_CONFLICT' ? 409 : 400).json({ error: code });
  });
  return app;
}
