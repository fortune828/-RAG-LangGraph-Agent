"""Align only within the explicit source volume, hexagram block, and line heading."""
import pathlib,json,re,unicodedata,difflib
ROOT=pathlib.Path(__file__).resolve().parents[5]
p=pathlib.Path(__file__).parent
mapping={}
for line in (ROOT/'data/knowledge/raw/opencc-TSCharacters.txt').read_text().splitlines():
 if line and not line.startswith('#'):
  a,b=line.split('\t');mapping[a]=b.split(' ')[0]
def norm(s):
 return ''.join(c for c in unicodedata.normalize('NFKC',''.join(mapping.get(c,c) for c in s)) if unicodedata.category(c).startswith('L'))
def headingnorm(s):return norm(s).translate(str.maketrans({'濳':'潜','㝠':'冥','卽':'即','渉':'涉','宼':'寇'}))
# Verified against JUAN headers and hexagram glyph blocks, not numeric filename assumptions.
volumes={1:(12,'卷一'),2:(14,'卷二'),3:(14,'卷二'),4:(14,'卷二'),15:(18,'卷四'),16:(18,'卷四'),29:(20,'卷五'),32:(22,'卷六')}
allitems=json.load(open(ROOT/'data/knowledge/releases/knowledge-v3-20260926/evidence.json'))
byid={e['id']:e for e in allitems};a=[e for e in allitems if 'wang-bi' in e.get('authorIds',[])]
res=[]
for e in a:
 h=e['hexagramId'];line=e['line'];file_no,juan=volumes[h];fn=f'KR1a0007_{file_no:03}.txt';raw=(p/fn).read_text()
 assert f'#+PROPERTY: JUAN {juan}' in raw
 glyph=chr(0x4dc0+h-1);starts=list(re.finditer(r'^([\u4dc0-\u4dff])',raw,re.M));hits=[(i,m) for i,m in enumerate(starts) if m[1]==glyph];assert len(hits)==1,(h,'hexagram glyph')
 i,g=hits[0];lo=g.start();hi=starts[i+1].start() if i+1<len(starts) else len(raw);block=raw[lo:hi]
 classic=byid[f'zy-{h}-line-{line}'];label=classic['locator'].split('·')[-1];prefix=headingnorm(classic['text'])[:2]
 titles=[m for m in re.finditer(r'^'+re.escape(label)+r'[^\n]*',block,re.M) if headingnorm(m[0][len(label):]).startswith(prefix)]
 assert len(titles)==1,(h,line,label,prefix,len(titles))
 title=titles[0];start=lo+title.start()
 # Preserve original character positions through markup/layout removal.
 skipped=set()
 for m in re.finditer(r'<pb:[^>]+>',raw[start:hi]):skipped.update(range(start+m.start(),start+m.end()))
 chars=[];pos=[]
 for j in range(start,hi):
  if j not in skipped and not raw[j].isspace() and raw[j] not in '¶/()':chars.append(raw[j]);pos.append(j)
 flat=''.join(chars)
 m=re.search(r'注(.*?)(?:音義|疏|象曰)',flat)
 assert m and m.start()<120,(h,line,'direct comment boundary')
 ref=m[1];rs=pos[m.start(1)];re_=pos[m.end(1)-1]+1
 page_matches=list(re.finditer(r'<pb:([^>]+)>',raw));pages=[z[1] for z in page_matches if z.start()<=rs][-1:]+[z[1] for z in page_matches if rs<z.start()<re_]
 locator={'file':fn,'volume':juan,'hexagramId':h,'hexagramGlyph':glyph,'lineLabel':label,'headingLine':raw.count('\n',0,start)+1,'startLine':raw.count('\n',0,rs)+1,'endLine':raw.count('\n',0,re_)+1,'pageLabels':pages,'startOffset':rs,'endOffset':re_,'offsetUnit':'unicode_code_points','selection':'exact_volume_then_hexagram_glyph_then_line_label_and_classic_prefix_then_direct_comment_boundary'}
 n=norm(e['text']);rn=norm(ref);sim=difflib.SequenceMatcher(None,n,rn,autojunk=False)
 diff=[{'type':tag,'local':n[i:j],'other':rn[k:l],'context':n[max(0,i-10):min(len(n),j+10)]} for tag,i,j,k,l in sim.get_opcodes() if tag!='equal']
 res.append({'id':e['id'],'local':e['text'],'ref':ref,'file':fn,'ratio':sim.ratio(),'diff':diff,'sourceLocator':locator})
 print(h,line,fn,locator['startLine'],locator['endLine'],','.join(pages),round(sim.ratio(),3))
(p/'preliminary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
