import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { z } from 'zod';
import type { Cast, Evidence } from '../shared/types';
import type { RetrievalResult, Retriever } from '../shared/knowledge';
const base = new URL('../data/', import.meta.url);
const readJSON = (path: string) => JSON.parse(readFileSync(new URL(path, base), 'utf8'));
const legacyManifest = readJSON('manifest.json');
export const LEGACY_RELEASE_ID: string = legacyManifest.version;
export const CURRENT_RELEASE_ID: string = readJSON('knowledge/active.json').releaseId;
const releases = new Map<string, { corpus: Evidence[]; manifest: any }>();
export function resolveReleaseId(candidate?: string): string {
  const id = candidate || CURRENT_RELEASE_ID;
  if (id === LEGACY_RELEASE_ID) return id;
  if (!/^[a-zA-Z0-9_-]{1,90}$/.test(id) || !existsSync(new URL(`knowledge/releases/${id}/manifest.json`, base))) throw new Error('RELEASE_UNAVAILABLE');
  return id;
}
export function loadRelease(candidate?: string) {
  const id = resolveReleaseId(candidate), cached = releases.get(id); if (cached) return cached;
  const file = id === LEGACY_RELEASE_ID ? 'evidence.json' : `knowledge/releases/${id}/evidence.json`;
  const raw = readFileSync(new URL(file, base), 'utf8');
  const manifest = id === LEGACY_RELEASE_ID ? legacyManifest : readJSON(`knowledge/releases/${id}/manifest.json`);
  if (manifest.evidenceSha256 && createHash('sha256').update(raw).digest('hex') !== manifest.evidenceSha256) throw new Error('CORPUS_HASH_MISMATCH');
  const corpus: Evidence[] = JSON.parse(raw).map((e: Evidence) => ({...e,layer:e.layer||'classic',releaseId:id}));
  const result={corpus,manifest}; releases.set(id,result); return result;
}
export const {corpus,manifest} = loadRelease();
export function getCoverage(releaseId?: string) {
  const r=loadRelease(releaseId);
  return {releaseId:resolveReleaseId(releaseId),...(r.manifest.coverage || {
    classic:{hexagramIds:Array.from({length:64},(_,i)=>i+1),records:r.corpus.length,missingTuan:r.manifest.missingTuan},
    authors:{},layers:['classic'],modernEditorialRecords:0,humanReviewedRecords:0})};
}
const sections = z.enum(['judgment','line','tuan','xiang','line_xiang','special']);
const stringList = z.array(z.string().trim().min(1).max(100)).min(1).max(12);
const filters = {
  authorIds:stringList.optional(),workIds:stringList.optional(),editionIds:stringList.optional(),anchorIds:stringList.optional(),
  layers:z.array(z.enum(['classic','commentary','subcommentary','modern_editorial'])).min(1).max(4).optional(),
};
export const passageArgs = z.object({hexagramId:z.number().int().min(1).max(64),sections:z.array(sections).min(1).max(6),
  lineIndices:z.array(z.number().int().min(1).max(7)).min(1).max(7).optional(),...filters}).strict();
export const searchArgs = z.object({query:z.string().trim().min(1).max(160),hexagramId:z.number().int().min(1).max(64).optional(),
  topK:z.number().int().min(1).max(6).default(4),...filters}).strict();
