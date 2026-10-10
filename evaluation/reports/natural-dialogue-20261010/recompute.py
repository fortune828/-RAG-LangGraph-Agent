"""Recompute recorded model usage; includes failed calls, never semantic scoring."""
import json
import statistics
from pathlib import Path

base=Path(__file__).resolve().parent
summary={}
for name in ('comparison','conversations','independent'):
    files=[base/name/'model-calls.jsonl'] if name=='independent' else sorted((base/name).glob('*.jsonl'))
    events=[json.loads(line) for p in files for line in p.read_text().splitlines()]
    calls=[e for e in events if e['kind']=='model_result']
    tokens=[(e.get('response',{}).get('usage') or {}).get('total_tokens') for e in calls]
    unknown=sum(not isinstance(v,int) or isinstance(v,bool) for v in tokens)
    reported=sum(v for v in tokens if isinstance(v,int) and not isinstance(v,bool))
    summary[name]=dict(requests=sum(e['kind']=='model_request' for e in events),
        confirmedCalls=sum(e.get('budgetCounted',False) for e in calls),
        errors=[e.get('error') for e in calls if e['status']!='ok'],
        repairs=sum(e['stage']=='repair' for e in calls),
        reportedTokens=reported,unknownUsageCalls=unknown,totalTokens=None if unknown else reported,
        callLatencyMedianMs=statistics.median(e['durationMs'] for e in calls) if calls else None)
summary['note']='Recorded calls, including failures; no prices or semantic pass rates. Comparison is isolated generation, others use the production graph. Do not pool these scenarios into a quality score.'
(base/'runtime-metrics.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False))
