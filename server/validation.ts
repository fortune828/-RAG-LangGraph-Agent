import { z } from 'zod';
import type { Answer, Evidence, Cast } from '../shared/types';
import { HEXAGRAMS } from '../src/lib/yijing';
// Search normalization may fold simplified/traditional forms. A displayed quote
// must still match the supplied version, apart from punctuation and whitespace.
const normalizeQuote = (s: string) => s.normalize('NFKC').replace(/[\s\p{P}\p{S}]/gu, '');
export interface EvidenceTarget {
  hexagramId: number; section?: Evidence['section']; line?: number;
  layer: 'classic' | 'commentary'; authorId?: string; workId?: string; anchorId?: string;
}
const AUTHORS: Record<string, string[]> = {
  'wang-bi': ['王弼'], 'cheng-yi': ['程颐', '程頤', '伊川'], 'zhu-xi': ['朱熹'],
  'kong-yingda': ['孔颖达', '孔穎達'], 'han-kangbo': ['韩康伯', '韓康伯'],
  'jiao-xun': ['焦循'],
};
const WORKS: Record<string, string[]> = {
  'zhouyi-zhengyi': ['周易正义', '周易正義'],
  'yichuan-yizhuan': ['伊川易传', '伊川易傳'],
  'zhouyi-benyi': ['周易本义', '周易本義'],
};
const unavailable = (text: string) => /未收录|未收錄|没有.*(?:材料|资料|資料|注解)|缺少|缺乏|资料不足|資料不足|尚无|尚無|无法确认|無法確認|不能确认|不能確認/.test(text);
export function mentionedAuthors(text: string) {
  return Object.entries(AUTHORS).filter(([, names]) => names.some(n => text.includes(n))).map(([id]) => id);
}
function assertedAuthors(text: string) {
  const sentences = text.split(/[。！？；\n]/);
  return mentionedAuthors(text).filter(author => sentences.some(sentence => {
    const names = AUTHORS[author].filter(name => sentence.includes(name));
    if (!names.length) return false;
    const explicitClaim = names.some(name => new RegExp(name + '.{0,10}(认为|認為|强调|強調|指出|主张|主張|解释为|解釋為|说|曰|看来|看來)').test(sentence));
    return explicitClaim || !unavailable(sentence);
  }));
}
function namedHexagrams(question: string) {
  return Object.values(HEXAGRAMS).filter(g => {
    const name = g.name.includes('为') ? g.name.split('为')[0] : g.name.slice(2);
    return question.includes(name + '卦') || question.includes(g.name);
  }).map(g => g.number);
}
export const answerSchema = z.object({
  status: z.enum(['answered', 'insufficient']), summary: z.string().min(1).max(600),
  paragraphs: z.array(z.object({ text: z.string().min(1).max(2000),
    citations: z.array(z.object({ evidenceId: z.string(), quote: z.string().min(2).max(600) }).strict()).max(5),
  }).strict()).max(6), limitations: z.array(z.string().max(600)).max(8),
}).strict();
// Narrow, inspectable task constraints, not a claim of general semantic entailment.
export function requestedEvidence(question: string, cast: Cast): string[] {
  const named = namedHexagrams(question);
  const ids = named.length ? named : [cast.original.number];
  const numberMap: Record<string, number> = { 一:1,二:2,三:3,四:4,五:5,六:6,'1':1,'2':2,'3':3,'4':4,'5':5,'6':6 };
  const lines = [...question.matchAll(/第?([一二三四五六1-6])爻/g)].map(m => numberMap[m[1]]);
  for (const match of question.matchAll(/[九六]([二三四五])/g)) lines.push(numberMap[match[1]]);
  if (/初九|初六/.test(question)) lines.push(1);
  if (/上九|上六/.test(question)) lines.push(6);
  if (question.includes('上爻')) lines.push(6);
  if (question.includes('初爻')) lines.push(1);
  if (lines.length) {
    const sections = /小象|象传|象傳/.test(question) ? /爻辞|爻辭/.test(question) ? ['line', 'line_xiang'] : ['line_xiang'] : ['line'];
    return ids.flatMap(id => [...new Set(lines)].flatMap(l => sections.map(section => `zy-${id}-${section}-${l}`)));
  }
  if (/用九|用六/.test(question)) return ids.map(id => `zy-${id}-special-7`);
  const explicitTarget = named.length > 0 || /本卦|当前|本次|刚才/.test(question);
  if (explicitTarget && /卦辞|卦辭/.test(question)) return ids.map(id => `zy-${id}-judgment`);
  if (explicitTarget && /彖传/.test(question)) return ids.map(id => `zy-${id}-tuan`);
  if (explicitTarget && /大象|象传/.test(question) && /原文|出处|查询|查出|请查/.test(question)) return ids.map(id => `zy-${id}-xiang`);
  return [];
}
// Targets describe the requested object, independent of a corpus's passage IDs.
export function requestedTargets(question: string, cast: Cast): EvidenceTarget[] {
  const authors = mentionedAuthors(question);
  const classicTargets = requestedEvidence(question, cast).map(id => {
    const match = /^zy-(\d+)-(judgment|line_xiang|line|special|tuan|xiang)(?:-(\d+))?$/.exec(id)!;
    const hexagramId = Number(match[1]), section = match[2] as Evidence['section'];
    const line = match[3] ? Number(match[3]) : undefined;
    const suffix = section === 'special' ? `special:${hexagramId === 1 ? 'use-nine' : 'use-six'}`
      : `${section}${line ? ':' + line : ''}`;
    const anchorId = `zhouyi:${String(hexagramId).padStart(2, '0')}:${suffix}`;
    return { hexagramId, section, line, layer: 'classic' as const, anchorId };
  });
  const workIds = Object.entries(WORKS).filter(([, names]) => names.some(name => question.includes(name))).map(([id]) => id);
  if (!authors.length && !workIds.length) return classicTargets;
  const named = namedHexagrams(question);
  const objects = classicTargets.length ? classicTargets : (named.length ? named : [cast.original.number]).map(hexagramId => ({ hexagramId }));
  const annotations: EvidenceTarget[] = objects.flatMap<EvidenceTarget>(target => authors.length
    ? authors.map(authorId => ({ ...target, layer: 'commentary' as const, authorId }))
    : workIds.map(workId => ({ ...target, layer: 'commentary' as const, workId })));
  return /原文.*(?:及|和|与|以及).*注|(?:原文|经文).*解释/.test(question) ? [...classicTargets, ...annotations] : annotations;
}
export function matchesTarget(e: Evidence, target: EvidenceTarget) {
  if (e.hexagramId !== target.hexagramId) return false;
  const layer = e.layer || 'classic';
  if (target.layer === 'classic' ? layer !== 'classic' : !['commentary', 'subcommentary'].includes(layer)) return false;
  if (target.authorId && !e.authorIds?.includes(target.authorId)) return false;
  if (target.workId && e.workId !== target.workId) return false;
  if (target.section && e.section !== target.section && !(target.anchorId && e.commentsOn?.includes(target.anchorId))) return false;
  if (target.line && e.line !== target.line && !(target.anchorId && e.commentsOn?.includes(target.anchorId))) return false;
  return true;
}
export function validateAnswer(value: unknown, evidence: Evidence[], requiredIds: string[] = [], targets: EvidenceTarget[] = []): { answer?: Answer; errors: string[] } {
  const parsed = answerSchema.safeParse(value);
  if (!parsed.success) return { errors: ['INVALID_ANSWER_SCHEMA'] };
  const a = parsed.data, errors: string[] = [];
  if (a.status === 'answered' && !a.paragraphs.length) errors.push('EMPTY_ANSWER_PARAGRAPHS');
  const byId = new Map(evidence.map(e => [e.id, e]));
  const citedIds = new Set(a.paragraphs.flatMap(p => p.citations.map(c => c.evidenceId)));
  if (a.status === 'answered') for (const id of requiredIds) {
    if (!citedIds.has(id)) errors.push('REQUIRED_EVIDENCE_NOT_CITED:' + id);
  }
  if (a.status === 'answered') for (const target of targets) {
    if (!evidence.some(e => citedIds.has(e.id) && matchesTarget(e, target)))
      errors.push('REQUIRED_TARGET_NOT_CITED:' + JSON.stringify(target));
  }
  if (a.status === 'insufficient' && !a.limitations.length) errors.push('MISSING_LIMITATION');
  for (const p of a.paragraphs) {
    if (a.status === 'answered' && !p.citations.length) errors.push('MISSING_CITATION');
    for (const c of p.citations) {
      const e = byId.get(c.evidenceId);
      if (!e) errors.push('UNKNOWN_EVIDENCE:' + c.evidenceId);
      else if (!normalizeQuote(e.text).includes(normalizeQuote(c.quote)) || normalizeQuote(c.quote).length < 2)
        errors.push('QUOTE_MISMATCH:' + c.evidenceId);
    }
    // Conservative author attribution check, not semantic entailment. Explicit
    // mentions in a paragraph require a cited passage attributed to that author.
    for (const author of assertedAuthors(p.text)) {
      if (!p.citations.some(c => byId.get(c.evidenceId)?.authorIds?.includes(author)))
        errors.push('AUTHOR_ATTRIBUTION_MISMATCH:' + author);
    }
    for (const [workId, names] of Object.entries(WORKS)) {
      const sentences = p.text.split(/[。！？；\n]/).filter(s => names.some(name => s.includes(name)));
      if (sentences.some(s => !unavailable(s)) && !p.citations.some(c => byId.get(c.evidenceId)?.workId === workId))
        errors.push('WORK_ATTRIBUTION_MISMATCH:' + workId);
    }
  }
  for (const author of assertedAuthors(a.summary)) {
    if (!evidence.some(e => citedIds.has(e.id) && e.authorIds?.includes(author)))
      errors.push('SUMMARY_AUTHOR_ATTRIBUTION_MISMATCH:' + author);
  }
  return { answer: errors.length ? undefined : a, errors: [...new Set(errors)] };
}
export function fallback(reason: string): Answer {
  return { status: 'insufficient', summary: '这次未能生成通过核验的完整解读。',
    paragraphs: [{ text: '本次卦象已保留。你可以查看下方原文依据，或对同一结果重试解释。', citations: [] }],
    limitations: [reason, '引用校验只验证来源和文字匹配，不能自动证明解释的语义正确。'] };
}