type Filters = z.infer<typeof searchArgs> | z.infer<typeof passageArgs>;
/** Used by citation validation: deliberately does NOT convert traditional/simplified characters. */
export function normalize(s:string) {return s.normalize('NFKC').replace(/[\s\p{P}\p{S}]/gu,'');}
const charMap = new Map<string,string>();
for(const line of readFileSync(new URL('knowledge/raw/opencc-TSCharacters.txt',base),'utf8').split('\n')) {
  const [from,to]=line.split('\t');if(from && to) charMap.set(from,to.split(' ')[0]);
}
/** Search only. OpenCC character dictionary is a lexical bridge, not a full phrase conversion engine. */
export function normalizeSearch(s:string) {return Array.from(normalize(s)).map(c=>charMap.get(c)||c).join('').replace(/恆/g,'恒');}
const nAnchor=(e:Evidence)=>e.anchorIds || [`zhouyi:${String(e.hexagramId).padStart(2,'0')}:${e.section}${e.line===null?'':':'+e.line}`];
function allowed(e:Evidence,a:Filters) {
  const layers=a.layers || ((a.authorIds||a.workIds) ? undefined : ['classic']);
  return (!a.hexagramId||a.hexagramId===e.hexagramId) && (!layers||layers.includes(e.layer||'classic'))
    && (!a.authorIds||a.authorIds.some(id=>e.authorIds?.includes(id))) && (!a.workIds||a.workIds.includes(e.workId||'zhouyi'))
    && (!a.editionIds||a.editionIds.includes(e.editionId||'open-iching-v2')) && (!a.anchorIds||a.anchorIds.some(id=>nAnchor(e).includes(id)));
}
function targetKeys(a:Filters) {
  if(a.anchorIds) return a.anchorIds;
  if(!('sections' in a)) return [];
  return a.sections.flatMap(section=>['line','line_xiang','special'].includes(section)
    ? (a.lineIndices || (section==='special'?[7]:[1,2,3,4,5,6])).map(line=>`${a.hexagramId}:${section}:${line}`)
    : [`${a.hexagramId}:${section}`]);
}
function matchesTarget(e:Evidence,target:string) {
  return target.startsWith('zhouyi:')?nAnchor(e).includes(target):`${e.hexagramId}:${e.section}${e.line===null?'':':'+e.line}`===target;
}
function result(a:Filters, all:Evidence[], found:Evidence[], channel:'exact'|'lexical', releaseId:string,
  ranked:{e:Evidence;score:number}[]=[], limit=18):RetrievalResult {
  // Explicit bounded evidence payload; original text is never cut mid-passage.
  const evidence:Evidence[]=[];let chars=0;
  for(const e of found) {if(evidence.length>=limit||chars+e.text.length>12000) continue;evidence.push(e);chars+=e.text.length;}
  const missingTargets:string[]=[];
  for(const target of targetKeys(a)) {
    if(a.authorIds) for(const author of a.authorIds) {if(!all.some(e=>matchesTarget(e,target)&&e.authorIds?.includes(author))) missingTargets.push(`${target}|author:${author}`);}
    else if(!all.some(e=>matchesTarget(e,target))) missingTargets.push(target);
  }
  if(a.authorIds && !targetKeys(a).length) for(const id of a.authorIds) if(!all.some(e=>e.authorIds?.includes(id))) missingTargets.push('author:'+id);
  if(a.workIds) for(const id of a.workIds) if(!all.some(e=>e.workId===id)) missingTargets.push('work:'+id);
  const scopePresent=all.length>0;
  return {evidence,hits:evidence.map(e=>({passageId:e.id,channel,score:channel==='exact'?null:ranked.find(r=>r.e.id===e.id)?.score||0,
    matchReason:channel==='exact'?'hexagram/section/line/metadata match':'Chinese bigram BM25 + exact normalized phrase boost; metadata filters enforced'})),releaseId,
    coverageStatus:!scopePresent?'not_ingested':missingTargets.length?'partial':'covered',missingTargets,
    ...(!found.length?{noHitReason:scopePresent?'no_match' as const:'filtered_out' as const}:{}),truncated:evidence.length<found.length};
}
export function retrieveExact(args:unknown,releaseId?:string):RetrievalResult {
  const a=passageArgs.parse(args),id=resolveReleaseId(releaseId);
  const scope=loadRelease(id).corpus.filter(e=>allowed(e,a));
  const found=scope.filter(e=>a.sections.includes(e.section)&&(!a.lineIndices?.length||e.line===null||a.lineIndices.includes(e.line)));
  // For exact coverage, scope is actual requested targets, not merely another section in the same book.
  return result(a,found,found,'exact',id);
}
function terms(s:string) {const chars=Array.from(s);return chars.length<2?chars:chars.slice(0,-1).map((c,i)=>c+chars[i+1]);}
interface LexicalIndex {docs:Map<string,{counts:Map<string,number>;length:number;text:string}>;postings:Map<string,Set<string>>;average:number}
const indexes=new Map<string,LexicalIndex>();
function lexicalIndex(id:string):LexicalIndex {
  const existing=indexes.get(id);if(existing)return existing;
  const docs:LexicalIndex['docs']=new Map(),postings:LexicalIndex['postings']=new Map();let size=0;
  for(const e of loadRelease(id).corpus) {
    const text=normalizeSearch(e.text+' '+e.name+' '+e.locator+' '+(e.workTitle||'')+' '+(e.authorNames||[]).join(' '));
    const ts=terms(text),counts=new Map<string,number>();size+=ts.length;
    for(const term of ts){counts.set(term,(counts.get(term)||0)+1);if(!postings.has(term))postings.set(term,new Set());postings.get(term)!.add(e.id);}
    docs.set(e.id,{counts,length:ts.length,text});
  }
  const index={docs,postings,average:size/Math.max(docs.size,1)};indexes.set(id,index);return index;
}
function rankedLexical(a:z.infer<typeof searchArgs>,releaseId:string,scope:Evidence[]) {
  const q=normalizeSearch(a.query);if(!q)return [];
  const index=lexicalIndex(releaseId),qs=[...new Set(terms(q))],count=index.docs.size;
  const candidateIds=new Set(qs.flatMap(t=>[...(index.postings.get(t)||[])]));
  return scope.filter(e=>candidateIds.has(e.id)||index.docs.get(e.id)!.text.includes(q)).map(e=> {
    const doc=index.docs.get(e.id)!;let score=doc.text.includes(q)?12:0;
    for(const term of qs) {const tf=doc.counts.get(term)||0;if(!tf)continue;const df=index.postings.get(term)?.size||0;
      score+=Math.log(1+(count-df+0.5)/(df+0.5))*(tf*2.2)/(tf+1.2*(0.25+0.75*doc.length/index.average));}
    return {e,score};
  }).filter(r=>r.score>0).sort((x,y)=>y.score-x.score||x.e.id.localeCompare(y.e.id));
}
export function retrieveLexical(args:unknown,releaseId?:string):RetrievalResult {
  const a=searchArgs.parse(args),id=resolveReleaseId(releaseId),scope=loadRelease(id).corpus.filter(e=>allowed(e,a));
  const ranked=rankedLexical(a,id,scope);
  return result(a,scope,ranked.map(r=>r.e),'lexical',id,ranked,a.topK);
}
export function getPassages(args:unknown,releaseId?:string):Evidence[]{return retrieveExact(args,releaseId).evidence;}
export function searchKnowledge(args:unknown,releaseId?:string):Evidence[]{return retrieveLexical(args,releaseId).evidence;}
export const retriever:Retriever={fetchExact:retrieveExact,searchLexical:retrieveLexical};
export function initialEvidence(cast:Cast,question='',releaseId?:string):Evidence[] {
  const id=resolveReleaseId(releaseId), records=[
    ...getPassages({hexagramId:cast.original.number,sections:['judgment','xiang','tuan']},id),
    ...(cast.changingIndices.length?getPassages({hexagramId:cast.original.number,sections:['line','line_xiang'],lineIndices:cast.changingIndices.map(i=>i+1)},id):[]),
    ...(cast.changed.number!==cast.original.number?getPassages({hexagramId:cast.changed.number,sections:['judgment','xiang']},id):[]),
  ];
  // Fixed workflow: enrich changing-line evidence independently of named-author requests.
  // With no moving line, only lexical relevance to the question can select a line commentary.
  const pool=loadRelease(id).corpus.filter(e=>e.layer==='commentary'&&e.hexagramId===cast.original.number&&e.text.length>20&&e.text.length<=1200
    &&(!cast.changingIndices.length||cast.changingIndices.includes((e.line||0)-1)));
  const rank=question?rankedLexical(searchArgs.parse({query:question.slice(0,160),hexagramId:cast.original.number,layers:['commentary']}),id,pool):[];
  const candidates=rank.length?rank.map(r=>r.e):(cast.changingIndices.length?pool:[]);
  let budget=1800;const chosen:Evidence[]=[];
  for(const e of candidates) {if(chosen.length>=3)break;if(e.text.length>budget)continue;
    // Prefer another author before adding a second passage from one commentator.
    if(chosen.some(p=>p.authorIds?.[0]===e.authorIds?.[0]))continue;
    chosen.push(e);budget-=e.text.length;}
  return [...new Map([...records,...chosen].map(e=>[e.id,e])).values()];
}
const filterProperties={authorIds:{type:'array',items:{type:'string',enum:['wang-bi','cheng-yi','zhu-xi','kong-yingda','han-kangbo']}},
  workIds:{type:'array',items:{type:'string'}},editionIds:{type:'array',items:{type:'string'}},anchorIds:{type:'array',items:{type:'string'}},
  layers:{type:'array',items:{type:'string',enum:['classic','commentary','subcommentary','modern_editorial']}}};
