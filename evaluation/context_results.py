"""Recompute this small experiment from saved rows and request-level traces."""
import json
import statistics
from pathlib import Path

from evaluation.context_experiment import BASE, read
from evaluation.paired import write_json


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*q
    low = int(position)
    return values[low] + (values[min(low+1,len(values)-1)]-values[low])*(position-low)


def recompute(base=BASE):
    groups, usage = [], []
    plan = read(base/'plan.json')
    price = plan['price']
    for stage in ('baseline','probe','diagnostic','history-probe','confirmation','knowledge','postfix'):
        folder=base/stage
        if not (folder/'rows.json').exists():
            continue
        rows=read(folder/'rows.json')
        for path in sorted(folder.glob('*.jsonl')):
            for line in path.read_text().splitlines():
                event=json.loads(line)
                if event['kind']!='model_result':
                    continue
                raw=event.get('response',{}).get('usage') or {}
                hit, miss, output = (raw.get(k) for k in ('prompt_cache_hit_tokens','prompt_cache_miss_tokens','completion_tokens'))
                cost=(hit*price['cacheHit']+miss*price['cacheMiss']+output*price['output'])/1e6 if all(v is not None for v in (hit,miss,output)) else None
                usage.append(dict(stage=stage,file=path.name,callId=event['callId'],status=event['status'],
                    budgetCounted=event.get('budgetCounted'),usage=raw,usdEstimate=cost,durationMs=event['durationMs'],
                    error=event.get('error'),finishReason=event.get('response',{}).get('finishReason')))
        for variant in sorted({r.get('variant',stage) for r in rows}):
            selected=[r for r in rows if r.get('variant',stage)==variant]
            durations=[r['durationMs']/1000 for r in selected if r.get('durationMs') is not None]
            calls=[u for u in usage if u['stage']==stage and any(u['file']==r['recordId']+'.jsonl' for r in selected)]
            costs=[u['usdEstimate'] for u in calls]
            casts={r['cast']['id']:r['cast'] for r in selected}
            groups.append(dict(stage=stage,variant=variant,units=len(selected),conversations=len({r['groupId'] for r in selected}),
                completed=sum(r['status']=='completed' and not r.get('errors') for r in selected),
                pending=sum(r['status'] in ('running','not_run') for r in selected),
                repairTriggered=sum((r.get('repairs') or 0)>0 for r in selected),
                repairCompleted=sum((r.get('repairs') or 0)>0 and r['status']=='completed' and not r.get('errors') for r in selected),
                failures=[dict(recordId=r['recordId'],status=r['status'],error=r.get('error'),errors=r.get('errors'))
                          for r in selected if r['status'] not in ('completed','running','not_run') or r.get('errors')],
                calls=sum(u['budgetCounted'] is True for u in calls),reportedTokens=sum(u['usage'].get('total_tokens') or 0 for u in calls),
                unknownTotalUsage=sum(u['budgetCounted'] is True and u['usage'].get('total_tokens') is None for u in calls),
                usdEstimate=sum(c for c in costs if c is not None),unknownCostCalls=sum(c is None for c in costs),
                latencyP50=percentile(durations,.5),latencyP95=percentile(durations,.95),
                randomCasts=[dict(id=c['id'],coins=c['coins'],lines=c['lines'],original=c['original']['name'],changed=c['changed']['name'],moving=c['changingIndices']) for c in casts.values()]))
    totals=dict(calls=sum(u['budgetCounted'] is True for u in usage),reportedTokens=sum(u['usage'].get('total_tokens') or 0 for u in usage),
        unknownTotalUsage=sum(u['budgetCounted'] is True and u['usage'].get('total_tokens') is None for u in usage),
        usdEstimate=sum(u['usdEstimate'] for u in usage if u['usdEstimate'] is not None),unknownCostCalls=sum(u['usdEstimate'] is None for u in usage))
    for label,field in [('input','prompt_tokens'),('output','completion_tokens')]:
        totals[label+'ReportedTokens']=sum(u['usage'].get(field) or 0 for u in usage)
        totals[label+'UnknownCalls']=sum(u['usage'].get(field) is None for u in usage)
    reasoning=[(u['usage'].get('completion_tokens_details') or {}).get('reasoning_tokens') for u in usage]
    totals['reasoningReportedTokens']=sum(v for v in reasoning if v is not None)
    totals['reasoningUnknownCalls']=sum(v is None for v in reasoning)
    write_json(base/'summary.json',dict(groups=groups,totals=totals,price=price))
    write_json(base/'usage.json',usage)
    all_rows={r['recordId']:dict(r,stage=stage) for stage in ('baseline','probe','diagnostic','history-probe','confirmation','knowledge','postfix')
              if (base/stage/'rows.json').exists() for r in read(base/stage/'rows.json')}
    scored=[]
    for name in ('blind-probe','blind-diagnostic','blind-history','human-random-v17','human-random'):
        review=base/name/'ai-review.json'
        if not review.exists():
            continue
        mapping={item['answerId']:item['recordId'] for item in read(base/(name+'-key.json'))['rows']}
        for score in read(review)['answers']:
            source=all_rows[mapping[score['answerId']]]
            scored.append(dict(score,recordId=source['recordId'],stage=source['stage'],variant=source.get('variant',source['stage']),reviewFile=str(review.relative_to(base))))
    quality=[]
    for name in sorted({r['variant'] for r in scored}):
        selected=[r for r in scored if r['variant']==name]
        means={}
        for dim in ('理解','关联','交流','帮助'):
            values=[r['scores'][dim] for r in selected if type(r.get('scores',{}).get(dim)) is int]
            means[dim]=dict(n=len(values),mean=statistics.mean(values) if values else None)
        quality.append(dict(variant=name,n=len(selected),scores=means,
            contextTags={tag:sum(tag in r.get('contextTags',[]) for r in selected) for tag in ('CTX-重复解释','CTX-未利用用户修正','CTX-重新解释已有状态')}))
    write_json(base/'ai-quality.json',dict(note='Independent-of-implementation AI, non-expert. Replay probes, v17 independent-author scenarios and v18 development smoke are separate groups. Nulls are not zero. Human scores separate.',groups=quality,answers=scored))
    text=['# 运行自动统计','','机械完成不等于语义正确；probe无修复，不能与完整图成功率直接比较。',
        'P95是小样本描述值，不是线上SLA。费用按价目估算，未知用量另列。','',
        '| 阶段/组 | 完成/单元 | 模型调用 | reported tokens | P50/P95秒 | USD估算 |',
        '|---|---:|---:|---:|---:|---:|']
    for group in groups:
        latency=f"{group['latencyP50']:.2f}/{group['latencyP95']:.2f}" if group['latencyP50'] is not None else 'N/A'
        text.append(f"| {group['stage']}/{group['variant']} | {group['completed']}/{group['units']}（待完成{group['pending']}） | {group['calls']} | {group['reportedTokens']} | {latency} | {group['usdEstimate']:.6f} |")
    text+=['',f"累计：{totals['calls']}次请求，{totals['reportedTokens']} reported tokens，估算USD {totals['usdEstimate']:.6f}。未知总用量{totals['unknownTotalUsage']}次，无法估价{totals['unknownCostCalls']}次。",'',
        '汇总包含所有失败。逐调用usage与费用见usage.json；原始输入/HTTP结果/逐次请求响应与修复候选保存在各阶段。']
    (base/'tables.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(totals,ensure_ascii=False))


if __name__=='__main__':
    recompute()
