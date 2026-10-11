"""Random API conversations and isolated context replay; no cast selection.

python -m evaluation.context_experiment baseline|probe|confirmation
All inputs are synthetic. A stage refuses overwrite. No runtime user data.
"""
import argparse
import asyncio
import copy
import json
import subprocess
import tempfile
import time
import types
from pathlib import Path
from uuid import uuid4

import httpx
from backend import graph
from backend.config import ROOT
from backend.domain import now, calculate_hexagram
from backend.evaluate import BudgetedChat
from backend.knowledge import CURRENT_RELEASE
from backend.llm import ChatConfig, configured_chat, ModelError
from evaluation.paired import Recorder, RecordedChat, sha, write_json

BASE = ROOT / 'evaluation/reports/context-20261011'
MAIN = ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='low', max_tokens=8192)
REPAIR = ChatConfig(model='deepseek-flash', thinking='disabled', max_tokens=8192)
MAX_CALLS, MAX_TOKENS = 120, 1_200_000
CONVERSATION_TASK = '你是一位熟悉《易经》、善于倾听的智者，与用户一起把所问之事想清楚。依据新的事实和已经作出的选择，推进眼下仍需解决的问题。以本次卦象和相关文献为理解处境的视角，结合现实条件讨论判断、取舍与行动。'
DEVELOPMENT = [
    dict(caseId='pottery', questions=[
        '陶艺课每次三小时，我每周只有一小时空闲，但一直想试试，要不要报名？',
        '我把课程表看错了，每两周上一回，我可以把两周的空闲集中成一个下午。费用也在预算内。',
        '我还没试过这种长时间坐着做东西的课，能不能先上一回体验课再决定？']),
    dict(caseId='exhibition', questions=[
        '我和同学共同办展，作品怎么摆一直谈不拢，我有点想取消。',
        '不是场地没确定，也不是我们关系不好，只是他想按创作时间排，我想按颜色排。',
        '我们都愿意试一个办法，那能不能先在纸上各摆一次，再找两个没参与准备的朋友看看？']),
]
KNOWLEDGE = [
    dict(caseId='mixed-source',questions=['“见金夫，不有躬”出自哪里？我想先看原文，再看朱熹如何解释“金夫”。']),
    dict(caseId='mixed-exact',questions=['请给乾卦九二的爻辞原文和王弼的注解，分别说明原文和注解。']),
]


def read(path):
    return json.loads(path.read_text())


def baseline_module():
    """Snapshot graph and validator, not a second production implementation."""
    directory = BASE / 'baseline-source'
    validation = types.ModuleType('backend.context_baseline_validation')
    exec(compile((directory/'validation.py').read_text(), str(directory/'validation.py'), 'exec'), validation.__dict__)
    module = types.ModuleType('backend.context_baseline_graph')
    exec(compile((directory/'graph.py').read_text(), str(directory/'graph.py'), 'exec'), module.__dict__)
    for name in ('validate_answer', 'requested_targets', 'matches_target', 'fallback'):
        setattr(module, name, getattr(validation, name))
    return module


def rearrange(messages, variant):
    """Same history/evidence words; vary only role/placement, no style instruction."""
    messages = copy.deepcopy(messages)
    context = json.loads(messages[1]['content'])
    history = context.pop('近期对话', [])
    turns = [dict(role=role, content=t[field]) for t in history
             for role, field in [('user', 'user'), ('assistant', 'assistant')] if t[field]]
    if variant == 'roles':
        return [messages[0], *turns, dict(role='user', content=graph.dumps(context))]
    if variant == 'background':
        current = context.pop('当前问题')
        return [messages[0], dict(role='user', content=graph.dumps(context)), *turns,
                dict(role='user', content=current)]
    return messages


def shared_budget():
    budget = BudgetedChat(configured_chat(MAIN, repair_config=REPAIR), MAX_CALLS, MAX_TOKENS)
    if (BASE/'ledger.json').exists():
        previous = read(BASE/'ledger.json')
        for attr, key in [('calls','calls'), ('reported','reportedTokens'), ('accounted','accountedTokens'), ('unknown','missingUsageCalls')]:
            setattr(budget, attr, previous[key])
    return budget


