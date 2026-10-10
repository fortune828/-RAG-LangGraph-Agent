import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { loadRelease, retrieveExact, retrieveLexical } from '../server/knowledge';
import { validateAnswer } from '../server/validation';
import type { Answer, Evidence } from '../shared/types';

const parent = 'knowledge-v3-20260926';
const corrected = 'knowledge-v3-20260926-r1';
const fixes = JSON.parse(readFileSync(new URL('../data/knowledge/review/crosscheck-20260926/corrections.json', import.meta.url), 'utf8'));
const answer = (e: Evidence, quote: string): Answer => ({ status: 'answered', summary: '按所查资料摘录。',
  paragraphs: [{ text: '以下引文来自所查资料。', citations: [{ evidenceId: e.id, quote }] }], limitations: [] });

test('all 12 corrected passages are retrieved and cited in their own release, while old quotes remain reproducible', () => {
  for (const fix of fixes) {
    const old = loadRelease(parent).corpus.find(e => e.id === fix.evidenceId)!;
    const args = { hexagramId: old.hexagramId, sections: [old.section],
      ...(old.line ? { lineIndices: [old.line] } : {}), layers: [old.layer],
      ...(old.authorIds?.length ? { authorIds: old.authorIds } : {}) };
    const updated = retrieveExact(args, corrected).evidence.find(e => e.id === old.id)!;
    assert.equal(updated.text, fix.correctedText);
    assert.equal(updated.textOriginal, fix.originalText);
    assert.equal(retrieveExact(args, parent).evidence.find(e => e.id === old.id)!.text, fix.originalText);
    assert.deepEqual(validateAnswer(answer(updated, updated.text), [updated]).errors, []);
    // The five classic typos change the quoted words. Adding a final 也 to a
    // commentary still leaves its former prefix a valid substring quotation.
    if (fix.audit_area === 'classic') {
      assert.ok(validateAnswer(answer(updated, old.text), [updated]).errors.some(e => e.startsWith('QUOTE_MISMATCH')));
    }
    assert.deepEqual(validateAnswer(answer(old, old.text), [old]).errors, []);
    assert.equal(updated.correctionHistory?.[0].humanReviewed, false);
  }
});

test('lexical search serves the corrected Wang Bi wording and pending edition differences stay unchanged', () => {
  const result = retrieveLexical({ query: '童蒙願發', hexagramId: 4, authorIds: ['wang-bi'], topK: 6 }, corrected);
  assert.ok(result.evidence.some(e => e.line === 6 && e.text.includes('童蒙願發') && !e.text.includes('童蒙原發')));
  const before = loadRelease(parent).corpus;
  const after = loadRelease(corrected).corpus;
  const ids = new Set(fixes.map((f: any) => f.evidenceId));
  assert.equal(after.length, before.length);
  assert.equal(after.filter(e => e.text !== before.find(p => p.id === e.id)!.text).length, 12);
  for (const old of before.filter(e => !ids.has(e.id))) {
    assert.deepEqual({ ...after.find(e => e.id === old.id), releaseId: parent }, old);
  }
  assert.ok(after.find(e => e.id === 'cheng-yi@1313743:h1:line3:10ea52416d')!.text.includes('三雖人位'));
});

test('published parent corpus is byte-for-byte unchanged', () => {
  const bytes = readFileSync(new URL(`../data/knowledge/releases/${parent}/evidence.json`, import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),
    'c9375046355eac73ef120aa9275877c9eafbbf225f1d5d2d9116a3a4ba3b2b05');
});
