import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { calculateHexagram } from '../src/lib/yijing';
import { retrieveExact, retrieveLexical, normalizeSearch, CURRENT_RELEASE_ID, LEGACY_RELEASE_ID, resolveReleaseId, initialEvidence } from '../server/knowledge';
import { requestedTargets, matchesTarget, validateAnswer } from '../server/validation';
import { runAgent, packEvidence, MAX_EVIDENCE_CHARACTERS, MAX_EVIDENCE_RECORDS } from '../server/agent';
import { Store } from '../server/store';
import type { Answer, Evidence, Session } from '../shared/types';
import type { Chat, Message } from '../server/llm';

function session(): Session {
  return { id: randomUUID(), question: '我刚进入团队，如何兼顾表现和学习？', createdAt: 'test', releaseId: CURRENT_RELEASE_ID, turns: [],
    cast: { ...calculateHexagram([7,7,7,7,7,7]), id: 'test-cast', method: 'three-coins', algorithmVersion: 'coins-v2.1', createdAt: 'test' } };
}
function response(message: Message) { return { message, usage: { total_tokens: 10 }, model: 'mock-no-live-call' }; }
function answer(e: Evidence, text = '这是借助资料进行的文化反思。'): Answer {
  return { status: 'answered', summary: '结合材料思考行动的分寸。', paragraphs: [{ text, citations: [{ evidenceId: e.id, quote: e.text.slice(0, 10) }] }], limitations: ['不能用于现实预测。'] };
}
const qian2 = { hexagramId: 1, sections: ['line'], lineIndices: [2] };

test('same anchor retrieves three distinct attributed commentaries without replacing original text', () => {
  const classic = retrieveExact(qian2);
  assert.deepEqual(classic.evidence.map(e => e.id), ['zy-1-line-2']);
  assert.ok(classic.evidence.every(e => e.layer === 'classic'));
  const ids = new Set<string>();
  for (const author of ['wang-bi', 'cheng-yi', 'zhu-xi']) {
    const r = retrieveExact({ ...qian2, authorIds: [author], layers: ['commentary'] });
    assert.ok(r.evidence.length > 0, `Missing imported Qian line 2: ${author}`);
    assert.equal(r.releaseId, CURRENT_RELEASE_ID);
    assert.ok(r.evidence.every(e => e.layer === 'commentary' && e.authorIds?.includes(author)));
    assert.ok(r.evidence.every(e => e.commentsOn?.includes('zhouyi:01:line:2')));
    assert.ok(r.evidence.every(e => e.workId && e.editionId && e.source.includes('oldid=') && e.reviewStatus !== 'human_verified'));
    r.evidence.forEach(e => { assert.equal(ids.has(e.id), false); ids.add(e.id); });
  }
});

test('author filtering does not substitute another author and coverage differs from lexical no-hit', () => {
  const missing = retrieveExact({ hexagramId: 64, sections: ['line'], lineIndices: [2], authorIds: ['zhu-xi'], layers: ['commentary'] });
  assert.deepEqual(missing.evidence, []);
  assert.ok(['partial', 'not_ingested'].includes(missing.coverageStatus));
  assert.ok(missing.missingTargets.length > 0);
  const noMatch = retrieveLexical({ query: 'quantumXYZnotInCorpus923', authorIds: ['zhu-xi'], topK: 4 });
  assert.deepEqual(noMatch.evidence, []);
  assert.ok(noMatch.noHitReason);
  const r = retrieveExact({ ...qian2, authorIds: ['wang-bi'], layers: ['commentary'] });
  assert.ok(r.evidence.every(e => e.authorIds?.includes('wang-bi') && !e.authorIds?.includes('kong-yingda')));
});

test('search folds simplified/traditional forms while quote checking preserves supplied text', () => {
  assert.equal(normalizeSearch('潛龍，勿用。'), normalizeSearch('潜龙，勿用。'));
  const a = retrieveLexical({ query: '潜龙勿用', hexagramId: 1, topK: 6 });
  const b = retrieveLexical({ query: '潛龍勿用', hexagramId: 1, topK: 6 });
  assert.deepEqual(a.evidence.map(e => e.id), b.evidence.map(e => e.id));
  assert.ok(a.evidence.some(e => e.id === 'zy-1-line-1'));
  const original = retrieveExact(qian2).evidence[0];
  const fixture: Evidence = { ...original, id: 'test-traditional-quote', text: '見龍在田，利見大人。', textOriginal: '見龍在田，利見大人。' };
  const good = answer(fixture); good.paragraphs[0].citations[0].quote = '見龍在田';
  assert.deepEqual(validateAnswer(good, [fixture]).errors, []);
  good.paragraphs[0].citations[0].quote = '见龙在田';
  assert.ok(validateAnswer(good, [fixture]).errors.some(e => e.startsWith('QUOTE_MISMATCH')));
});

