from pathlib import Path
import json,re,hashlib,collections,difflib,unicodedata
p=Path(__file__).resolve().parent;root=p.parents[4]
items=json.loads((root/'data/knowledge/releases/knowledge-v3-20260926/evidence.json').read_text());byid={x['id']:x for x in items};pre=json.loads((p/'preliminary.json').read_text());commit=(p/'commit.txt').read_text()
mapping={}
for s in (root/'data/knowledge/raw/opencc-TSCharacters.txt').read_text().splitlines():
 if s and not s.startswith('#'):
  a,b=s.split('\t');mapping[a]=b.split()[0]
def norm(t):return ''.join(c for c in unicodedata.normalize('NFKC',''.join(mapping.get(c,c) for c in t)) if unicodedata.category(c).startswith('L'))
fixes={
 (3,2):('故屯邅。「屯」時方屯難','故屯邅也。時方屯難',136,'也误作屯，导致错误断句'),
 (4,1):('刑不可長。','刑不可長也。',146,'句末脱也字'),
 (4,2):('故「包蒙吉」也','故曰「包蒙吉」也',147,'故后脱曰字'),
 (4,6):('童蒙原發','童蒙願發',150,'愿望之愿误为原'),
 (15,6):('可以邑一國','可以征邑國',249,'征邑误作邑一'),
 (29,3):('「枕」者，枕枝而不安之謂也','「枕」者，枝而不安之謂也',361,'衍枕字'),
 (32,3):('上不至尊，下不至卑','上不全尊，下不全卑',389,'两处全误为至'),
}
# Exact glyph variants are retained as variants, not transformed in the corpus.
variant_groups=['潜濳','甯宁','睹覩','阴隂','美羙','隐隠','慎愼','获𫉬','弘𢎞','涉渉','远逺','寇宼','往徃','舍舎','微㣲','苟茍','两両','于扵','峭陗']
vmap={c:g[0] for g in variant_groups for c in g}
def variantnorm(t):return ''.join(vmap.get(c,c) for c in norm(t))
records=[];corrections=[]
for old in pre:
 e=byid[old['id']];key=(e['hexagramId'],e['line']);fn=old['file'];raw=(p/fn).read_text();ref=old['ref'];fname='eee-'+str(e['hexagramId'])+'.txt'
 paragraphs=[re.sub(r'^注云[:：]','',s.strip()) for s in (p/fname).read_text().splitlines() if s.strip()]
 n=norm(e['text']);ep=max(paragraphs,key=lambda s:difflib.SequenceMatcher(None,n,norm(s),autojunk=False).ratio())
 score=difflib.SequenceMatcher(None,n,norm(ep),autojunk=False).ratio()
 status='matched_normalized' if not old['diff'] else 'variant_not_error' if variantnorm(e['text'])==variantnorm(ref) else 'needs_philological_review'
 notes=[]
 if key in [(1,4),(2,6),(3,4),(3,5),(4,2),(15,6),(16,5),(16,6)]:notes.append('存在版本用字或己/已/巳混写；未据机器字形差异统一改字。')
 if key==(15,3):
  status='attested_edition_variant';notes.append('接/綏：四库《卷四考证》明确“綏本或作接”，属于已有校勘记载的版本异文，保留原文。')
 if key in fixes:
  before,after,scan,why=fixes[key];assert e['text'].count(before)==1
  status='confirmed_transcription_issue';notes.append(why+'；Kanripo与易学网古籍影印对应位置一致。仅建议局部修正，其他异文不动。')
  corrections.append({'evidenceId':e['id'],'before':before,'after':after,'reason':why,'reviewMethod':'machine_assisted_text_comparison_and_visual_scan_reading','reviewDate':'2026-09-26','sourceUrls':[f'https://github.com/kanripo/KR1a0007/blob/{commit}/{fn}',f'https://www.eee-learning.com/book/eee-jy{e["hexagramId"]}',f'https://www.eee-learning.com/eeebook/zenyi/{scan}.gif'],'scanFile':f'scan-{scan}.gif','originalText':e['text'],'correctedText':e['text'].replace(before,after),'humanReviewed':False,'applied':False})
 sources=[{'role':'current_witness','url':e['source'],'sourceId':e['sourceId'],'rawLocator':e['rawLocator']},{'role':'independent_transcription','name':'Kanripo 周易註疏 文淵閣四庫全書本','url':f'https://github.com/kanripo/KR1a0007/blob/{commit}/{fn}','commit':commit,'localFile':fn,'rawLocator':old['sourceLocator'],'sha256':hashlib.sha256((p/fn).read_bytes()).hexdigest(),'note':'独立托管且注明WYG底本；不以GitHub/Raw两个URL计算为两个信源。'}, {'role':'additional_text_channel','name':'易学网 周易正义','url':f'https://www.eee-learning.com/book/eee-jy{e["hexagramId"]}','localFile':fname,'note':'网站给出校对状态及影印链接；录文与维基转录的历史依赖未完全证明，故不单凭多个网页多数投票判正误。'}]
 if key in fixes:sources.append({'role':'visual_witness','url':f'https://www.eee-learning.com/eeebook/zenyi/{fixes[key][2]}.gif','localFile':f'scan-{fixes[key][2]}.gif','visualRead':True,'note':'代理直接检查影印字形；影印宿主与易学网录文不算独立站点，影印刊本书目信息待进一步确认。'})
 if key==(15,3):sources.append({'role':'variant_apparatus','url':'https://www.shidianguji.com/book/SK0006/chapter/1l9rdovxh6n7z','excerpt':'九三注：上承下綏。○“綏”本或作“接”。'})
 rec={'evidenceId':e['id'],'hexagramId':e['hexagramId'],'line':e['line'],'status':status,'review_status':'machine_assisted','humanReviewed':False,'reviewDate':'2026-09-26','localText':e['text'],'comparisonText':ref,'comparisonLocator':old['sourceLocator'],'comparisonSimilarity':old['ratio'],'differencesAfterBasicNormalization':old['diff'],'additionalChannelText':ep,'additionalChannelSimilarity':score,'compared_sources':sources,'attribution':{'author':'王弼','work':'周易正義/周易註疏','layer':'注','result':'consistent_with_chapter_header_and_comment_boundary','note':'只核对经文爻辞后的注，不将孔颖达疏、陆德明音义或韩康伯传注混算。'},'notes':notes,'changeApplied':False}
 records.append(rec)
