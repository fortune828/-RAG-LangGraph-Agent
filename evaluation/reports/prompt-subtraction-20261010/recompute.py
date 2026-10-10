"""Recount saved calls and mechanically check proposed router calls; no LLM judge."""
import json
import statistics
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from backend.knowledge import execute_tool, CURRENT_RELEASE

base=Path(__file__).with_name('real');rows=json.loads((base/'rows.json').read_text())
summary={}
for v in ('v15','v16'):
    selected=[r for r in rows if r['version']==v]
    events=[json.loads(s) for p in base.glob(f'*-{v}.jsonl') for s in p.read_text().splitlines()]
    calls=[e for e in events if e['kind']=='model_result']
    generations=[c for c in calls if c['stage']=='generate']
    usage={}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        values=[(c.get('response',{}).get('usage') or {}).get(key) for c in generations]
        known=[n for n in values if isinstance(n,int) and not isinstance(n,bool)]
        usage[key]=dict(reportedSum=sum(known),unknownCalls=len(values)-len(known),total=sum(known) if len(values)==len(known) else None)
    summary[v]=dict(generationCalls=len(generations),completed=sum(r['status']=='completed' for r in selected),
        mechanicalValid=sum(r.get('mechanicalValid',False) for r in selected),
        latencyMedianMs=statistics.median(r['durationMs'] for r in selected),usage=usage,
        errors=[r.get('error') for r in selected if r['status']!='completed'],
        routerCalls=len(calls)-len(generations))
    events=[json.loads(s) for s in (base/f'router-{v}.jsonl').read_text().splitlines()]
    request=next(e for e in events if e['kind']=='model_request');response=next(e for e in events if e['kind']=='model_result')
    context=json.loads(request['payload']['messages'][1]['content'])
    checks=[]
    for call in response.get('response',{}).get('message',{}).get('tool_calls',[]):
        fn=call['function']
        try:
            result=execute_tool(fn['name'],json.loads(fn['arguments']),CURRENT_RELEASE,context['allowedHexagramIds'])
            checks.append(dict(tool=fn['name'],status='ok',evidenceIds=[e['id'] for e in result['evidence']]))
        except Exception as exc:checks.append(dict(tool=fn['name'],status='rejected',error=str(exc)))
    summary[v]['routerOfflineChecks']=checks
summary['sameFactHashes']=all(len({r['factHash'] for r in rows if r['caseId']==cid})==1 for cid in {r['caseId'] for r in rows})
summary['note']='Generation usage includes incomplete responses; router calls separate. Mechanical validity is not semantic truth. Six single-sample development pairs, two of them boundary checks. No repair, no discarded failures.'
(base/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False))