def start(stage, scenarios):
    out = BASE/stage
    out.mkdir(parents=True, exist_ok=False)
    write_json(out/'scenarios.json', scenarios)
    files = list((ROOT/'backend').glob('*.py')) + [Path(__file__)]
    for path in files:
        dest = out/'source'/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    write_json(out/'manifest.json', dict(createdAt=now(), commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        dirty=bool(subprocess.check_output(['git','status','--porcelain'], cwd=ROOT)),
        scenarioHash=sha(scenarios), codeHashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files},
        graphVersion=baseline_module().GRAPH_VERSION if stage in ('baseline','probe','diagnostic','history-probe') else graph.GRAPH_VERSION,
        baselineGraphHash=sha((BASE/'baseline-source/graph.py').read_bytes()) if (BASE/'baseline-source/graph.py').exists() else None,
        baselineValidatorHash=sha((BASE/'baseline-source/validation.py').read_bytes()) if (BASE/'baseline-source/validation.py').exists() else None,
        main=configured_chat(MAIN).request_payload([]), repair=configured_chat(REPAIR).request_payload([]),
        releaseId=CURRENT_RELEASE, budget=dict(calls=MAX_CALLS,tokens=MAX_TOKENS),
        sampling='One real POST /api/sessions per conversation; retain recorded random coins, never replace/reroll/select casts.',
        measurement='In-process HTTP handlers + real model + isolated SQLite; not browser/network performance.',
        probe='Generation-only replay of actual baseline requests, identical cast/history/evidence; no repair. Not an end-to-end comparison.'))
    return out


async def conversations(stage, scenarios):
    from backend import app as application
    module = baseline_module() if stage == 'baseline' else graph
    application.initial_state = module.initial_state
    application.GRAPH_VERSION = module.GRAPH_VERSION
    out = start(stage, scenarios)
    rows = []
    shared = shared_budget()
    # Separate apps avoid sharing a cast, history, or rate limit across scenarios.
    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix='iching-context-') as temp:
            app = application.create_app(Path(temp))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    headers = {'authorization': 'Bearer '+str(uuid4())}
                    created = await client.post('/api/sessions',headers=headers,json=dict(requestId=str(uuid4()),question=scenario['questions'][0]))
                    created.raise_for_status()
                    session = created.json()
                    original_cast = copy.deepcopy(session['cast'])
                    assert [6+sum(c) for c in original_cast['coins']] == original_cast['lines']
                    expected = calculate_hexagram(original_cast['lines'])
                    assert all(original_cast[k] == v for k,v in expected.items())
                    write_json(out/f"{scenario['caseId']}-created.json",session)
                    for index, question in enumerate(scenario['questions'], 1):
                        ident = f"{scenario['caseId']}-{index}"
                        recorder = Recorder(out/(ident+'.jsonl'),ident,stage)
                        budget = BudgetedChat(configured_chat(MAIN,repair_config=REPAIR),8,120000,parent=shared)
                        app.state.graph = module.build_graph(app.state.saver,RecordedChat(budget,recorder),observe=recorder)
                        row = dict(recordId=ident,groupId=scenario['caseId'],turnIndex=index,question=question,
                            coreQuestion=session['question'],inputSession=copy.deepcopy(session),cast=original_cast,
                            status='running',answer=None,evidence=[],errors=[],semanticReview='pending')
                        rows.append(row);write_json(out/'rows.json',rows)
                        started=time.monotonic()
                        try:
                            response=await client.post('/api/sessions/'+session['id']+'/turns',headers=headers,
                                json=dict(requestId=str(uuid4()),question=question))
                            response.raise_for_status();session=response.json();turn=session['turns'][-1]
                            row.update(status=turn['status'],answer=turn.get('answer'),evidence=turn['evidence'],trace=turn['trace'],error=turn.get('error'),httpStatus=response.status_code)
                            assert session['cast']==original_cast
                            row['sameCast']=True
                        except Exception as exc:
                            row.update(status='failed',error=str(exc) if isinstance(exc,ModelError) else type(exc).__name__)
                        validations=[e for e in recorder.events if e['kind']=='validation']
                        if validations:
                            row.update(errors=validations[-1]['errors'],finalCandidate=validations[-1]['candidate'],
                                finalGenerationEvidence=validations[-1]['evidence'],repairs=validations[-1]['repairNumber'])
                        row.update(durationMs=round((time.monotonic()-started)*1000),budget=budget.summary())
                        write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
                        print(stage,ident,row['status'],budget.summary()['reportedTokens'],flush=True)
                    write_json(out/f"{scenario['caseId']}-final.json",session)
    return rows


