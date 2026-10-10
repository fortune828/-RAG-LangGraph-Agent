import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import type { Evidence } from '../../shared/types';
import { parseWangBi, parseChengYi, parseZhuXi, cleanWiki } from './parse';
const root = new URL('../../data/knowledge/', import.meta.url);
const read = (file: string) => readFileSync(new URL(file, root), 'utf8');
const json = (file: string) => JSON.parse(read(file));
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
export const RELEASE_ID = 'knowledge-v3-20260926';
const sources = json('sources.json');
for (const s of sources) if (hash(read(s.rawFile)) !== s.sha256) throw new Error('SOURCE_HASH_MISMATCH:' + s.sourceId);
const legacy: Evidence[] = JSON.parse(readFileSync(new URL('../../data/evidence.json', import.meta.url), 'utf8'));
const oldManifest = JSON.parse(readFileSync(new URL('../../data/manifest.json', import.meta.url), 'utf8'));
const names = new Map(legacy.map(e => [e.hexagramId, e.name]));
export function anchor(id: number, section: string, line: number | null) {
  if (line === 7) return `zhouyi:${String(id).padStart(2,'0')}:${section === 'line_xiang' ? 'special_xiang' : 'special'}:${id === 1 ? 'use-nine' : 'use-six'}`;
  return `zhouyi:${String(id).padStart(2,'0')}:${section}${line === null ? '' : ':' + line}`;
}
const records: Evidence[] = legacy.map(e => ({ ...e, layer: 'classic', workId:'zhouyi', workTitle:'周易',
  sourceId: e.source.includes('wikisource') ? 'legacy-ws-2404991' : 'open-iching-4c5f00f8cb62',
  editionId: 'open-iching-v2', editionLabel:'Open I Ching 固定快照（未逐条校勘）',
  authorIds:[], authorNames:[], anchorIds:[anchor(e.hexagramId,e.section,e.line)], commentsOn:[],
  reviewStatus:'legacy_unreviewed', releaseId:RELEASE_ID, textOriginal:e.text }));
const reviews: any[] = [], excluded: any[] = [];
const works = [
  {workId:'zhouyi',title:'周易',contributors:[], note:'原典作者归属存在传统说法，本项目不强行逐段指定现代意义作者'},
  {workId:'zhouyi-zhengyi',title:'周易正義',contributors:[{personId:'wang-bi',name:'王弼',role:'注'},{personId:'han-kangbo',name:'韩康伯',role:'注'},{personId:'kong-yingda',name:'孔颖达',role:'疏'}],note:'试点仅抽六爻经文后明确 * 模板中的王弼注；未导入韩注或孔疏'},
  {workId:'yichuan-yizhuan',title:'伊川易傳',contributors:[{personId:'cheng-yi',name:'程颐',role:'传'}]},
  {workId:'zhouyi-benyi',title:'周易本義',contributors:[{personId:'zhu-xi',name:'朱熹',role:'注'}]},
];
const pilot = [1,2,3,4,15,16,29,32];
const wangRevs: Record<number,string> = {1:'2676007',2:'2404938',3:'2689596',4:'2404940',15:'2000726',16:'7904495',29:'2404949',32:'2404960'};
for (const id of pilot) for (const personId of ['wang-bi','cheng-yi','zhu-xi']) {
  const rev = personId === 'wang-bi' ? wangRevs[id] : personId === 'cheng-yi' ? '1313743' : id <= 30 ? '528287' : '737053';
  const source = sources.find((s: any) => s.sourceId === 'ws-' + rev), raw = read(source.rawFile);
  const parsed = personId === 'wang-bi' ? parseWangBi(raw) : personId === 'cheng-yi' ? parseChengYi(raw,id) : parseZhuXi(raw,id);
  const work = works.find(w => w.contributors.some(c => c.personId === personId))!;
  const author = work.contributors.find(c => c.personId === personId)!;
  for (const n of parsed.notes) {
    const e: Evidence = {id:`${personId}@${rev}:h${id}:line${n.line}:${hash(n.text).slice(0,10)}`,
      hexagramId:id, name:names.get(id)!,section:'line',line:n.line,text:n.text,textOriginal:n.text,
      source:source.url,locator:`《${work.title}》·${names.get(id)}·第${n.line}爻注`, version:'ws-'+rev,
      review:'机器规则提取，已保留来源与边界；未经人工校勘，异文未擅改',reviewStatus:'machine_parsed',
      layer:'commentary',workId:work.workId,workTitle:work.title,sourceId:source.sourceId,
      editionId:personId==='zhu-xi'?'ws-benyi-skqs':'ws-'+work.workId,
      editionLabel:personId==='zhu-xi'?'维基文库四库全书本（未逐页对照影像）':'维基文库通行电子文本（底本未逐页核验）',
      authorIds:[personId],authorNames:[author.name],anchorIds:[anchor(id,'line',n.line)],commentsOn:[anchor(id,'line',n.line)],
      releaseId:RELEASE_ID,rawLocator:{file:'data/knowledge/'+source.rawFile,startLine:raw.slice(0,n.start).split('\n').length,endLine:raw.slice(0,n.end).split('\n').length,pageLabels:[]}};
    records.push(e); reviews.push({passageId:e.id,status:'machine_parsed',reviewer:'deterministic-source-parser-v1',humanReviewer:null,
      date:'2026-09-26',sourceId:source.sourceId,rawStart:n.start,rawEnd:n.end,offsetUnit:'UTF-16 code units',method:n.method,
      transform:'Remove explicit wiki template / surrounding commentary parentheses; trim whitespace; render simple language-variant markup. No punctuation insertion, simplified conversion or typo repair.',issues:[]});
  }
  for (let line=1;line<=6;line++) if (!parsed.notes.some(n=>n.line===line)) excluded.push({authorId:personId,hexagramId:id,line,reason:parsed.excluded.find(n=>n.line===line)?.reason || 'No unambiguous directly attached annotation in this selected source/scope'});
}
// Fill the known missing classical passage from a separate fixed witness, never edit the old release.
const hengSource = sources.find((s:any)=>s.sourceId==='ws-2530043');
const hengRaw = read(hengSource.rawFile), hengLine = hengRaw.split('\n').find((line:string)=>line.startsWith('**恆，久也。'))!;
if (!hengLine) throw new Error('HENG_TUAN_BOUNDARY_MISSING');
const hengText=cleanWiki(hengLine.slice(2));
records.push({id:'zy-32-tuan',hexagramId:32,name:names.get(32)!,section:'tuan',line:null,text:hengText,textOriginal:hengText,
  source:hengSource.url,locator:'《周易》·恒·彖传',version:'ws-2530043',review:'机器按明确彖传边界提取；未人工校勘',reviewStatus:'machine_parsed',
  layer:'classic',workId:'zhouyi',workTitle:'周易',sourceId:hengSource.sourceId,editionId:'ws-zhouyi',editionLabel:'维基文库周易固定修订2530043',authorIds:[],authorNames:[],anchorIds:[anchor(32,'tuan',null)],commentsOn:[],releaseId:RELEASE_ID,
  rawLocator:{file:'data/knowledge/'+hengSource.rawFile,startLine:hengRaw.split('\n').indexOf(hengLine)+1,endLine:hengRaw.split('\n').indexOf(hengLine)+1,pageLabels:[]}});
