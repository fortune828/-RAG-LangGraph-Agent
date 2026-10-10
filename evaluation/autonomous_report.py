"""Recompute tables from preserved events and reveal completed AI review labels."""
import csv
import json
import statistics
from collections import defaultdict

from evaluation.autonomous import BASE, summarize
from evaluation.autonomous_review import usage_report
from evaluation.paired import write_json


def main():
    stages=('screening','finalists','rag','agent','routing','holdout','continuous','history-diagnostic','release-verification',
            'repair-check','repair-located','repair-explained','final-verification','low-verification','confirmation')
    for stage in stages:
        if (BASE/stage/'rows.json').exists():summarize(BASE/stage)
    usage_report()
    usage=json.loads((BASE/'usage.json').read_text());costs=defaultdict(float);unknown_cost=defaultdict(int)
    for call in usage['rows']:
        stage=call['file'].split('/')[0]
        trace=call['file'].split('/')[1]
        row=next(r for r in json.loads((BASE/stage/'rows.json').read_text()) if trace==f"{r['caseId']}-{r['arm']}.jsonl")
        if call['costEstimateUSD'] is not None: costs[(stage,row['arm'])]+=call['costEstimateUSD']
        else:unknown_cost[(stage,row['arm'])]+=1
    tables=[]
    for stage in stages:
        p=BASE/stage/'summary.json'
        if p.exists():
            for arm,data in json.loads(p.read_text()).items():tables.append(dict(stage=stage,arm=arm,**data,costEstimateUSD=costs[(stage,arm)] if not unknown_cost[(stage,arm)] else None,unknownCostCalls=unknown_cost[(stage,arm)]))
    write_json(BASE/'tables.json',tables)
    with (BASE/'tables.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(tables[0]),lineterminator='\n');writer.writeheader();writer.writerows(tables)
    review_rows=[]
    for stage in stages:
        scorepath=BASE/'blind'/stage/'scores.json';keypath=BASE/stage/'review-key.json'
        if not scorepath.exists() or not keypath.exists():continue
        scores=json.loads(scorepath.read_text())
        key={r['answerId']:r for r in json.loads(keypath.read_text())}
        for row in scores['results']:
            review_rows.append(dict(stage=stage) | key[row['answerId']] | row)
    for name in ('configuration-check','confirmation-check'):
        scorepath=BASE/'blind'/name/'scores.json';keypath=BASE/f'{name}-review-key.json'
        if not scorepath.exists() or not keypath.exists():continue
        key={r['answerId']:r for r in json.loads(keypath.read_text())}
        for row in json.loads(scorepath.read_text())['results']:
            review_rows.append(key[row['answerId']] | row)
    for row in review_rows:
        source=json.loads((BASE/row['stage']/'rows.json').read_text())
        result=next(r for r in source if r['caseId']==row['caseId'] and r['arm']==row['arm'])
        row['executionStatus']=result['status']
    write_json(BASE/'review-revealed.json',dict(type='Independent AI subjective review, not human or expert. No significance/non-inferiority claim.',rows=review_rows))
    averages=[]
    for group in sorted({(r['stage'],r['arm']) for r in review_rows}):
        rs=[r for r in review_rows if (r['stage'],r['arm'])==group]
        if rs:
            scored=[r for r in rs if r.get('scores') and all(isinstance(v,(int,float)) for v in r['scores'].values())]
            completed=[r for r in scored if r['executionStatus']=='completed']
            def means(records):
                return {d:statistics.mean(r['scores'][d] for r in records) for d in records[0]['scores']} if records else None
            averages.append(dict(stage=group[0],arm=group[1],reviewed=len(rs),n=len(scored),noAnswerOrUnscored=len(rs)-len(scored),
                scores=means(scored),completedOutputCount=len(completed),completedOutputScores=means(completed),
                note='Visible fallback scores do not mean task completion. Compare paired completed outputs separately; preserve all failures in success denominator.'))
    write_json(BASE/'review-averages.json',averages)
    rows=[r | dict(runDirectory=stage) for stage in stages if (BASE/stage/'rows.json').exists() for r in json.loads((BASE/stage/'rows.json').read_text())]
    write_json(BASE/'totals.json',dict(outputCells=len(rows),completed=sum(r['status']=='completed' for r in rows),
        failedCells=[dict(stage=r['stage'],runDirectory=r['runDirectory'],caseId=r['caseId'],arm=r['arm'],status=r['status'],errors=r.get('errors'),error=r.get('error')) for r in rows if r['status']!='completed'],
        mainModelCalls=usage['calls'],reportedTokens=usage['knownTotalTokens'],unknownUsageCalls=usage['unknownUsageCalls'],
        estimatedUSD=usage['knownCostEstimateUSD'],unknownCostCalls=usage['unknownCostCalls'],aiReviewed=len(review_rows)))

if __name__=='__main__':main()
