"""Recompute mechanical/usage summaries from saved calls; no semantic scores."""
import csv
import json
import math
import random
import statistics
from pathlib import Path

from backend.models import Answer
from backend.knowledge import normalize


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def metric(numerator, denominator):
    return dict(numerator=numerator, denominator=denominator,
                rate=numerator / denominator if denominator else None)


def distribution(values):
    values = sorted(values)
    return dict(n=len(values), p50=statistics.median(values) if values else None,
                p95=values[max(0, math.ceil(len(values) * .95) - 1)] if values else None,
                raw=values, method='median / nearest-rank p95; small development sample')


def schema_valid(content):
    try:
        Answer.model_validate(json.loads(content))
        return True
    except (ValueError, TypeError):
        return False


def score_row(row, events):
    calls = [e for e in events if e['kind'] == 'model_result' and e['budgetCounted']]
    tools = [e for e in events if e['kind'] == 'tool_result']
    generations = [e for e in calls if e['stage'] == 'generate']
    validations = [e for e in events if e['kind'] == 'validation']
    values = {k: [number((e.get('response', {}).get('usage') or {}).get(k)) for e in calls]
              for k in ('prompt_tokens','completion_tokens','total_tokens')}
    usage = {k: dict(reportedSum=sum(v for v in vs if v is not None),
                    unknownCalls=sum(v is None for v in vs),
                    total=sum(vs) if all(v is not None for v in vs) else None) for k, vs in values.items()}
    # Citation validity is narrower than schema, target or semantic validity.
    final = validations[-1] if validations else None
    citations = []
    if row['status'] == 'completed' and final and row.get('answer'):
        evidence = {e['id']: e for e in final['evidence']}
        for para in row['answer']['paragraphs']:
            for citation in para['citations']:
                e = evidence.get(citation['evidenceId'])
                quote = normalize(citation['quote'])
                citations.append(bool(e and len(quote) >= 2 and quote in normalize(e['text'])))
    first_message = generations[0].get('response', {}).get('message') if generations else None
    first_schema = schema_valid(first_message.get('content')) if isinstance(first_message, dict) else None
    return dict(caseId=row['caseId'], variant=row['variant'], scenario=row['scenario'], status=row['status'],
        completed=row['status']=='completed', businessStatus=(row.get('answer') or {}).get('status') if row['status']=='completed' else None,
        durationMs=row.get('durationMs'), modelRequests=len([e for e in events if e['kind']=='model_request']),
        modelCalls=len(calls), failedModelCalls=sum(e['status']!='ok' for e in calls),
        budgetRejectedRequests=sum(e['kind']=='model_result' and not e['budgetCounted'] for e in events),
        usage=usage, toolRequests=sum(e['kind']=='tool_request' for e in events),
        toolBudgetAttempts=sum(e['budgetCounted'] for e in tools),
        toolSucceeded=sum(e['executionStatus']=='ok' for e in tools),
        toolEmpty=sum(e['executionStatus']=='empty' for e in tools),
        toolRejected=sum(e['executionStatus']=='rejected' for e in tools),
        toolErrors=[e['returnedResult']['error'] for e in tools if e['executionStatus']=='rejected'],
        repairs=sum(e['stage']=='repair' for e in calls), firstSchemaValid=first_schema,
        finalMechanicalValid=row['status']=='completed' and bool(final) and not final['errors'],
        citationValidity=metric(sum(citations),len(citations)), sameCast=row.get('sameCast'),
        semanticReview='pending', estimatedCost=None)


def aggregate(rows):
    attempted = [r for r in rows if r['status'] not in ('not_run','running')]
    first = [r['firstSchemaValid'] for r in rows if r['firstSchemaValid'] is not None]
    usage = {}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        reported = sum(r['usage'][key]['reportedSum'] for r in rows)
        unknown = sum(r['usage'][key]['unknownCalls'] for r in rows)
        usage[key] = dict(reportedSum=reported, unknownCalls=unknown, total=None if unknown else reported)
    return dict(planned=len(rows), attempted=len(attempted),
        statuses={status:sum(r['status']==status for r in rows) for status in ('completed','failed','budget_stopped','not_run','running')},
        firstSchemaValidity=metric(sum(first),len(first)),
        finalMechanicalValidity=metric(sum(r['finalMechanicalValid'] for r in rows),len(attempted)),
        mechanicalCompletion=metric(sum(r['completed'] for r in rows),len(attempted)),
        citationValidity=metric(sum(r['citationValidity']['numerator'] for r in rows),sum(r['citationValidity']['denominator'] for r in rows)),
        totals={k:sum(r[k] for r in rows) for k in ('modelRequests','modelCalls','failedModelCalls','budgetRejectedRequests','toolRequests','toolBudgetAttempts','toolSucceeded','toolEmpty','toolRejected','repairs')},
        usage=usage, latencyMs=distribution([r['durationMs'] for r in attempted if r['durationMs'] is not None]),
        semanticTaskSuccess=None, estimatedCost=None)


