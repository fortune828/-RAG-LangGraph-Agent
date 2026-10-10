"""Controlled semantic experiments over synthetic inputs; no production sessions.

Use existing graph preparation, evidence validation, bounded model adapter and
visible trace recorder. Candidate context transforms here are offline treatments.
"""
import argparse
import asyncio
import copy
import json
import statistics
import subprocess
import time
from pathlib import Path

from backend.config import ROOT
from backend.domain import now
from backend.evaluate import BudgetedChat
from backend.knowledge import CURRENT_RELEASE
from backend.llm import ChatConfig, configured_chat
from backend.validation import validate_answer
from evaluation.autonomous import fixture, prepare, answers
from evaluation.paired import Recorder, RecordedChat, sha, write_json

BASE = ROOT / 'evaluation/reports/semantic-20261010'
CONFIG = ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='low', max_tokens=8192)
CALL_CAP = 90
TOKEN_CAP = 700000


def budget():
    result = BudgetedChat(configured_chat(CONFIG), max_calls=CALL_CAP, max_tokens=TOKEN_CAP)
    if (BASE/'ledger.json').exists():
        previous = json.loads((BASE/'ledger.json').read_text())
        result.calls, result.reported, result.accounted, result.unknown = (
            previous[k] for k in ('calls','reportedTokens','accountedTokens','missingUsageCalls'))
    return result


def context_variant(messages, arm):
    messages = copy.deepcopy(messages)
    if arm == 'baseline':
        return messages
    context = json.loads(messages[1]['content'])
    current = context.pop('当前问题')
    history = context.pop('近期对话', [])
    context.pop('本轮阶段', None)
    if arm in ('grouped', 'core'):
        # Explicit source roles/anchors, without adding generated interpretations.
        by_id = {e['id']:e for e in __import__('backend.knowledge',fromlist=['load_release']).load_release()[0]}
        records = [by_id[e['id']] for e in context['参考原文']]
        if arm == 'core':
            original = records[0]['hexagramId'] if records else None
            records = [e for e in records if e['layer']=='classic' and e['hexagramId']==original]
        groups = {}
        for e in records:
            anchor = (e['hexagramId'],e['section'],e.get('line'))
            group = groups.setdefault(anchor, dict(卦号=e['hexagramId'],章节=e['section'],爻位=e.get('line'),原典=[],历代注解=[]))
            key='原典' if e['layer']=='classic' else '历代注解'
            group[key].append({k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')})
        context['参考原文'] = list(groups.values())
    out=[messages[0],dict(role='user',content=json.dumps({'本轮参考资料':context},ensure_ascii=False))]
    for turn in history:
        out.extend([dict(role='user',content=turn['user']),dict(role='assistant',content=turn['assistant'])])
    out.append(dict(role='user',content=current))
    return out


def effective_evidence(messages, state):
    if len(messages)==2:
        ids=[e['id'] for e in json.loads(messages[1]['content']).get('参考原文',[])]
    else:
        entries=json.loads(messages[1]['content'])['本轮参考资料'].get('参考原文',[])
        ids=[e['id'] for entry in entries for e in (entry.get('原典',[])+entry.get('历代注解',[]) if '原典' in entry else [entry])]
    return [e for e in state['evidence'] if e['id'] in ids]


def snapshot(out, cases, arms, stage):
    paths=list((ROOT/'backend').glob('*.py'))+[Path(__file__),ROOT/'evaluation/paired.py',ROOT/'evaluation/autonomous.py',ROOT/'requirements.txt']
    for p in paths:
        target=out/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
    write_json(out/'inputs.json',cases)
    write_json(out/'manifest.json',dict(stage=stage,createdAt=now(),commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        graphVersion='python-graph-v13',promptVersion='evidence-agent-python-v16',releaseId=CURRENT_RELEASE,caseHash=sha(cases),arms=arms,
        configuration=configured_chat(CONFIG).request_payload([]),callCap=CALL_CAP,tokenCap=TOKEN_CAP,
        files={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        design='Single generation per cell; no repair for context ablation; alternate arm order per case. All failures retained.',
        labels='AI proposed semantic references never enter model messages; synthetic inputs only.'))


async def run(stage):
    out=BASE/stage;out.mkdir(parents=True,exist_ok=False)
    if stage=='context':
        old=ROOT/'evaluation/reports/autonomous-20261010/fixtures/development.json'
        cases=[fixture(c) for c in json.loads(old.read_text())['cases'][:3]]
        wedding=json.loads((ROOT/'evaluation/reports/autonomous-20261010/confirmation/inputs.json').read_text())[1]
        cases.append(wedding)
        arms=['baseline','roles','grouped','core']
    else:
        raise ValueError('unsupported stage')
    snapshot(out,cases,arms,stage);rows=[]
    for i,case in enumerate(cases):
        original,state=await prepare(case)
        for arm in (arms if i%2==0 else list(reversed(arms))):
            model=budget();rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            messages=context_variant(original,arm);evidence=effective_evidence(messages,state)
            row=dict(caseId=case['caseId'],arm=arm,config='flash-low',stage=stage,evidence=evidence,
                initialEvidenceIds=[e['id'] for e in state['evidence']],visibleEvidenceIds=[e['id'] for e in evidence],inputHash=sha(messages))
            start=time.monotonic()
            try:
                response=await RecordedChat(model,rec)(messages)
                candidate=json.loads(response['message']['content'])
                answer,errors=validate_answer(candidate,evidence,state['targets'],state['require_citations'])
                row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors)
            except Exception as exc:
                row.update(status='failed',error=type(exc).__name__+':'+str(exc))
            row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
            print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)


def summarize(out):
    rows=json.loads((out/'rows.json').read_text());summary={}
    for row in rows:
        events=[json.loads(line) for line in (out/f"{row['caseId']}-{row['arm']}.jsonl").read_text().splitlines()]
        calls=[e for e in events if e['kind']=='model_result' and e.get('budgetCounted')]
        usage=[e.get('response',{}).get('usage') or {} for e in calls]
        row['auto']=dict(calls=len(calls),reportedTokens=sum(u.get('total_tokens') or 0 for u in usage),unknownUsage=sum(u.get('total_tokens') is None for u in usage))
    for arm in {r['arm'] for r in rows}:
        group=[r for r in rows if r['arm']==arm]
        summary[arm]=dict(n=len(group),completed=sum(r['status']=='completed' for r in group),medianMs=statistics.median(r['durationMs'] for r in group),
            **{k:sum(r['auto'][k] for r in group) for k in ('calls','reportedTokens','unknownUsage')})
    write_json(out/'rows.json',rows);write_json(out/'summary.json',summary);print(summary)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage');args=p.parse_args();asyncio.run(run(args.stage))
