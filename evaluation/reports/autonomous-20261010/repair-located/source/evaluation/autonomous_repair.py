"""One paired repair replay using the failed request's visible input.

The archived thinking request includes only a reasoning hash; non-thinking repair
uses the same visible messages/errors without omitted internal reasoning.
"""
import asyncio
import argparse
import json
import time

from backend.evaluate import BudgetedChat
from backend.knowledge import load_release
from backend.llm import ChatConfig, configured_chat
from backend.validation import validate_answer
from evaluation.autonomous import BASE, answers, snapshot, summarize
from evaluation.paired import RecordedChat, Recorder, write_json, sha


async def main(located=False):
    stage='repair-located' if located else 'repair-check'
    source=BASE/'release-verification';out=BASE/stage;out.mkdir(parents=True,exist_ok=False)
    case=next(c for c in json.loads((source/'inputs.json').read_text()) if c['caseId']=='dev-exact-gap')
    events=[json.loads(line) for line in (source/'dev-exact-gap-released-high.jsonl').read_text().splitlines()]
    request=next(e for e in events if e['kind']=='model_request' and e['stage']=='repair')
    messages=request['payload']['messages']
    for m in messages:m.pop('reasoningOmitted',None)
    context=json.loads(messages[1]['content']);corpus={e['id']:e for e in load_release()[0]}
    evidence=[corpus[e['id']] for e in context['参考原文']]
    prepared=next(e for e in events if e['kind']=='prepare')
    original_errors=json.loads(messages[-1]['content'])['errors']
    if located:
        issues=[]
        validate_answer(json.loads(messages[-2]['content']),evidence,prepared['targets'],prepared['requireCitations'],issues=issues)
        feedback=json.loads(messages[-1]['content']);feedback['errors']=issues
        messages[-1]['content']=json.dumps(feedback,ensure_ascii=False)
    snapshot(out,[case],stage,['repair-disabled'])
    config=ChatConfig(model='deepseek-flash',thinking='disabled',max_tokens=8192)
    budget=BudgetedChat(configured_chat(config),max_calls=97 if located else 96,max_tokens=900000)
    ledger=json.loads((BASE/'ledger.json').read_text())
    budget.calls,budget.reported,budget.accounted,budget.unknown=(ledger[k] for k in ('calls','reportedTokens','accountedTokens','missingUsageCalls'))
    row=dict(caseId=case['caseId'],arm='repair-disabled',config='flash-high-generation-disabled-repair',stage=stage,evidence=evidence,
        visibleInputHash=sha(messages),originalErrors=original_errors,repairErrors=json.loads(messages[-1]['content'])['errors'])
    start=time.monotonic()
    try:
        result=await RecordedChat(budget,Recorder(out/'dev-exact-gap-repair-disabled.jsonl',case['caseId'],'repair-disabled'))(messages)
        candidate=json.loads(result['message']['content']);answer,errors=validate_answer(candidate,evidence,prepared['targets'],prepared['requireCitations'])
        row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors)
    except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
    row['durationMs']=round((time.monotonic()-start)*1000)
    write_json(out/'rows.json',[row]);write_json(BASE/'ledger.json',budget.summary());answers(out,[row],[case])
    (out/'source/evaluation/autonomous_repair.py').write_text(__import__('pathlib').Path(__file__).read_text())
    manifest=json.loads((out/'manifest.json').read_text());manifest.update(actualConfig=configured_chat(config).request_payload([]),globalCallCap=budget.max_calls,note=__doc__);write_json(out/'manifest.json',manifest)
    summarize(out)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--located',action='store_true')
    asyncio.run(main(parser.parse_args().located))
