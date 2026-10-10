#!/usr/bin/env python3
"""Offline, non-mutating collation of frozen Song commentary against pinned Kanripo snapshots.
No similarity threshold declares a historical text correct; differences remain review findings.
"""
import json,re,unicodedata,difflib,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/knowledge/review/crosscheck-20260926/song'
MAP={}
for line in (ROOT/'data/knowledge/raw/opencc-TSCharacters.txt').read_text().splitlines():
 if '\t' in line and not line.startswith('#'):
  a,b=line.split('\t');MAP[a]=b.split()[0]
# Displayed in report, never applied to stored source or evidence text.
VARIANTS=dict(zip('无隂㫁𧰼𥘉㣲𫝑㫖飬𤣥徳歴値賔内羣强衆逺䝉','無陰斷象初微勢旨養玄德歷值賓內群強眾遠蒙'))
def norm(s):
 s=re.sub(r'<pb:[^>]+>|^#.*$','',s,flags=re.M)
 return ''.join(MAP.get(VARIANTS.get(c,c),VARIANTS.get(c,c)) for c in unicodedata.normalize('NFKC',s) if '\u3400'<=c<='\u9fff' or '\U00020000'<=c<='\U0003134f')
def docnorm(s):
 out=[];positions=[];blocked=set()
 for m in re.finditer(r'<pb:[^>]+>|^#.*$',s,flags=re.M):blocked.update(range(m.start(),m.end()))
 for i,c in enumerate(s):
  if i not in blocked:
   for x in norm(c):out.append(x);positions.append(i)
 return ''.join(out),positions
sources=json.loads((OUT/'sources.json').read_text());docs={}
for m in sources:
 if m.get('path','').endswith('.txt') and not m['path'].endswith('_000.txt'):
  raw=(OUT/m['localFile']).read_text();n,pos=docnorm(raw);docs[m['localFile']]=(m,raw,n,pos)
