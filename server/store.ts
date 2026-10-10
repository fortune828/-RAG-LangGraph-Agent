import { mkdirSync, readFileSync, writeFileSync, renameSync, existsSync, appendFileSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { createHash, randomInt, randomUUID, timingSafeEqual } from 'node:crypto';
import { calculateHexagram } from '../src/lib/yijing';
import type { Session, Cast } from '../shared/types';
import { CURRENT_RELEASE_ID, LEGACY_RELEASE_ID } from './knowledge';
export function sampleCast(): Cast {
  const lines = Array.from({ length: 6 }, () => 6 + randomInt(2) + randomInt(2) + randomInt(2));
  return { ...calculateHexagram(lines), id: randomUUID(), method: 'three-coins',
    algorithmVersion: 'coins-v2.1', createdAt: new Date().toISOString() };
}
export class Store {
  private cache = new Map<string, Session>();
  constructor(public dir: string) { mkdirSync(dir, { recursive: true, mode: 0o700 }); }
  private path(id: string) {
    if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error('INVALID_SESSION_ID');
    return join(this.dir, id + '.json');
  }
  private hash(token: string) { return createHash('sha256').update(token).digest('hex'); }
  authorize(id: string, token: string) {
    const p = this.path(id);
    if (!existsSync(p)) return false;
    const saved = JSON.parse(readFileSync(p, 'utf8'));
    return timingSafeEqual(Buffer.from(saved.tokenHash, 'hex'), Buffer.from(this.hash(token), 'hex'));
  }
  get(id: string): Session | undefined {
    if (this.cache.has(id)) return this.cache.get(id);
    if (!existsSync(this.path(id))) return undefined;
    const { tokenHash, ...session } = JSON.parse(readFileSync(this.path(id), 'utf8'));
    // Legacy sessions retain their original corpus. Never silently reinterpret
    // a saved answer using whichever knowledge release is current at startup.
    session.releaseId ||= LEGACY_RELEASE_ID;
    for (const t of session.turns) if (t.status === 'running') {
      t.status = 'failed'; t.error = 'SERVER_RESTARTED';
    }
    this.cache.set(id, session); this.save(session, tokenHash);
    return session;
  }
  create(id: string, token: string, question: string) {
    if (existsSync(this.path(id))) {
      if (!this.authorize(id, token)) throw new Error('SESSION_ACCESS_DENIED');
      const session = this.get(id)!;
      if (session.question !== question) throw new Error('IDEMPOTENCY_CONFLICT');
      return session;
    }
    const session: Session = { id, question, cast: sampleCast(), turns: [], createdAt: new Date().toISOString(), releaseId: CURRENT_RELEASE_ID };
    this.save(session, this.hash(token)); return session;
  }
  save(session: Session, tokenHash?: string) {
    const path = this.path(session.id);
    const hash = tokenHash || JSON.parse(readFileSync(path, 'utf8')).tokenHash;
    const tmp = path + '.' + randomUUID() + '.tmp';
    writeFileSync(tmp, JSON.stringify({ ...session, tokenHash: hash }), { mode: 0o600 });
    renameSync(tmp, path); this.cache.set(session.id, session);
  }
  delete(id: string) { unlinkSync(this.path(id)); this.cache.delete(id); }
  log(value: unknown) { appendFileSync(join(this.dir, 'traces.jsonl'), JSON.stringify(value) + '\n', { mode: 0o600 }); }
}