test('explicit named hexagram without a line binds annotation target to that hexagram', () => {
  const targets = requestedTargets('朱熹怎样解释坤卦？', session().cast);
  assert.ok(targets.length);
  assert.ok(targets.every(t => t.hexagramId === 2 && t.authorId === 'zhu-xi' && t.layer === 'commentary'));
  const wrong = retrieveExact({ ...qian2, authorIds: ['zhu-xi'] }).evidence[0];
  assert.equal(matchesTarget(wrong, targets[0]), false);
});

test('true quotation cannot disguise wrong attribution or wrong commentary target', () => {
  const e = retrieveExact({ ...qian2, authorIds: ['wang-bi'] }).evidence[0];
  const wrong = answer(e, '朱熹认为应当以此理解行动的分寸。');
  assert.ok(validateAnswer(wrong, [e]).errors.some(x => x.includes('AUTHOR_ATTRIBUTION_MISMATCH')));
  const targets = requestedTargets('朱熹怎样解释乾卦九二？', session().cast);
  assert.ok(validateAnswer(answer(e), [e], [], targets).errors.some(x => x.startsWith('REQUIRED_TARGET_NOT_CITED')));
  const zhuxi = retrieveExact({ ...qian2, authorIds: ['zhu-xi'] }).evidence[0];
  assert.deepEqual(validateAnswer(answer(zhuxi, '朱熹的这段注解可以作为理解材料。'), [zhuxi], [], targets).errors, []);
});

test('honest missing-author explanation is allowed without inventing a quotation', () => {
  const missing: Answer = { status: 'insufficient', summary: '当前资料不足。',
    paragraphs: [{ text: '本库未收录孔颖达此处的疏，不能提供原文。', citations: [] }], limitations: ['只说明本库收录范围，不判断该著作是否存在。'] };
  assert.deepEqual(validateAnswer(missing, []).errors, []);
  const fabricated: Answer = { ...missing, paragraphs: [{ text: '孔颖达认为这里应当立即行动。', citations: [] }] };
  assert.ok(validateAnswer(fabricated, []).errors.some(x => x.includes('AUTHOR_ATTRIBUTION_MISMATCH')));
});

test('natural follow-up may choose a commentary tool; executed metadata and final citation survive loop', async () => {
  const s = session(), before = JSON.stringify(s.cast);
  const e = retrieveExact({ ...qian2, authorIds: ['zhu-xi'], layers: ['commentary'] }).evidence[0];
  let calls = 0, sawToolMetadata = false;
  const chat: Chat = async (messages, options) => {
    calls++;
    if (calls === 1) {
      assert.ok(options?.tools?.length);
      assert.ok(!messages.at(-1)?.content?.includes('朱熹'));
      return response({ role: 'assistant', content: null, tool_calls: [{ id: 'natural-decision', type: 'function', function: {
        name: 'get_passages', arguments: JSON.stringify({ ...qian2, layers: ['commentary'], authorIds: ['zhu-xi'] }),
      } }] });
    }
    sawToolMetadata ||= messages.some(m => m.role === 'tool' && m.content?.includes(e.id) && m.content.includes('zhu-xi'));
    return response({ role: 'assistant', content: options?.tools ? '{"ready":true}' : JSON.stringify(answer(e, '朱熹的注解提供了另一种理解角度。')) });
  };
  const r = await runAgent(s, '我怕主动表现变成逞强，怎样把握分寸？能换一个角度理解吗？', 'followup', chat);
  assert.equal(r.error, undefined); assert.equal(r.answer?.status, 'answered');
  assert.ok(sawToolMetadata); assert.equal(JSON.stringify(s.cast), before);
  assert.ok(r.trace.some(t => t.step === 'tool' && t.evidenceIds?.includes(e.id)));
  assert.ok(r.answer?.paragraphs.some(p => p.citations.some(c => c.evidenceId === e.id)));
  // This test verifies orchestration with a mock decision; live model behavior is reported separately.
});

test('fixed and agent variants use the same answer repair validation; fixed variant cannot call tools', async () => {
  const s = session(); const e = retrieveExact({ hexagramId: 1, sections: ['xiang'] }).evidence[0];
  let calls = 0;
  const fixed: Chat = async (_messages, options) => {
    assert.equal(options?.tools, undefined);
    return response({ role: 'assistant', content: ++calls === 1 ? '{"status":"answered"}' : JSON.stringify(answer(e)) });
  };
  const r = await runAgent(s, '还能怎样理解？', 'followup', fixed, { disableTools: true });
  assert.equal(calls, 2); assert.equal(r.answer?.status, 'answered');
  assert.deepEqual(r.trace.filter(t => t.step === 'validate').map(t => t.status), ['failed', 'passed']);
});