def rescore(directory):
    directory = Path(directory)
    rows = json.loads((directory / 'rows.json').read_text())
    scores = []
    for row in rows:
        path = directory / row.get('traceFile','missing')
        events = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        scores.append(score_row(row, events))
    pairs = []
    for case_id in dict.fromkeys(r['caseId'] for r in rows):
        c,d = [next(r for r in rows if r['caseId']==case_id and r['variant']==v) for v in ('C','D')]
        pairs.append(dict(caseId=case_id,
            inputIdentical=c.get('inputSha256') == d.get('inputSha256') if c.get('inputSha256') and d.get('inputSha256') else None,
            initialEvidenceIdentical=c.get('initialEvidenceSha256') == d.get('initialEvidenceSha256') if c.get('initialEvidenceSha256') and d.get('initialEvidenceSha256') else None))
    result = dict(note='Mechanical checks and recorded usage only. Semantic scores pending; no gold-based retrieval metrics or price estimates.',
        rows=scores, pairs=pairs,
        overall={v:aggregate([r for r in scores if r['variant']==v]) for v in ('C','D')},
        byScenario={s:{v:aggregate([r for r in scores if r['variant']==v and r['scenario']==s]) for v in ('C','D')} for s in sorted({r['scenario'] for r in scores})})
    (directory / 'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=['caseId','scenario','variant','status','businessStatus','modelCalls','failedModelCalls','toolRequests','toolBudgetAttempts','toolSucceeded','toolEmpty','toolRejected','repairs','reportedTokens','unknownUsageCalls','durationMs','firstSchemaValid','finalMechanicalValid','semanticReview']
    with (directory / 'comparison.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
        for r in scores:
            out={k:r.get(k) for k in fields}
            out.update(reportedTokens=r['usage']['total_tokens']['reportedSum'],unknownUsageCalls=r['usage']['total_tokens']['unknownCalls'])
            writer.writerow(out)
    lines=['# C/W1 与 D/W2 开发配对结果','',result['note'],'','| 组别 | 机械完成 / 已执行 | 模型调用 | 工具成功 / 空 / 拒绝 | reported tokens | unknown calls | P50 ms | P95 ms |','|---|---:|---:|---|---:|---:|---:|---:|']
    for v, r in result['overall'].items():
        n=r['mechanicalCompletion']; t=r['totals']; u=r['usage']['total_tokens']; lat=r['latencyMs']
        lines.append(f"| {v} | {n['numerator']}/{n['denominator']} | {t['modelCalls']} | {t['toolSucceeded']}/{t['toolEmpty']}/{t['toolRejected']} | {u['reportedSum']} | {u['unknownCalls']} | {lat['p50']} | {lat['p95']} |")
    lines += ['', '逐题和分场景数据：comparison.csv / metrics.json；输入：inputs.json；逐次请求、工具、验证及节点更新：traces/。', '', '不以机械完成代表任务语义成功。成本金额未估算；未知用量保留 null/unknown。分位数是小样本描述，不代表生产 SLA。', '']
    (directory / 'report.md').write_text('\n'.join(lines))
    return result


def make_review_pack(directory):
    directory = Path(directory)
    target=directory/'review'; target.mkdir(exist_ok=True)
    rows=json.loads((directory/'rows.json').read_text())
    cases={c['caseId']:c for c in json.loads((directory/'inputs.json').read_text())['cases']}
    selected={'lunch-choice','haircut-tai','haircut-kan','project-decision','life-correction','source-two-lines','fuzzy-author'}
    items=[r for r in rows if r['caseId'] in selected and r['status']=='completed']
    random.Random(20261010).shuffle(items)
    materials, key = [], []
    for index,row in enumerate(items):
        aid=f'answer-{index+1:02d}'; case=cases[row['caseId']]
        materials.append(dict(answerId=aid,question=case['question'],coreQuestion=case['session']['question'],
            cast=case['session']['cast'],history=case['session']['turns'],answer=row['answer'],evidence=row['finalEvidence']))
        key.append(dict(answerId=aid,caseId=row['caseId'],variant=row['variant']))
    (target/'materials.json').write_text(json.dumps(materials,ensure_ascii=False,indent=2)+'\n')
    # Kept separate and clearly named; reviewer must not open until scoring ends.
    (directory/'review-key-do-not-open-before-scoring.json').write_text(json.dumps(key,ensure_ascii=False,indent=2)+'\n')
    fields=['answerId','reviewer','meaningSupport','castConnection0to2','situationResponse0to2','proportion0to2','overRefusal','inventedFacts','notes']
    if not (target/'scores.csv').exists():
        with (target/'scores.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
            writer.writerows(dict(answerId=item['answerId']) for item in materials)
    (target/'rubric.md').write_text((Path(__file__).parent/'phase1/human-rubric.md').read_text())
    if len(materials)<10:
        (target/'INCOMPLETE.txt').write_text('Fewer than 10 completed outputs. Do not claim the human pilot pack is complete.\n')