export const tools=[
  {type:'function',function:{name:'get_passages',description:'按卦、爻和知识层精确读取本地证据。用户通常问实际处境：当已有原典不足以解释时，可自主选择 layers=[commentary] 读取该爻历代注解辅助理解，无须用户点名注家。默认仅原典；王弼wang-bi、程颐cheng-yi、朱熹zhu-xi仅部分卦爻已收录。显式作者过滤绝不以他家替代；返回覆盖/缺失/截断信息。爻位1..6，7仅兼容用九/用六。',parameters:{type:'object',properties:{hexagramId:{type:'integer',minimum:1,maximum:64},sections:{type:'array',items:{type:'string',enum:['judgment','line','tuan','xiang','line_xiang','special']}},lineIndices:{type:'array',items:{type:'integer',minimum:1,maximum:7}},...filterProperties},required:['hexagramId','sections'],additionalProperties:false}}},
  {type:'function',function:{name:'search_knowledge',description:'本地原典与注解的简繁归一词面检索（非语义搜索）。用户没有指定注家时也可为解释疑问主动补充依据。找注解时显式 layers=[commentary]；同时找原典与注解用layers=[classic,commentary]。可先从上下文提炼进退、守正等古文关键词；词面不命中不代表原书没有。出处未知时不要默认限定当前卦。返回来源、归属、实际覆盖与截断，不提供网页抓取或未收录现代书籍。',parameters:{type:'object',properties:{query:{type:'string',maxLength:160},hexagramId:{type:'integer',minimum:1,maximum:64},topK:{type:'integer',minimum:1,maximum:6},...filterProperties},required:['query'],additionalProperties:false}}},
];
export function executeToolResult(name:string,args:unknown,releaseId?:string):RetrievalResult {
  if(name==='get_passages')return retrieveExact(args,releaseId);if(name==='search_knowledge')return retrieveLexical(args,releaseId);throw new Error('UNKNOWN_TOOL');
}
export function executeTool(name:string,args:unknown,releaseId?:string):Evidence[]{return executeToolResult(name,args,releaseId).evidence;}
