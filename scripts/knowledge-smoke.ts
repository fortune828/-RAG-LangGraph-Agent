import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { calculateHexagram } from '../src/lib/yijing';
import { runAgent, PROMPT_VERSION } from '../server/agent';
import { CURRENT_RELEASE_ID } from '../server/knowledge';
import { deepseekChat } from '../server/llm';
import { settings } from '../server/config';
import { validateAnswer } from '../server/validation';
import { budgetedChat } from './lib/model-budget';
import type { Session, Turn } from '../shared/types';

const resumePath = process.argv.find(a => a.startsWith('--resume='))?.slice('--resume='.length);
const resumed = resumePath ? JSON.parse(readFileSync(resumePath, 'utf8')) : undefined;
if (resumed && (resumed.kind !== 'live_development_conversation_smoke' || !Array.isArray(resumed.rows) || !Array.isArray(resumed.fixedLines))) throw new Error('Resume requires a saved live development conversation smoke report');
const originalCases = [
  { id: 'natural-initial', mode: 'initial' as const, question: '我刚进入一个新团队，有一些经验，但还没有建立信任。面对主动表现和先观察学习，我该怎么思考？' },
  { id: 'natural-followup', mode: 'followup' as const, question: '我担心一味主动会越界，但过分谨慎又错过机会。这个分寸该怎么把握？有没有不同的理解角度？' },
  { id: 'missing-work', mode: 'followup' as const, question: '请给出焦循《易章句》对本卦第二爻的完整注解原文，不能用其他著作替代。' },
];
const questionOverride = process.argv.find(a => a.startsWith('--question='))?.slice('--question='.length);
if (questionOverride !== undefined && (!resumed || !questionOverride.trim() || questionOverride.length > 2000)) throw new Error('--question requires --resume and 1..2000 characters');
const cases = resumed ? [{ id: 'natural-new-stage', mode: 'followup' as const, question: questionOverride || '假如以后开始承担管理责任，怎样避免因职位提高而自满？请从另一个阶段的角度帮我理解。' }] : originalCases;
const fixedLines = resumed?.fixedLines || [7,9,7,7,7,7];
const releaseId = resumed?.releaseId || CURRENT_RELEASE_ID;
const option = (key: string, fallback: number) => {
  const arg = process.argv.find(v => v.startsWith(`--${key}=`));
  const value = arg ? Number(arg.slice(key.length + 3)) : fallback;
  if (!Number.isInteger(value) || value < 1) throw new Error(`Invalid --${key}`);
  return value;
};
const maxCalls = option('max-calls', resumed ? 6 : 12);
if (maxCalls > (resumed ? 6 : 12)) throw new Error('This smoke is capped at 12 requests (6 for resume); use a separate frozen evaluation for larger runs.');
const maxTokens = option('max-tokens', 60000);
if (!process.argv.includes('--live')) {
  console.log(JSON.stringify({ mode: 'plan_only_no_model_calls', releaseId, resumedFrom: resumePath,
    fixedLines, maxCalls, maxTokens, cases,
    note: '显式加 --live 才调用模型。前两问不点名注家，工具是否调用由真实模型决定；输出不预设成功。' }, null, 2));
} else {
  const dir = 'evaluation/results'; mkdirSync(dir, { recursive: true });
  const file = `${dir}/${resumed ? 'knowledge-followup' : 'knowledge-smoke'}-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  const session: Session = { id: randomUUID(), question: resumed?.rows[0]?.question || cases[0].question, createdAt: new Date().toISOString(), turns: [],
    releaseId,
    cast: { ...calculateHexagram(fixedLines), id: 'smoke-fixed-qian-line2', method: 'three-coins', algorithmVersion: 'coins-v2.1', createdAt: 'fixed-evaluation-input' } };
  if (resumed) {
    const history = resumed.rows.slice(0, 2);
    if (history.length !== 2 || history.some((r: any) => r.error || !r.answer || !Array.isArray(r.evidence))) throw new Error('Resume requires the first two real completed turns');
    session.turns = history.map((r: any) => ({ id: randomUUID(), question: r.question, mode: r.mode, status: 'completed' as const,
      createdAt: 'restored-development-smoke', model: resumed.model, releaseId, answer: r.answer, evidence: r.evidence, trace: r.trace || [] }));
  }
  const castBefore = JSON.stringify(session.cast);
  const { chat, budget } = budgetedChat(deepseekChat, maxCalls, maxTokens);
  const rows: unknown[] = [];
  const persist = () => writeFileSync(file, JSON.stringify({ kind: resumed ? 'live_development_resumed_followup' : 'live_development_conversation_smoke', resumedFrom: resumePath, restoredTurns: session.turns.slice(0, resumed ? 2 : 0), model: settings.model,
    promptVersion: PROMPT_VERSION, releaseId, fixedLines: session.cast.lines, budget,
    note: '开发会话冒烟，不是冻结留出集。自动检查不能证明解释的语义正确；自然追问不强制检索，若已有材料足够，少调用工具也是合理结果。预算按序列化UTF8字节+输出额度保守预留、按返回usage结算，不是供应商精确分词或金额承诺。', rows }, null, 2));
  persist();
  for (const c of cases) {
    if (budget.stopped) { rows.push({ caseId: c.id, outcome: 'not_run_budget_exhausted' }); persist(); continue; }
    const startCalls = budget.calls, startTokens = budget.reportedTokens;
    const result = await runAgent(session, c.question, c.mode, chat);
    const validation = validateAnswer(result.answer, result.evidence);
    const toolEvents = result.trace.filter(e => e.step === 'tool');
    const toolIds = new Set(toolEvents.filter(e => e.status === 'ok').flatMap(e => e.evidenceIds || []));
    const cited = new Set(result.answer?.paragraphs.flatMap(p => p.citations.map(c => c.evidenceId)) || []);
    const commentary = result.evidence.filter(e => e.layer === 'commentary');
    const safeFallback = result.trace.filter(e => e.step === 'validate').at(-1)?.status === 'failed';
    const sameCast = JSON.stringify(session.cast) === castBefore;
    const row = { caseId: c.id, question: c.question, mode: c.mode, error: result.error,
      outcome: result.error ? 'execution_failed' : safeFallback ? 'validation_fallback' : result.answer?.status,
      schemaAndCitationValid: !result.error && !validation.errors.length, validationErrors: validation.errors,
      sameCast, modelCalls: budget.calls - startCalls, reportedTokens: budget.reportedTokens - startTokens,
      toolCalls: toolEvents.length, toolEvents, commentaryRetrievedIds: commentary.map(e => e.id),
      commentaryCitedIds: commentary.filter(e => cited.has(e.id)).map(e => e.id),
      toolDerivedCommentaryCitedIds: commentary.filter(e => toolIds.has(e.id) && cited.has(e.id)).map(e => e.id),
      insufficientExpected: c.id === 'missing-work',
      correctlyStoppedForMissingWork: c.id === 'missing-work' ? !result.error && !safeFallback && result.answer?.status === 'insufficient' : undefined,
      semanticSupportReview: 'pending', attributionMeaningReview: 'pending', answer: result.answer,
      evidence: result.evidence, trace: result.trace, durationMs: result.durationMs };
    rows.push(row);
    const turn: Turn = { id: randomUUID(), question: c.question, mode: c.mode, createdAt: new Date().toISOString(),
      model: settings.model, status: result.error ? 'failed' : 'completed', ...result };
    session.turns.push(turn);
    persist();
    console.log(JSON.stringify({ caseId: c.id, outcome: row.outcome, sameCast, modelCalls: row.modelCalls,
      toolCalls: row.toolCalls, commentaryCitedIds: row.commentaryCitedIds, toolDerivedCommentaryCitedIds: row.toolDerivedCommentaryCitedIds }));
  }
  console.log(`Saved ${file}`);
}