async def probe(stage='probe'):
    baseline=read(BASE/'baseline/rows.json')
    selected=[r for r in baseline if r['turnIndex']>1]
    out=start(stage,[r['recordId'] for r in selected]);shared=shared_budget();rows=[]
    module=baseline_module()
    for i,source in enumerate(selected):
        events=[json.loads(l) for l in (BASE/'baseline'/(source['recordId']+'.jsonl')).read_text().splitlines()]
        request=next(e for e in events if e['kind']=='model_request' and e['stage']=='generate')
        validation=next((e for e in events if e['kind']=='validation'),None)
        prepared=next(e for e in events if e['kind']=='prepare')
        variants={'probe':['roles','background'],'diagnostic':['task','initial-evidence'],
                  'history-probe':['user-history-only','disabled']}[stage]
        for variant in variants if i%2==0 else variants[::-1]:
            row=copy.deepcopy(source);row.update(recordId=source['recordId']+'-'+variant,variant=variant,status='running',answer=None,errors=[],sourceRequestHash=request['payloadSha256'])
            for key in ('finalCandidate','finalGenerationEvidence','trace','repairs','budget','durationMs','error'):
                row.pop(key,None)
            rows.append(row);write_json(out/'rows.json',rows)
            rec=Recorder(out/(row['recordId']+'.jsonl'),row['recordId'],variant)
            config=REPAIR if variant=='disabled' else MAIN
            budget=BudgetedChat(configured_chat(config),1,120000,parent=shared)
            started=time.monotonic()
            messages=copy.deepcopy(request['payload']['messages'])
            evidence=validation['evidence'] if validation else source['evidence']
            if variant in ('roles','background'):
                messages=rearrange(messages,variant)
            elif variant=='task':
                messages[0]['content']=CONVERSATION_TASK+'\n'+messages[0]['content'].split('\n',1)[1]
            elif variant=='initial-evidence':
                first=next(r for r in baseline if r['groupId']==source['groupId'] and r['turnIndex']==1)
                evidence=first['finalGenerationEvidence']
                context=json.loads(messages[1]['content'])
                context['参考原文']=[{k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')} for e in evidence]
                messages[1]['content']=graph.dumps(context)
            elif variant=='user-history-only':
                context=json.loads(messages[1]['content'])
                context['近期对话']=[dict(user=t['user']) for t in context['近期对话']]
                messages[1]['content']=graph.dumps(context)
            row['configuration']=configured_chat(config).request_payload([])
            try:
                rec(dict(kind='model_phase',phase='generate'))
                result=await RecordedChat(budget,rec)(messages)
                candidate=json.loads(result['message']['content'])
                answer,errors=module.validate_answer(candidate,evidence,prepared['targets'],prepared['requireCitations'])
                row.update(answer=answer,finalCandidate=candidate,errors=errors,status='completed' if answer else 'validation_failed',evidence=evidence)
                rec(dict(kind='validation',candidate=candidate,answer=answer,errors=errors,evidence=evidence))
            except Exception as exc:
                row.update(status='failed',error=str(exc) if isinstance(exc,ModelError) else type(exc).__name__)
            row.update(durationMs=round((time.monotonic()-started)*1000),budget=budget.summary())
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
            print(stage,row['recordId'],row['status'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('stage',choices=['baseline','probe','diagnostic','history-probe','confirmation','knowledge']);args=parser.parse_args()
    if args.stage in ('probe','diagnostic','history-probe'):asyncio.run(probe(args.stage))
    elif args.stage=='baseline':asyncio.run(conversations('baseline',DEVELOPMENT))
    elif args.stage=='knowledge':asyncio.run(conversations('knowledge',KNOWLEDGE))
    else:asyncio.run(conversations('confirmation',read(ROOT/'evaluation/context_probe_candidates.json')))
