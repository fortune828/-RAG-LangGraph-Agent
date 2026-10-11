"""Prepare a small, source-preserving human reading batch. No model calls or scores read."""
import copy
import csv
import hashlib
import html
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent/'reports/formal-20261010'
DEST=BASE/'human/first-batch-20261011'
KEY=BASE/'analysis/first-batch-20261011-key.json'
SINGLES=['test-01','test-03','test-07','test-09','test-10','test-11','test-13','test-16']
GROUPS=['test-pottery-pair','test-group-14']


def read(p):return json.loads(p.read_text())

def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def paragraphs(answer):
    if not answer:return ['未完成可交付回答。']
    return [p['text'] for p in answer.get('paragraphs',[])]


def main():
    if DEST.exists() or KEY.exists():raise FileExistsError('Refuse to overwrite review material or human scores')
    selected=[];mapping=[]
    for stage,ids in [('test',SINGLES),('continuous',[f'{g}-turn{i}' for g in GROUPS for i in range(1,4)])]:
        source=BASE/'human'/stage/'materials.json'
        materials={m['answerId']:m for m in read(source)}
        keys={(r['caseId'],r['arm']):r for r in read(BASE/'analysis'/f'{stage}-key.json')}
        for cid in ids:
            k=keys[(cid,'H')];m=copy.deepcopy(materials[k['answerId']]);old=m['answerId'];m['answerId']=f'R{len(selected)+1:02}'
            selected.append(m)
            mapping.append(dict(answerId=m['answerId'],stage=stage,caseId=cid,arm='H',sourceAnswerId=old,
                sourceMaterial=str(source.relative_to(BASE)),sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    DEST.mkdir(parents=True)
    dump(DEST/'materials.json',selected)
    dump(KEY,dict(selection='Purposeful coverage of eight life inputs and two complete three-turn test conversations; not selected by AI scores or answer quality, not a random representative sample. Production H only; hide this key from raters.',
        results=mapping,csvFields={'理解':'understanding','知识':'knowledge','关联':'integration','交流':'conversation','帮助':'helpfulness','是否回答到点上':'taskSuccess','理由或原句':'notes'},
        limitation='Simplified human trial sheet. Do not pass directly to the formal AI-score importer; retain original human entries and map fields with explicit N/A reasons on later aggregation.'))
    md=['# 首批人工阅读材料（14份）','','先读同目录 README.md，再填 scores.csv。这里不提供任何已有评分。正文保持原样；“未完成可交付回答”不是一个新的模型回答。','']
    articles=[]
    for index,m in enumerate(selected):
        group='单题场景' if index<8 else f'连续对话{1+(index-8)//3} · 第{1+(index-8)%3}轮'
        title=m['answerId']+' · '+group
        md+=['## '+title,'','**当前用户问题：** '+m['question'],'']
        h=['<article id="'+m['answerId']+'"><h2>'+title+'</h2><p><b>当前用户问题：</b>'+html.escape(m['question'])+'</p>']
        if m['coreQuestion']!=m['question']:
            md+=['**这次聊天最初的问题：** '+m['coreQuestion'],'']
            h+=['<p><b>最初问题：</b>'+html.escape(m['coreQuestion'])+'</p>']
        if m['history']:
            md+=['<details><summary>展开本轮之前的完整对话（评分前请阅读）</summary>','']
            h+=['<details><summary>本轮之前的完整对话（评分前请阅读）</summary>']
            for turn in m['history']:
                md+=['**用户：** '+(turn.get('question') or ''),'','**助手：**','']+ [t+'\n' for t in paragraphs(turn.get('answer'))]
                h+=['<p><b>用户：</b>'+html.escape(turn.get('question') or '')+'</p><b>助手：</b>']+['<p>'+html.escape(t)+'</p>' for t in paragraphs(turn.get('answer'))]
            md+=['</details>',''];h+=['</details>']
        cast=m['cast'];moving=cast.get('changingIndices',[])
        cast_text='本卦：'+cast['original']['name']+'；变卦：'+cast['changed']['name']+'；动爻位置（自下而上）：'+('、'.join(str(i+1) for i in moving) if moving else '无')+'。'
        md+=[cast_text,'','### 本轮实际回答','']+[t+'\n' for t in paragraphs(m['answer'])]
        h+=['<p class="cast">'+html.escape(cast_text)+'</p><section><h3>本轮实际回答</h3>']+['<p>'+html.escape(t).replace('\n','<br>')+'</p>' for t in paragraphs(m['answer'])]+['</section>']
        md+=['<details><summary>展开本轮引用及原文依据（核对知识时阅读）</summary>','']
        h+=['<details><summary>本轮引用及原文依据</summary>']
        for pi,p in enumerate((m['answer'] or {}).get('paragraphs',[]),1):
            for c in p.get('citations',[]):
                entry=f"第{pi}段 · {c['evidenceId']}\n模型关联到此引文的文字（支持关系待评）：{c.get('claim','未标注')}\n引文：{c['quote']}"
                md+=['```text',entry,'```',''];h+=['<pre>'+html.escape(entry)+'</pre>']
        for label,records in [('本轮生成实际可见资料',m['evidence']),('仅供查证、生成未见资料',m['referenceOnlyEvidence'])]:
            if not records:continue
            md+=['#### '+label,''];h+=['<h4>'+label+'</h4>']
            for e in records:
                name=' · '.join(str(e.get(k) or '') for k in ['id','locator','workTitle'])
                names='、'.join(e.get('authorNames') or [])
                text=e.get('textOriginal') or e['text'];link=e.get('source','')
                md+=['**'+name+' '+names+'**','','> '+text.replace('\n','\n> '),'']
                if isinstance(link,str) and link.startswith(('https://','http://')):md+=['[原文来源]('+link+')','']
                h+=['<p><b>'+html.escape(name+' '+names)+'</b></p><blockquote>'+html.escape(text).replace('\n','<br>')+'</blockquote>']
                if isinstance(link,str) and link.startswith(('https://','http://')):h+=['<a href="'+html.escape(link,quote=True)+'">原文来源</a>']
        md+=['</details>','','---',''];h+=['</details></article>'];articles.append(''.join(h))
    (DEST/'materials.md').write_text('\n'.join(md).rstrip()+'\n')
    (DEST/'materials.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>首批人工阅读材料</title><style>body{max-width:900px;margin:2rem auto;padding:0 1rem;font:17px/1.8 sans-serif}article{border-top:1px solid #ccc;margin:2rem 0}section{background:#f5f6f8;padding:1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}blockquote{border-left:3px solid #ccc;padding-left:1rem}.cast{color:#555}</style><h1>首批人工阅读材料 · 14份</h1><p>配合README.md和scores.csv使用。保持回答原文，评分留空。每轮历史及依据可展开。</p>'+''.join(articles)+'</html>\n')
    fields=['编号','理解','知识','关联','交流','帮助','是否回答到点上','理由或原句','评分人','日期']
    with (DEST/'scores.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows({'编号':m['answerId']} for m in selected)
    guide='''# 首批人工评分：从这里开始

这批共14份：R01—R08是8个生活场景；R09—R11、R12—R14是两段完整的三轮对话。按场景覆盖挑选，未按已有分数挑好答案，也保留了未完成回答。它是首批体验试评，不是随机抽样，也不用于直接估计全体用户满意度。

1. 打开 [阅读版 HTML](materials.html) 或 [Markdown](materials.md)。先看问题，追问还要展开此前对话，再读本轮回答。
2. 打开 [scores.csv](scores.csv)，找到同一编号。用Excel/WPS或编辑器填写；也可以直接把“R01：理解…，知识…，理由…”发给助手整理。
3. 五项各填 **0 / 1 / 2**；不能判断或不适用填 **NA**，在理由栏说明。完全没评的项留空。不要为了填满而猜古文。
4. “是否回答到点上”填 **是 / 否 / 不确定**。这与五项总分不同：建议有用，也可能包含错误解释。理由栏尽量写具体原句和问题，可写“理解：…；知识：…”。评分人与日期按实际填写。

| 评分项 | 主要看什么 | 2分 | 1分 | 0分 |
|---|---|---|---|---|
| 理解 | 是否听懂问题、采用最新事实与更正 | 准确承接 | 局部遗漏或脑补 | 核心理解错误 |
| 知识 | 卦义、作者观点和引用解释是否准确 | 依据充分且解释准确 | 局部误释或范围扩大 | 核心文义/归属错误 |
| 关联 | 卦义与现实联系是否合理 | 自然且有事实依据 | 有用与牵强并存 | 严重硬套或当作预测 |
| 交流 | 是否像正常交流、展开是否适度 | 连贯自然 | 机械、重复或过度展开 | 妨碍理解和交流 |
| 帮助 | 是否解决关心的矛盾、给出有用分析 | 有实质判断或下一步 | 部分有用 | 没有实质帮助 |

知识项可以展开原文再判断；引文是真的，不代表其解释就对，也不代表整段建议被经文证明。只作文化类比，不要求经文直接讨论现代事物。没有把握就填NA并说明。

连续对话逐轮评分：只依据该轮之前的信息，不用后续才出现的事实倒扣前轮。历史助手的话不自动等于用户事实。不要以字数、段数或引用数量打分。

若明确没有交付回答，五项填NA（理由：未交付），是否回答到点上填否；若实际只有通用失败提示，帮助填0，其余NA。内部未交付候选保留在JSON档案，本批不拿它替代用户体验评分。

优先记录自己的判断；试评期间先不要阅读最终报告、解盲目录或AI评分。本表是简化人工试评表，后续汇总会保留你的原始分数与理由，单列于AI评分，不自动补出你没评的结论。
'''
    (DEST/'README.md').write_text(guide)
    for m,k in zip(selected,mapping):
        original=next(x for x in read(BASE/k['sourceMaterial']) if x['answerId']==k['sourceAnswerId'])
        assert {a:v for a,v in m.items() if a!='answerId'}=={a:v for a,v in original.items() if a!='answerId'}
    with (DEST/'scores.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f));assert len(rows)==14 and all(not v for r in rows for k,v in r.items() if k!='编号')
    print('14 original records preserved; 14 blank human scoring rows; no model calls.')

if __name__=='__main__':main()
