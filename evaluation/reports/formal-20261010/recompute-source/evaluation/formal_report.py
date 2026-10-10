"""Recompute the final evidence tables. No API calls or production changes."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from evaluation.formal_analysis import evidence_analysis
from evaluation.formal_stats import BASE, DIMENSIONS, cluster_bootstrap, recompute, review_material, wilson, write_csv
from evaluation.paired import write_json


def read(path):return json.loads(path.read_text())


def analyze_reviews(base):
    specs=read(base/'review-manifest.json')['reviews']
    roles={str((base/s['scores']).resolve()):s['role'] for s in specs}
    all_reviews=read(base/'analysis/reviews.json')['results']
    cells={(c['stage'],c['caseId'],c['arm']):c for c in read(base/'analysis/cells.json')}
    data_path=base/'source/evaluation/formal/data.json'
    if not data_path.exists():data_path=Path(__file__).parent/'formal/data.json'
    raw_data={c['caseId']:c for c in read(data_path)['cases']}
    actual_evidence={}
    for stage in ('development','test','rag','continuous'):
        if (base/stage/'rows.json').exists():
            for row in read(base/stage/'rows.json'):
                actual_evidence[(stage,row['caseId'],row['arm'])]={e['id'] for e in row.get('finalGenerationEvidence',row.get('evidence',[]))}
    groups=defaultdict(list);indexed={}
    for r in all_reviews:
        if r['target']!='delivered':continue
        role=roles[str(Path(r['source']).resolve())]
        key=(role,r['stage'],r['caseId'],r['arm'])
        if key in indexed:raise ValueError('DUPLICATE_PANEL_SCORE:'+str(key))
        indexed[key]=r
        category=raw_data.get(r['caseId'],{}).get('category','continuous')
        split=cells.get((r['stage'],r['caseId'],r['arm']),{}).get('split',raw_data.get(r['caseId'],{}).get('split','unknown'))
        groups[(role,r['stage'],split,r['arm'],category)].append(r)
    summaries=[]
    for (role,stage,split,arm,category),rs in sorted(groups.items()):
        scores={d:[r['scores'].get(d) for r in rs if r['scores'].get(d) is not None] for d in DIMENSIONS}
        errors=Counter();errors_by_certainty=defaultdict(Counter);claims=defaultdict(Counter);claims_by_scope=defaultdict(Counter);coverage=Counter()
        for r in rs:
            e=next(e for e in r['original']['evaluations'] if e['target']=='delivered')
            errors.update({err['type'] for err in e.get('errors',[])})
            for certainty in {err.get('certainty','unspecified') for err in e.get('errors',[])}:
                errors_by_certainty[certainty].update({err['type'] for err in e['errors'] if err.get('certainty','unspecified')==certainty})
            f=e.get('faithfulness') or {}
            for k,v in f.get('coverage',{}).items():
                if type(v) is int:coverage[k]+=v
            for c in f.get('claims',[]):
                if c.get('supportLabel') in ('supported','partial','unsupported','unknown'):
                    claims[c.get('layer','unknown')][c['supportLabel']]+=1
                    visible=actual_evidence.get((r['stage'],r['caseId'],r['arm']),set())
                    ids=set(c.get('sourceIds',[]))
                    inferred='reference_only' if ids and not ids&visible else 'mixed_or_reference' if ids-visible else 'input_evidence'
                    scope=c.get('supportEvidenceScope') or inferred
                    if scope in ('actually_available','actual_evidence'):scope='input_evidence'
                    claims_by_scope[str(scope)+'/'+c.get('layer','unknown')][c['supportLabel']]+=1
        summaries.append(dict(role=role,stage=stage,split=split,arm=arm,category=category,n=len(rs),
            reviewers=sorted({json.dumps(r['reviewer'],sort_keys=True,ensure_ascii=False) for r in rs}),
            delivered=sum(r['mechanicalComplete'] for r in rs),
            taskSuccess=wilson(sum(r['mechanicalComplete'] and r['original'].get('taskSuccess') is True for r in rs),len(rs)),
            taskSuccessKnown=wilson(sum(r['mechanicalComplete'] and r['original'].get('taskSuccess') is True for r in rs),sum(r['original'].get('taskSuccess')!='unknown' for r in rs)),
            unknownTask=sum(r['original'].get('taskSuccess')=='unknown' for r in rs),
            dimensions={d:dict(n=len(v),unknown=len(rs)-len(v),mean=mean(v) if v else None,distribution=dict(Counter(v))) for d,v in scores.items()},
            errorAnswerCounts=dict(errors),errorAnswerCountsByCertainty={k:dict(v) for k,v in errors_by_certainty.items()},
            claimLabelsByScopeAndLayer={k:dict(v) for k,v in claims_by_scope.items()},claimLabelsByLayer={k:dict(v) for k,v in claims.items()},claimCoverage=dict(coverage),
            faithfulnessApplicable=arm!='memory',note='One primary AI score per output; secondary never averaged into primary. No expert or human scores. Memory knowledge can use reference-only sources, input-evidence faithfulness is N/A.'))
    pairs=[]
    for stage in ('test','development','rag'):
        subset={k:r for k,r in indexed.items() if k[0]=='primary' and k[1]==stage}
        arms=sorted({k[3] for k in subset})
        import itertools
        for left,right in itertools.combinations(arms,2):
            shared=sorted({k[2] for k in subset if k[3]==left}&{k[2] for k in subset if k[3]==right})
            for category,split in sorted({(raw_data[c]['category'],raw_data[c].get('split','unknown')) for c in shared}):
                ids=[c for c in shared if raw_data[c]['category']==category and raw_data[c].get('split','unknown')==split]
                for d in (*DIMENSIONS,'taskSuccess'):
                    deltas=[]
                    for cid in ids:
                        a,b=[subset[('primary',stage,cid,arm)] for arm in (left,right)]
                        if d=='taskSuccess':
                            av,bv=[(0 if not r['mechanicalComplete'] else int(r['original'][d]) if type(r['original'].get(d)) is bool else None) for r in (a,b)]
                        else:av,bv=a['scores'].get(d),b['scores'].get(d)
                        if av is not None and bv is not None:deltas.append((raw_data[cid]['groupId'],bv-av))
                    pairs.append(dict(stage=stage,split=split,category=category,left=left,right=right,dimension=d,
                        rightWins=sum(v>0 for _,v in deltas),leftWins=sum(v<0 for _,v in deltas),ties=sum(v==0 for _,v in deltas),
                        clusterCI=cluster_bootstrap(deltas),pairedPlanned=len(ids),unknownOrNAScores=len(ids)-len(deltas)))
    disagreements=[]
    for (role,stage,cid,arm),b in indexed.items():
        if role!='secondary':continue
        a=indexed.get(('primary',stage,cid,arm))
        if not a:continue
        disagreements.append(dict(stage=stage,caseId=cid,arm=arm,
            primary=a['source'],secondary=b['source'],
            dimensions={d:dict(primary=a['scores'].get(d),secondary=b['scores'].get(d),
                difference=b['scores'][d]-a['scores'][d] if a['scores'].get(d) is not None and b['scores'].get(d) is not None else None) for d in DIMENSIONS},
            primaryTask=a['original'].get('taskSuccess'),secondaryTask=b['original'].get('taskSuccess')))
    agreement={}
    for d in DIMENSIONS:
        deltas=[r['dimensions'][d]['difference'] for r in disagreements if r['dimensions'][d]['difference'] is not None]
        agreement[d]=dict(n=len(deltas),exact=sum(v==0 for v in deltas),meanAbsoluteDifference=mean(abs(v) for v in deltas) if deltas else None)
    result=dict(groups=summaries,pairs=pairs,disagreements=disagreements,agreement=agreement,
        primaryRecords=sum(k[0]=='primary' for k in indexed),secondaryRecords=len(disagreements),humanRecords=0)
    write_json(base/'analysis/quality.json',result);write_csv(base/'analysis/quality.csv',summaries);write_csv(base/'analysis/quality-pairs.csv',pairs)
    return result


def retrieval_diagnostics(base):
    path=base/'retrieval-judged/rows.json'
    if not path.exists():return
    rows=read(path);out=[]
    for split in ('development','independent_test'):
        for arm in ('R1','R2','R3'):
            scores=[next(s for s in r['scores'] if s['k']==5) for r in rows if r['split']==split and r['arm']==arm]
            scores=[s for s in scores if s['eligible']]
            n=sum(s['returned'] for s in scores);k=sum(s['knownRelevant'] for s in scores);u=sum(s['unknownRelevance'] for s in scores)
            out.append(dict(split=split,arm=arm,cases=len(scores),returned=n,knownRelevant=k,unknown=u,
                precisionReturnedLower=k/n if n else None,precisionReturnedUpper=(k+u)/n if n else None,
                posthoc='Diagnostic supplement: actual-returned purity, not replacement for preregistered Precision@5 (denominator 5 per query).'))
    write_json(base/'analysis/retrieval-returned.json',out)


def export_examples(base):
    """Posthoc illustrative selection, not a scoring subset or anonymous pack."""
    selected={'development':{'dev-31'},'test':{'test-01','test-07','test-11','test-29','test-33','test-34'},
              'rag':{'test-24'},'continuous':None}
    lines=['# 代表性真实回答与连续对话全文','','这是事后用于解释机制的选例，已揭示策略，不用于替代全量结果或匿名评分。包含成功、技术失败、可读但有语义争议的答案。完整候选只供诊断，不冒充交付。','']
    for stage,ids in selected.items():
        if not (base/stage/'rows.json').exists():continue
        inputs=read(base/stage/'inputs.json');inputs=inputs['cases'] if isinstance(inputs,dict) else inputs
        inputs={c['caseId']:c for c in inputs}
        for row in read(base/stage/'rows.json'):
            if ids is not None and row['caseId'] not in ids:continue
            if stage=='test' and row['arm'] not in ('H','D') :continue
            m=review_material(row,inputs[row['caseId']],f"{stage}/{row['caseId']}/{row['arm']}")
            lines+=['## '+m['answerId'],'','**核心问题：** '+m['coreQuestion'],'','**当前问题：** '+m['question'],'',
                    '**状态：** '+str(row['status']),'','### 完整正文','']
            if m['answer']:
                lines += [p['text']+'\n' for p in m['answer']['paragraphs']]
            else:lines+=['未完成可交付回答。','']
            for label,key in [('完整历史','history'),('固定程序卦象','cast'),('回答及引用','answer'),('未交付候选','candidate'),('实际可见原文','evidence')]:
                if m[key] is not None:lines+=['<details><summary>'+label+'</summary>','','```json',json.dumps(m[key],ensure_ascii=False,indent=2),'```','','</details>','']
    (base/'representative-answers.md').write_text('\n'.join(lines).rstrip()+'\n')


def rag_diagnostics(base):
    if not (base/'rag/rows.json').exists():return
    rows=read(base/'rag/rows.json')
    write_json(base/'analysis/rag-evidence-profile.json',[
        dict(caseId=r['caseId'],split=r['split'],arm=r['arm'],status=r['status'],
             evidenceIds=[e['id'] for e in r.get('evidence',[])],evidenceCount=len(r.get('evidence',[]))) for r in rows])
    if not (base/'analysis/reviews.json').exists():return
    reviews=[r for r in read(base/'analysis/reviews.json')['results'] if r['stage']=='rag']
    bykey={(r['caseId'],r['arm'],r['target']):r for r in reviews}
    output=[]
    for row in rows:
        target='delivered' if row['status']=='completed' else 'candidate'
        r=bykey.get((row['caseId'],row['arm'],target))
        output.append(dict(caseId=row['caseId'],split=row['split'],arm=row['arm'],status=row['status'],
            reviewedTarget=target,reviewAvailable=r is not None,scores=r['scores'] if r else None,
            scope='Posthoc diagnostic of the one visible generation, including rejected candidates. Not user-delivered quality or task success.'))
    write_json(base/'analysis/rag-generation-diagnostic.json',output)


def main(base):
    specs=read(base/'review-manifest.json')['reviews'] if (base/'review-manifest.json').exists() else []
    metrics=recompute(base,base/'price_table.json',review_paths=specs)
    evidence_analysis(base)
    retrieval_diagnostics(base)
    export_examples(base)
    rag_diagnostics(base)
    quality=analyze_reviews(base) if specs else None
    cells=read(base/'analysis/cells.json')
    model_errors=[]
    for call in read(base/'analysis/usage.json')['calls']:
        if call['status']!='error':continue
        events=[json.loads(line) for line in (base/call['trace']).read_text().splitlines()]
        event=next(e for e in events if e.get('kind')=='model_result' and e.get('callId')==call['callId'])
        cell=next(c for c in cells if c['trace']==call['trace'])
        model_errors.append(dict(trace=call['trace'],callId=call['callId'],error=call['error'],
            finishReason=(event.get('response') or {}).get('finishReason'),usage=call['usage'],
            estimatedUSD=call['estimate']['usd'],eventuallyMechanicalComplete=cell['mechanicalComplete']))
    write_json(base/'analysis/model-errors.json',model_errors)
    failures=[{k:c[k] for k in ('stage','split','caseId','arm','status','error','errors','calls','reportedTokens','estimatedUSD','durationMs','trace')} for c in cells if not c['mechanicalComplete']]
    error_groups=defaultdict(Counter)
    for c in failures:
        codes={str(e).split(':',1)[0] for e in c['errors']}
        if not codes:codes={c['status']}
        error_groups[(c['stage'],c['arm'])].update(codes)
    write_json(base/'analysis/failures.json',dict(cases=failures,counts=[dict(stage=k[0],arm=k[1],errors=dict(v)) for k,v in error_groups.items()],note='One output may have several error codes; code counts must not be summed as independent failures.'))
    write_csv(base/'analysis/failures.csv',failures)
    lines=['# 可重算指标表','','机械完成不是语义成功；AI 评分不是真人验收。失败保留在运行分母，五维无答案为 N/A。','',
           '| 阶段 / 策略 | 完成 / N | 调用 | Tokens | P50 / P95 秒 | Repair 成功 / 触发 | 估算 USD |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for g in metrics['groups']:
        rate=g['mechanicalComplete'];repair=g['repair']
        delay=' / '.join(f'{g[k]/1000:.2f}' if g[k] is not None else 'unknown' for k in ('p50Ms','p95Ms'))
        cost=f"{g['estimatedUSD']:.6f}" if g['estimatedUSD'] is not None else 'unknown'
        lines.append(f"| {g['stage']} / {g['split']} / {g['arm']} | {rate['successes']}/{rate['n']} | {g['calls']} | {g['reportedTokens']} | {delay} | {repair['successful']}/{repair['triggered']} | {cost} |")
    if quality:
        lines+=['','## 独立 AI 首评','','顺序：理解 / 知识 / 关联 / 交流 / 帮助；每项 0–2，仅对有分值的回答取均值；N/A 数见 quality.json。','',
                '| 阶段 / 策略 / 场景 | N | 确认完成 / N | 五维均分 |','|---|---:|---:|---|']
        for g in quality['groups']:
            if g['role']!='primary':continue
            means=' / '.join(f"{g['dimensions'][d]['mean']:.2f}" if g['dimensions'][d]['mean'] is not None else 'N/A' for d in DIMENSIONS)
            lines.append(f"| {g['stage']} / {g['split']} / {g['arm']} / {g['category']} | {g['n']} | {g['taskSuccess']['successes']}/{g['n']} | {means} |")
    (base/'analysis/tables.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(calls=metrics['accounting']['calls'],tokens=metrics['accounting']['reportedTokens'],
        usd=metrics['cost']['estimatedUSD'],ledgerConsistent=metrics['ledger']['consistent'],integrityIssues=metrics['integrityIssues']),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,default=BASE);args=p.parse_args();main(args.base)
