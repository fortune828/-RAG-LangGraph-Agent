"""Blind review exports and usage arithmetic, without another model call."""
import argparse
import json
import random
from pathlib import Path
from evaluation.paired import sha, write_json
from evaluation.autonomous import BASE


def export(name):
    source=BASE/name
    rows=json.loads((source/'rows.json').read_text())
    cases={c['caseId']:c for c in json.loads((source/'inputs.json').read_text())}
    items=[];key=[]
    random.Random('autonomous-review-'+name).shuffle(rows)
    for index,row in enumerate(rows):
        ident=f'{name}-{index+1:02d}'
        c=cases[row['caseId']]
        items.append(dict(answerId=ident,coreQuestion=c['session']['question'],question=c['question'],
            cast=c['session']['cast'],history=c['session']['turns'],status=row['status'],answer=row.get('answer'),
            evidence=row.get('evidence', []), error=row.get('error')))
        key.append(dict(answerId=ident,caseId=row['caseId'],arm=row['arm'],config=row['config']))
    out=BASE/'blind'/name;out.mkdir(parents=True,exist_ok=True)
    write_json(out/'materials.json',items)
    write_json(source/'review-key.json',key)
    (out/'rubric.md').write_text('独立 AI 主观评阅，不是人工或专家验收。各项0–2：理解与承接、自然交流及适度深度、建议帮助、文献支持与推断克制。对无答案技术失败记N/A。评语用答案中的具体文字说明，不能用段落数/字数当质量指标；领域不确定时明确标注。证据多少和引用形式本身不加分。\n')
    print(out)


def export_combined(names, name):
    """Hide configuration-bearing stage names as well as arm labels."""
    batch=[]
    for stage in names:
        cases={c['caseId']:c for c in json.loads((BASE/stage/'inputs.json').read_text())}
        for row in json.loads((BASE/stage/'rows.json').read_text()):
            batch.append((stage,row,cases[row['caseId']]))
    random.Random(name).shuffle(batch)
    items=[];keys=[]
    for i,(stage,row,case) in enumerate(batch):
        ident=f'{name}-{i+1:02d}'
        items.append(dict(answerId=ident,coreQuestion=case['session']['question'],question=case['question'],
            cast=case['session']['cast'],history=case['session']['turns'],status=row['status'],
            answer=row.get('answer'),evidence=row.get('evidence',[]),error=row.get('error')))
        keys.append(dict(answerId=ident,stage=stage,caseId=row['caseId'],arm=row['arm'],config=row['config']))
    out=BASE/'blind'/name;out.mkdir(parents=True,exist_ok=False)
    write_json(out/'materials.json',items);write_json(BASE/f'{name}-review-key.json',keys)
    (out/'rubric.md').write_text((BASE/'blind/screening/rubric.md').read_text())
    print(out)


def usage_report():
    table=json.loads((BASE/'capabilities/price_table.json').read_text())
    rows=[]
    paths=sorted(BASE.glob('*/*.jsonl'))
    for path in paths:
        events=[json.loads(line) for line in path.read_text().splitlines()]
        requests={e['callId']:e for e in events if e['kind']=='model_request'}
        for e in events:
            if e['kind']!='model_result' or not e.get('budgetCounted'):continue
            payload=requests[e['callId']]['payload'];model=payload['model']
            u=e.get('response',{}).get('usage') or {}
            hit,miss,output=(u.get(k) for k in ('prompt_cache_hit_tokens','prompt_cache_miss_tokens','completion_tokens'))
            rates=table['models'][model]['off_peak']
            cost=None if any(v is None for v in (hit,miss,output)) else (hit*rates['input_cache_hit']+miss*rates['input_cache_miss']+output*rates['output'])/1000000
            rows.append(dict(file=str(path.relative_to(BASE)),callId=e['callId'],stage=e['stage'],model=model,
                thinking=payload['thinking'],status=e['status'],durationMs=e['durationMs'],usage=u,costEstimateUSD=cost))
    write_json(BASE/'usage.json',dict(priceSource=table['source'],priceDate=table['observedDate'],currency='USD',rate='off_peak: Saturday 2026-10-10',
        note='List price estimate using reported usage incl failures; not invoice/account debit. Missing components remain null; reasoning is included in completion.',
        calls=len(rows),knownTotalTokens=sum(r['usage'].get('total_tokens',0) for r in rows),unknownUsageCalls=sum(r['usage'].get('total_tokens') is None for r in rows),
        knownCostEstimateUSD=sum(r['costEstimateUSD'] or 0 for r in rows),unknownCostCalls=sum(r['costEstimateUSD'] is None for r in rows),rows=rows))
    print('calls',len(rows),'USD',sum(r['costEstimateUSD'] or 0 for r in rows))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--export');p.add_argument('--usage',action='store_true');a=p.parse_args()
    if a.export:export(a.export)
    if a.usage:usage_report()
