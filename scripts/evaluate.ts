import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { calculateHexagram } from '../src/lib/yijing';
import { corpus, getPassages, CURRENT_RELEASE_ID, manifest } from '../server/knowledge';
import { runAgent, PROMPT_VERSION } from '../server/agent';
import { deepseekChat } from '../server/llm';
import { validateAnswer } from '../server/validation';
import { settings } from '../server/config';
import { budgetedChat } from './lib/model-budget';
import type { Session } from '../shared/types';
const data = JSON.parse(readFileSync('evaluation/cases.json', 'utf8'));
const dir = 'evaluation/results'; mkdirSync(dir, { recursive: true });
if (!process.argv.includes('--live')) {
  const rows = data.cases.filter((c: any) => c.kind === 'exact_lookup').map((c: any) => {
    const ids = getPassages(c.args).map(e => e.id); return { id: c.id, passed: JSON.stringify(ids) === JSON.stringify(c.expectedIds) };
  });
  const result = { kind: 'offline_structural_retrieval', releaseId: CURRENT_RELEASE_ID, total: rows.length, passed: rows.filter((r: any) => r.passed).length,
    note: '只验证精确检索契约，不代表端到端LLM质量。对话场景尚需模型运行及人工语义审查。', rows };
  writeFileSync(`${dir}/offline.json`, JSON.stringify(result, null, 2)); console.log(JSON.stringify({ kind: result.kind, total: result.total, passed: result.passed, note: result.note }));
  if (result.passed !== result.total) process.exitCode = 1;
} else {
  const ids = ['initial-qian', 'line-qian2', 'search-source', 'missing-tuan', 'missing-commentary', 'same-cast'];
  const caseArg = process.argv.find(a => a.startsWith('--case='))?.slice('--case='.length);
  const cases = data.cases.filter((c: any) => c.kind === 'conversation' && (caseArg ? c.id === caseArg : process.argv.includes('--all') || ids.includes(c.id))).map((c: any) => {
    if (c.id === 'missing-tuan') {
      const passages = getPassages({ hexagramId: 32, sections: ['tuan'] });
      return { ...c, expected: passages.length ? 'answered' : 'insufficient', required: passages.map(e => e.id), expectationMigration: 'derived_from_current_release_coverage' };
    }
    if (c.id === 'missing-commentary') {
      const passages = getPassages({ hexagramId: 1, sections: ['line'], lineIndices: [2], authorIds: ['zhu-xi'], layers: ['commentary'] });
      return { ...c, question: '请引用朱熹对乾卦九二的逐字注解，库里没有就说明不足。',
        expected: passages.length ? 'answered' : 'insufficient', required: [], requiredAuthor: passages.length ? 'zhu-xi' : undefined,
        expectationMigration: 'question_scoped_to_qian_line2_and_current_release_coverage' };
    }
    return c;
  });
  if (!cases.length) throw new Error('No matching development case');
  const numeric = (key: string, fallback: number) => Number(process.argv.find(a => a.startsWith(`--${key}=`))?.slice(key.length + 3) || fallback);
  const { chat, budget } = budgetedChat(deepseekChat, numeric('max-calls', 60), numeric('max-tokens', 150000));
  const rows: any[] = [];
  const filename = `${dir}/live-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  const variants = process.argv.includes('--agent-only') ? ['agent'] : ['baseline', 'evidence', 'agent'];
  const persist = () => writeFileSync(filename, JSON.stringify({ protocol: 'shared-generation-validation-v3', model: settings.model, promptVersion: PROMPT_VERSION,
    corpusVersion: manifest.version, releaseId: CURRENT_RELEASE_ID, temperature: 0.2, caseVersion: data.version, budget,
    note: '开发集模块冒烟，不是独立测试集或完整多轮实验。三个版本共用生成Prompt、结构/目标/归属校验和一次修复；baseline仅给卦辞，evidence给固定取证，agent额外开放工具。旧版历史数字不可直接合并。原cases中的test标签未被升级为独立留出声明。', rows }, null, 2));
  persist();
  for (const c of cases) {
    for (const variant of variants) {
      if (budget.stopped) { rows.push({ caseId: c.id, variant, outcome: 'not_run_budget_exhausted' }); persist(); continue; }
      const s: Session = { id: randomUUID(), question: '面对工作变化，我该怎样思考？', createdAt: new Date().toISOString(), turns: [], releaseId: CURRENT_RELEASE_ID,
        cast: { ...calculateHexagram(c.lines), id: 'evaluation-fixed-cast', method: 'three-coins', algorithmVersion: 'coins-v2.1', createdAt: 'fixed' } };
      const start = performance.now(), startCalls = budget.calls;
      const override = variant === 'baseline' ? corpus.filter(e => e.layer === 'classic' && e.section === 'judgment' && [s.cast.original.number, s.cast.changed.number].includes(e.hexagramId)) : undefined;
      const result = await runAgent(s, c.question, c.mode, chat, { disableTools: variant !== 'agent', initialEvidenceOverride: override });
      const { answer, evidence, trace, error } = result;
      const validation = validateAnswer(answer, evidence);
      const citations = answer?.paragraphs.flatMap(p => p.citations) || [];
      const cited = new Set(citations.map(cit => cit.evidenceId));
      const factualCitations = citations.filter(cit => !validateAnswer({ status: 'answered', summary: 'check', paragraphs: [{ text: 'check', citations: [cit] }], limitations: [] }, evidence).errors.length).length;
      const requiredHit = c.required.every((id: string) => cited.has(id));
      const authorHit = !c.requiredAuthor || evidence.some(e => cited.has(e.id) && e.authorIds?.includes(c.requiredAuthor));
      const tools = trace.filter(e => e.step === 'tool');
      const safeFallback = trace.filter(e => e.step === 'validate').at(-1)?.status === 'failed';
      const row = { caseId: c.id, variant, question: c.question, expectationMigration: c.expectationMigration,
        status: answer?.status, error, schemaAndCitationValid: !error && !validation.errors.length,
        citedCount: citations.length, validCitationCount: factualCitations, requiredEvidenceCited: requiredHit,
        requiredAuthorCited: authorHit, expectedStatus: c.expected, expectedStatusMatched: answer?.status === c.expected,
        expectedTool: c.tool, expectedToolUsed: !c.tool || tools.some(t => t.tool === c.tool && t.status === 'ok'),
        safeFallback, mechanicalTaskPass: !error && !safeFallback && !validation.errors.length && requiredHit && authorHit && answer?.status === c.expected,
        durationMs: Math.round(performance.now() - start), totalTokens: trace.reduce((n, e) => n + (e.totalTokens || 0), 0),
        modelCalls: budget.calls - startCalls, toolCalls: tools.length, validationErrors: validation.errors, answer, evidenceIds: evidence.map(e => e.id), trace,
        semanticSupportReview: 'pending', relevanceReview: 'pending' };
      rows.push(row); persist(); console.log(JSON.stringify({ case: row.caseId, variant, pass: row.mechanicalTaskPass, status: row.status, error, tokens: row.totalTokens, tools: row.toolCalls }));
    }
  }
  console.log('Saved ' + filename);
}