def locate(needle,repo,hexid):
 candidates=[]
 for fn,(m,raw,hay,pos) in docs.items():
  if m['repo']!=repo:continue
  glyph=chr(0x4dc0+hexid-1);hstart=raw.find(glyph)
  if hstart<0:continue
  nxt=re.search('[\u4dc0-\u4dff]',raw[hstart+1:]);hend=hstart+1+nxt.start() if nxt else len(raw)
  import bisect
  lo=bisect.bisect_left(pos,hstart);hi=bisect.bisect_left(pos,hend)
  hay=hay[lo:hi];pos=pos[lo:hi]
  idx=hay.find(needle)
  if idx>=0:return record(m,raw,hay,pos,idx,idx+len(needle),needle)
  longest=difflib.SequenceMatcher(None,needle,hay,autojunk=False).find_longest_match()
  if longest.size<min(8,len(needle)//2):continue
  start=max(0,longest.b-longest.a)
  # Longest block anchors candidate; semiglobal alignment removes neighbouring prose.
  window=hay[max(0,start-20):start+len(needle)+50];offset=max(0,start-20)
  sm=difflib.SequenceMatcher(None,needle,window,autojunk=False)
  blocks=[b for b in sm.get_matching_blocks() if b.size]
  if not blocks:continue
  begin=offset+blocks[0].b-blocks[0].a;end=offset+blocks[-1].b+blocks[-1].size+(len(needle)-blocks[-1].a-blocks[-1].size)
  begin=max(0,begin);end=min(len(hay),end)
  candidate=record(m,raw,hay,pos,begin,end,needle)
  candidates.append(candidate)
  # Repeated sentence endings should not include an entire neighbouring annotation.
  for tail in re.finditer(re.escape(needle[-2:]),hay[max(0,start-10):start+len(needle)+50]):
   stop=max(0,start-10)+tail.end()
   if stop>begin:candidates.append(record(m,raw,hay,pos,begin,stop,needle))
 return max(candidates,key=lambda x:x['similarity']) if candidates else None
def record(m,raw,hay,pos,start,end,needle):
 other=hay[start:end];a,b=pos[start],pos[end-1]+1
 ops=[]
 for tag,i,j,k,l in difflib.SequenceMatcher(None,needle,other,autojunk=False).get_opcodes():
  if tag!='equal':ops.append(dict(op=tag,current=needle[i:j],witness=other[k:l],currentNormalizedOffset=[i,j],witnessNormalizedOffset=[k,l],context=needle[max(0,i-7):min(len(needle),j+7)]))
 before=raw[:a];page=re.findall(r'<pb:([^>]+)>',before)
 return dict(url=m['url'],repo=m['repo'],commit=m['commit'],file=m['localFile'],sha256=m['sha256'],lineStart=before.count('\n')+1,lineEnd=raw[:b].count('\n')+1,page=page[-1] if page else None,matchedRawText=raw[a:b],normalizedWitness=other,similarity=difflib.SequenceMatcher(None,needle,other,autojunk=False).ratio(),differences=ops)
def third_text(r):
 from html.parser import HTMLParser
 class Text(HTMLParser):
  def __init__(self):super().__init__();self.parts=[];self.skip=0
  def handle_starttag(self,tag,attrs):
   if tag in ('script','style'):self.skip+=1
  def handle_endtag(self,tag):
   if tag in ('script','style'):self.skip=max(0,self.skip-1)
   if tag in ('p','div','h1','h2','br'):self.parts.append('\n')
  def handle_data(self,data):
   if not self.skip:self.parts.append(data)
 src=next(m for m in sources if m['localFile']==f"raw/eee-cheng-{r['hexagramId']}.html")
 parser=Text();parser.feed((OUT/src['localFile']).read_text());raw=''.join(parser.parts);hay,pos=docnorm(raw);needle=norm(r['text']);idx=hay.find(needle)
 m=dict(src,repo='eee-learning',commit=None)
 if idx>=0:return record(m,raw,hay,pos,idx,idx+len(needle),needle)
 long=difflib.SequenceMatcher(None,needle,hay,autojunk=False).find_longest_match();start=max(0,long.b-long.a);c=[]
 for end in range(max(start+1,start+len(needle)-20),min(len(hay),start+len(needle)+20)):
  c.append(record(m,raw,hay,pos,start,end,needle))
 return max(c,key=lambda x:x['similarity'])
rows=[]
for r in json.loads((ROOT/'data/knowledge/releases/knowledge-v3-20260926/evidence.json').read_text()):
 author=next(iter(set(r.get('authorIds',[]))&{'cheng-yi','zhu-xi'}),None)
 if not author:continue
 checks=[]
 for repo in (['KR1a0016'] if author=='cheng-yi' else ['KR1a0032','KR1a0031']):
  text=r['text'];note=None
  if repo=='KR1a0031' and '○' in text:text=text.split('○',1)[1];note='原本本未含当前别本的前置音义；只对读○后的注释正文，音义不算缺文'
  match=locate(norm(text),repo,r['hexagramId'])
  if match:match['scopeNote']=note;checks.append(match)
 if author=='cheng-yi':checks.append(third_text(r))
 row=dict(evidenceId=r['id'],author=author,hexagramId=r['hexagramId'],line=r['line'],anchorIds=r['anchorIds'],currentText=r['text'],currentSource=r['source'],currentEdition=r['editionId'],reviewMode='machine_assisted_not_human',reviewedAt=datetime.now(timezone.utc).isoformat(),status=('text_agrees_after_declared_normalization' if checks and all(not c['differences'] for c in checks) else 'differences_require_review'),sourceRelationship='Wikisource与Kanripo为不同发布平台；同四库系统底本的转录谱系未证实独立。朱熹0031原本与0032别本为同库两种编次/版本，非两个独立平台。不得把一致次数当独立票数。',checks=checks)
 rows.append(row)
(OUT/'records.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
print('rows',len(rows))
for r in rows:
 print(r['evidenceId'],[(c['repo'],round(c['similarity'],3),[(d['current'],d['witness']) for d in c['differences']]) for c in r['checks']])
(OUT/'normalization.json').write_text(json.dumps(dict(openccSource='data/knowledge/raw/opencc-TSCharacters.txt',variants=VARIANTS,rule='NFKC; remove header/page markup/non-Han/punctuation; apply declared glyph map then single-character OpenCC TS; raw unchanged',warning='Normalization supports comparison, not correction or proof of equal meaning.'),ensure_ascii=False,indent=2)+'\n')
# Keep normalization differences visible, but distinguish common glyphs from unresolved wording.
GLYPH_PAIRS={frozenset(x) for x in ['冰氷','统綂','涉渉','怀懐','盖葢','悦恱','谄謟','沉沈','耽躭','专専','冥㝠','高髙','缘縁','赞賛','遯遁','陷䧟','囘回','寇冦','往徃','宜冝','类𩔖','簪簮','昏昬','补𥙷','著着']}
def is_glyph(d):
 a,b=d['current'],d['witness']
 return len(a)==len(b) and all(x==y or frozenset((x,y)) in GLYPH_PAIRS for x,y in zip(a,b))
for r in rows:
 for c in r['checks']:
  for d in c['differences']:d['classification']='orthographic_variant' if is_glyph(d) else 'wording_difference_pending'
  c['assessment']='agrees_after_declared_normalization' if not c['differences'] else ('orthographic_variants_only' if all(is_glyph(d) for d in c['differences']) else 'version_or_transcription_difference_pending')
 primary=r['checks'][0]
 r['primaryComparison']=primary['assessment']
 r['status']='cross_source_agreement' if all(c['assessment']=='agrees_after_declared_normalization' for c in r['checks']) else ('orthographic_variants_only' if all(c['assessment']!='version_or_transcription_difference_pending' for c in r['checks']) else 'version_or_transcription_difference_pending')
 r['attributionCheck']={'workTitleAndAuthor':'matching title/author headings in downloaded witnesses','hexagram':'comparison restricted to matching Unicode hexagram section in Kanripo','line':'matched commentary text and surrounding locus retained; no scholarly authorship verification claimed','caution':'book-level attribution does not establish authorship of every phonetic gloss or quoted ancient phrase'}
 r['sourceRelationship']='当前维基文库；Kanripo为另一个发布平台，0031原本/0032别本为同库不同编次，不能算两个独立平台。程传还与易学网具名校对版逐条对比；各电子转录的谱系未确认独立。影像只核对明确列出的页，不能宣称整库影像复核。'
 if r['author']=='cheng-yi' and r['hexagramId']==1 and r['line']==3:
  r['scanCheck']={'url':'https://www.eee-learning.com/eeebook/yichen/yi-chen-juan013.gif','localFile':'raw/eee-cheng-scan-13.png','locus':'卷一二b，右数第五栏起','reading':'三雖臣位','currentReading':'三雖人位','reviewMode':'assistant_visual_not_human','finding':'扫描与Kanripo为臣；当前及易学网转录为人。证实与该四库影像不一致；当前通行本底本未明，不把版本差异直接覆盖。'}
(OUT/'records.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
from collections import Counter
summary={'release':'knowledge-v3-20260926','evidenceCount':len(rows),'authorCounts':dict(Counter(r['author'] for r in rows)),'statusCounts':dict(Counter(r['status'] for r in rows)),'primaryComparisonCounts':dict(Counter(r['primaryComparison'] for r in rows)),'directScanInspectedEvidenceCount':1,'reviewMode':'machine_assisted_not_human','reviewConclusion':'Cross-source concordance is not certification. No production evidence changed.'}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False))