(p/'records.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in records))
(p/'corrections.json').write_text(json.dumps(corrections,ensure_ascii=False,indent=2)+'\n')
counts=dict(collections.Counter(x['status'] for x in records));print(counts)
print('EEE low similarities',[(x['hexagramId'],x['line'],round(x['additionalChannelSimilarity'],3)) for x in records if x['additionalChannelSimilarity']<.8])
(p/'summary.json').write_text(json.dumps({'total':len(records),'statusCounts':counts,'confirmedCorrectionCount':len(corrections),'alignmentMethod':'fixed_volume+hexagram_glyph+line_label+classical_prefix+direct_comment_boundary','scope':'47 existing Wang Bi line comments; all text-aligned against Kanripo; additional EEE page comparison; 7 substantive suspected errors visually checked on scans','humanReviewed':False,'currentCorpusUnchanged':True,'kanripoCommit':commit},ensure_ascii=False,indent=2)+'\n')
manifest=[]
for f in sorted(p.iterdir()):
 if f.suffix in ['.txt','.html','.gif','.png','.org']:
  url=f'https://www.eee-learning.com/eeebook/zenyi/{f.stem.split("-")[-1]}.gif' if f.name.startswith('scan-') else f'https://www.eee-learning.com/book/eee-jy{f.stem.split("-")[-1]}' if f.name.startswith('eee-') else f'https://raw.githubusercontent.com/kanripo/KR1a0007/{commit}/{f.name}' if f.name.startswith('KR') or f.name=='Readme.org' else None
  manifest.append({'file':f.name,'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bytes':f.stat().st_size,'url':url,'retrievedAt':'2026-09-26','use':'local audit reference only; not imported into runtime corpus'})
(p/'snapshots.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