test('old sessions retain legacy release while newly created sessions use current release', () => {
  const dir = mkdtempSync(join(tmpdir(), 'yijing-knowledge-version-'));
  try {
    const old = session(), token = randomUUID(); delete old.releaseId;
    writeFileSync(join(dir, old.id + '.json'), JSON.stringify({ ...old, tokenHash: createHash('sha256').update(token).digest('hex') }));
    const store = new Store(dir);
    const loaded = store.get(old.id)!;
    assert.equal(loaded.releaseId, LEGACY_RELEASE_ID);
    assert.equal(JSON.stringify(loaded.cast), JSON.stringify(old.cast));
    assert.equal(retrieveExact({ ...qian2, authorIds: ['zhu-xi'] }, loaded.releaseId).evidence.length, 0);
    assert.equal(store.create(randomUUID(), randomUUID(), '新问题').releaseId, CURRENT_RELEASE_ID);
    assert.equal(resolveReleaseId(LEGACY_RELEASE_ID), LEGACY_RELEASE_ID);
    assert.throws(() => resolveReleaseId('nonexistent-release-do-not-silently-switch'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});


test('every possible cast preserves all moving-line originals within initial evidence budget', () => {
  for (let n = 0; n < 4096; n++) {
    const lines = Array.from({ length: 6 }, (_, i) => 6 + ((n >> (2 * i)) & 3));
    const cast = { ...session().cast, ...calculateHexagram(lines) };
    const initial = initialEvidence(cast, '面对变化，我如何思考？', CURRENT_RELEASE_ID);
    const packed = packEvidence(initial.filter(e => e.layer === 'classic'), initial.filter(e => e.layer !== 'classic'));
    assert.ok(packed.characters <= MAX_EVIDENCE_CHARACTERS);
    assert.ok(packed.evidence.length <= MAX_EVIDENCE_RECORDS);
    for (const index of cast.changingIndices) {
      assert.ok(packed.evidence.some(e => e.layer === 'classic' && e.hexagramId === cast.original.number && e.section === 'line' && e.line === index + 1), `Cast ${lines.join(',')} lost moving line ${index + 1}`);
    }
  }
});

test('ready with an available but unseen explicit target gets one router feedback opportunity', async () => {
  const s = session();
  const upper = retrieveExact({ hexagramId: 1, sections: ['line'], lineIndices: [6] }).evidence[0];
  let routerCalls = 0, generationCalls = 0, sawFeedback = false;
  const chat: Chat = async (messages, options) => {
    if (options?.tools) {
      routerCalls++;
      if (routerCalls === 1) return response({ role: 'assistant', content: '{"ready":true}' });
      if (routerCalls === 2) {
        sawFeedback = messages.some(m => m.role === 'user' && /6|上爻/.test(m.content || '') && /缺|目标/.test(m.content || ''));
        return response({ role: 'assistant', content: null, tool_calls: [{ id: 'feedback-upper', type: 'function', function: {
          name: 'get_passages', arguments: JSON.stringify({ hexagramId: 1, sections: ['line'], lineIndices: [6] }),
        } }] });
      }
      return response({ role: 'assistant', content: '{"ready":true}' });
    }
    generationCalls++;
    assert.ok(messages.some(m => m.role === 'tool' && m.content?.includes(upper.id)));
    return response({ role: 'assistant', content: JSON.stringify(answer(upper)) });
  };
  const r = await runAgent(s, '如果从本卦上爻来看，为什么进得太过反而会有悔？', 'followup', chat);
  assert.ok(sawFeedback); assert.equal(r.answer?.status, 'answered');
  assert.equal(r.trace.filter(t => t.step === 'replan' && t.code === 'MISSING_RETRIEVABLE_TARGET').length, 1);
  assert.ok(r.trace.some(t => t.step === 'tool' && t.evidenceIds?.includes(upper.id)));
  assert.equal(generationCalls, 1); assert.ok(routerCalls <= 3);
});

test('router feedback is bounded and unavailable materials do not trigger retry loops', async () => {
  for (const [question, maxRouterCalls] of [
    ['请解释本卦上爻。', 2],
    ['请提供孔颖达对乾卦九二的疏，未收录就说明不足。', 1],
  ] as const) {
    let routerCalls = 0;
    const chat: Chat = async (_messages, options) => {
      if (options?.tools) { routerCalls++; return response({ role: 'assistant', content: '{"ready":true}' }); }
      return response({ role: 'assistant', content: JSON.stringify({ status: 'insufficient', summary: '这轮未取得对应资料。', paragraphs: [], limitations: ['不能用无关材料替代。'] }) });
    };
    const r = await runAgent(session(), question, 'followup', chat);
    assert.equal(r.error, undefined);
    assert.equal(r.answer?.status, 'insufficient');
    assert.ok(routerCalls <= maxRouterCalls);
    assert.equal(r.trace.filter(t => t.step === 'tool').length, 0);
  }
});
