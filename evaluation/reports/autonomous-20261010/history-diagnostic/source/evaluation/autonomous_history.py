"""Diagnostic only: actual conversation third turn, with/without assistant history.

Not a production policy: removing all assistant context can break references.
Two single generations, no repair or best-of; the frozen candidate is unchanged.
"""
import asyncio
import copy
import json
import time

from backend import graph
from backend.knowledge import load_release
from backend.validation import validate_answer
from evaluation.autonomous import BASE, budget_for, snapshot, answers, summarize
from evaluation.paired import RecordedChat, Recorder, write_json


async def main():
    out=BASE/'history-diagnostic';out.mkdir(parents=True,exist_ok=False)
    source=BASE/'continuous'
    case=json.loads((source/'inputs.json').read_text())[-1]
    events=[json.loads(line) for line in (source/'conversation-3-candidate.jsonl').read_text().splitlines()]
    original=next(e['payload']['messages'] for e in events if e['kind']=='model_request' and e['stage']=='generate')
    corpus={e['id']:e for e in load_release()[0]}
    snapshot(out,[case],'history-diagnostic',['full-history','user-only'])
    rows=[]
    for arm in ('full-history','user-only'):
        messages=copy.deepcopy(original);context=json.loads(messages[1]['content'])
        if arm=='user-only':
            context['近期对话']=[{'user':turn['user']} for turn in context['近期对话']]
            messages[1]['content']=graph.dumps(context)
        evidence=[corpus[e['id']] for e in context['参考原文']]
        budget=budget_for('flash-off');rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
        start=time.monotonic();row=dict(caseId=case['caseId'],arm=arm,config='flash-off',stage='history-diagnostic',evidence=evidence)
        try:
            response=await RecordedChat(budget,rec)(messages)
            candidate=json.loads(response['message']['content']);answer,errors=validate_answer(candidate,evidence)
            row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors)
        except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
        row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
        write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',budget.summary());answers(out,rows,[case])
    (out/'source/evaluation/autonomous_history.py').write_text(__import__('pathlib').Path(__file__).read_text())
    summarize(out)

if __name__=='__main__':asyncio.run(main())