if (new Set(records.map(e=>e.id)).size!==records.length) throw new Error('DUPLICATE_IDS');
const coverage = {classic:{hexagramIds:Array.from({length:64},(_,i)=>i+1),records:records.filter(e=>e.layer==='classic').length,missingTuan:[]},
  authors:Object.fromEntries(['wang-bi','cheng-yi','zhu-xi'].map(authorId=>{const es=records.filter(e=>e.authorIds?.includes(authorId));return [authorId,{name:es[0]?.authorNames?.[0],workId:es[0]?.workId,records:es.length,hexagramIds:[...new Set(es.map(e=>e.hexagramId))],anchorIds:es.flatMap(e=>e.anchorIds!),sections:['line'],scope:'selected directly attached line annotations only; not complete book'}]})),
  layers:['classic','commentary'],modernEditorialRecords:0,humanReviewedRecords:0,excludedCount:excluded.length};
const serialized=JSON.stringify(records,null,2)+'\n';
const release = {version:RELEASE_ID,releaseId:RELEASE_ID,records:records.length,legacyVersion:oldManifest.version,parserVersion:'source-specific-v1',indexVersion:'bigrams-bm25-v1',normalizerVersion:'opencc-TSCharacters-'+sources.find((s:any)=>s.sourceId==='opencc-TSCharacters.txt').upstreamRevision.slice(0,12),
  evidenceSha256:hash(serialized),sourceHashes:Object.fromEntries(sources.map((s:any)=>[s.rawFile,s.sha256])),coverage,
  sourceRevisions:sources.map((s:any)=>({sourceId:s.sourceId,revision:s.upstreamRevision})),
  licenseNotice:'Mixed sources: legacy Open I Ching learning/non-commercial restriction retained; new Wikisource classical excerpts retain attribution + CC BY-SA 4.0 notice; OpenCC dictionary Apache-2.0. No blanket new data license.',
  review:'Machine parsed, zero human-reviewed claims. See review records and exclusions.',missingTuan:[]};
const dir=new URL(`releases/${RELEASE_ID}/`,root);mkdirSync(dir,{recursive:true});
const oldPath=new URL('manifest.json',dir);
if(existsSync(oldPath)&&JSON.stringify(JSON.parse(readFileSync(oldPath,'utf8')))!==JSON.stringify(release)) throw new Error('IMMUTABLE_RELEASE_CHANGED: choose a new release ID');
const write=(file:string,data:unknown)=>writeFileSync(new URL(file,root),typeof data==='string'?data:JSON.stringify(data,null,2)+'\n');
write(`releases/${RELEASE_ID}/evidence.json`,serialized);write(`releases/${RELEASE_ID}/manifest.json`,release);
write(`releases/${RELEASE_ID}/legacy-aliases.json`,Object.fromEntries(legacy.map(e=>[e.id,e.id])));
write('works.json',works);write('review/records.jsonl',reviews.map(r=>JSON.stringify(r)).join('\n')+'\n');write('review/excluded.json',excluded);
// Rebuilding the historical V3 must not silently roll back an activated correction release.
if (!existsSync(new URL('active.json', root))) write('active.json',{releaseId:RELEASE_ID});
console.log(JSON.stringify({releaseId:RELEASE_ID,records:records.length,coverage},null,2));
