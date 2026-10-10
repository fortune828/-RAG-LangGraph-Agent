"""One paired repair replay using the failed request's visible input.

The archived thinking request includes only a reasoning hash; non-thinking repair
uses the same visible messages/errors without omitted internal reasoning.
"""
import asyncio
import json
import time

from backend.evaluate import BudgetedChat
from backend.knowledge import load_release
from backend.llm import ChatConfig, configured_chat
from backend.validation import validate_answer
from evaluation.autonomous import BASE, answers, snapshot, summarize
from evaluation.paired import RecordedChat, Recorder, write_json, sha


async def main():
    source=BASE/'release-verification';out=BASE/'repair-check';out.mkdir(parents=True,exist_ok=False)
    case=next(c for c in json.loads((source/'inputs.json').read_text()) if c['caseId']=='dev-exact-gap')
    events=[json.loads(line) for line in (source/'dev-exact-gap-released-high.jsonl').read_text().splitlines()]
    request=next(e for e in events if e['kind']=='model_request' and e['stage']=='repair')
    messages=request['payload']['messages']
    for m in messages:m.pop('reasoningOmitted',None)
    context=json.loads(messages[1]['content']);corpus={e['id']:e for e in load_release()[0]}
    evidence=[corpus[e['id']] for e in context['参考原文']]
    prepared=next(e for e in events if e['kind']=='prepare')
    snapshot(out,[case],'repair-check',['repair-disabled'])
    config=ChatConfig(model='deepseek-flash',thinking='disabled',max_tokens=8192)
    budget=BudgetedChat(configured_chat(config),max_calls=96,max_tokens=900000)
    ledger=json.loads((BASE/'ledger.json').read_text())
    budget.calls,budget.reported,budget.accounted,budget.unknown=(ledger[k] for k in ('calls','reportedTokens','accountedTokens','missingUsageCalls'))
    row=dict(caseId=case['caseId'],arm='repair-disabled',config='flash-high-generation-disabled-repair',stage='repair-check',evidence=evidence,
        visibleInputHash=sha(messages),originalErrors=json.loads(messages[-1]['content'])['errors'])
    start=time.monotonic()
    try:
        result=await RecordedChat(budget,Recorder(out/'dev-exact-gap-repair-disabled.jsonl',case['caseId'],'repair-disabled'))(messages)
        candidate=json.loads(result['message']['content']);answer,errors=validate_answer(candidate,evidence,prepared['targets'],prepared['requireCitations'])
        row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors)
    except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
    row['durationMs']=round((time.monotonic()-start)*1000)
    write_json(out/'rows.json',[row]);write_json(BASE/'ledger.json',budget.summary());answers(out,[row],[case])
    (out/'source/evaluation/autonomous_repair.py').write_text(__import__('pathlib').Path(__file__).read_text())
    manifest=json.loads((out/'manifest.json').read_text());manifest.update(actualConfig=configured_chat(config).request_payload([]),globalCallCap=96,note=__doc__);write_json(out/'manifest.json',manifest)
    summarize(out)

if __name__=='__main__':asyncio.run(main())
