"""Frozen-system evaluation. All treatments are evaluation-only; no runtime data.

The production graph is used unchanged for H. D disables only exact-target
planning in an in-memory source copy. B/C use the existing policy injection.
"""
import argparse
import asyncio
import copy
import json
import subprocess
import time
import types
import tempfile
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from backend import graph
from backend.config import ROOT
from backend.domain import now
from backend.evaluate import BudgetedChat
from backend.knowledge import CURRENT_RELEASE, load_release, lexical_index
from backend.llm import ChatConfig, ModelError, configured_chat
from backend.prompts import PROMPT_VERSION
from backend.validation import validate_answer
from evaluation.autonomous import fixture, prepare
from evaluation.paired import Recorder, RecordedChat, sha, write_json
from evaluation.workflow import rule_route

BASE = ROOT/'evaluation/reports/formal-20261010'
DATA = ROOT/'evaluation/formal'
CONFIG = ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='low', max_tokens=8192)
REPAIR = ChatConfig(model='deepseek-flash', thinking='disabled', max_tokens=8192)
MAX_CALLS, MAX_TOKENS = 800, 5_000_000
ARMS = ['B', 'C', 'D', 'H']


def read(path):
    return json.loads(path.read_text())


def ready(_state):
    return dict(role='assistant', content='{"ready":true}')


def model_routing_graph():
    source = (ROOT/'backend/graph.py').read_text()
    old = '            response = exact_target_request(state)'
    assert source.count(old) == 1
    source = source.replace(old, '            response = None  # evaluation-only exact-policy ablation')
    module = types.ModuleType('backend.formal_model_router')
    exec(compile(source, 'formal-D-source-copy', 'exec'), module.__dict__)
    return module, source


def shared_budget():
    shared = BudgetedChat(configured_chat(CONFIG, repair_config=REPAIR), max_calls=MAX_CALLS, max_tokens=MAX_TOKENS)
    if (BASE/'ledger.json').exists():
        previous = read(BASE/'ledger.json')
        for attr, key in [('calls','calls'), ('reported','reportedTokens'), ('accounted','accountedTokens'), ('unknown','missingUsageCalls')]:
            setattr(shared, attr, previous[key])
    return shared


def assert_frozen():
    frozen = read(BASE/'freeze.json')
    for key in ('productionFiles', 'dataFiles'):
        for name, digest in frozen[key].items():
            if sha((ROOT/name).read_bytes()) != digest:
                raise ValueError('FROZEN_CHANGED:'+name)
    assert (graph.GRAPH_VERSION, PROMPT_VERSION, CURRENT_RELEASE) == tuple(frozen[k] for k in ('graphVersion','promptVersion','releaseId'))
    assert configured_chat(CONFIG).request_payload([]) == frozen['mainConfiguration']


def freeze():
    BASE.mkdir(parents=True, exist_ok=True)
    if (BASE/'freeze.json').exists():
        raise ValueError('FREEZE_ALREADY_EXISTS')
    assert (BASE/'smoke/rows.json').exists(), 'SMOKE_REQUIRED'
    cases=read(DATA/'data.json')['cases']
    assert len(cases)==80 and len({c['caseId'] for c in cases})==80
    assert sum(c['split']=='development' for c in cases)==40
    assert sum(c['split']=='independent_test' for c in cases)==40
    groups={}
    for case in cases:
        groups.setdefault(case['groupId'],set()).add(case['split'])
    assert all(len(splits)==1 for splits in groups.values()), 'GROUP_SPLIT_LEAK'
    paths=list((ROOT/'backend').glob('*.py'))+[ROOT/'requirements.txt',ROOT/'data/knowledge/active.json',ROOT/'data/knowledge/raw/opencc-TSCharacters.txt']
    paths+=list((ROOT/'data/knowledge/releases'/CURRENT_RELEASE).glob('*.json'))
    datafiles=[p for p in DATA.iterdir() if p.suffix in ('.json','.md')]
    result=dict(frozenAt=now(),commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        graphVersion=graph.GRAPH_VERSION,promptVersion=PROMPT_VERSION,releaseId=CURRENT_RELEASE,
        mainConfiguration=configured_chat(CONFIG).request_payload([]),repairConfiguration=configured_chat(REPAIR).request_payload([]),
        productionFiles={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        dataFiles={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in datafiles},
        cases=80,groups=len(groups),budget=dict(calls=MAX_CALLS,tokens=MAX_TOKENS,perCellCalls=8,perCellTokens=100000),
        scope='No production tuning after evaluation. Synthetic inputs only. One sample per cell; preserve failures.',
        strategies=dict(B='fixed initial evidence; ready policy',C='unchanged rules-c-v1',D='production graph minus exact_target_request shortcut; other deterministic branches retained',H='unchanged production hybrid'),
        execution='Four arms of each fixed input run concurrently, rotating launch order. Concurrency=4, queue time excluded. Not a serial latency benchmark.',
        uncertainty='API alias/cache drift and shared-provider load remain. Independent input groups, not independent knowledge texts.')
    write_json(BASE/'freeze.json',result)
    for p in paths+datafiles+[Path(__file__),ROOT/'evaluation/paired.py',ROOT/'evaluation/workflow.py']:
        target=BASE/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
    (BASE/'source/D-graph.py').write_text(model_routing_graph()[1])
    print(json.dumps(dict(frozen=True,cases=len(cases),groups=len(groups)),ensure_ascii=False))


def start_stage(stage,cases,arms):
    if stage != 'smoke':
        assert_frozen()
    out=BASE/stage;out.mkdir(parents=True,exist_ok=False)
    write_json(out/'inputs.json',cases)
    write_json(out/'manifest.json',dict(stage=stage,createdAt=now(),graphVersion=graph.GRAPH_VERSION,
        promptVersion=PROMPT_VERSION,releaseId=CURRENT_RELEASE,arms=arms,caseHash=sha(cases),
        configuration=configured_chat(CONFIG).request_payload([]),repairConfiguration=configured_chat(REPAIR).request_payload([]),
        codeHash=sha(Path(__file__).read_bytes()),freezeSha256=sha((BASE/'freeze.json').read_bytes()) if (BASE/'freeze.json').exists() else None,
        design='One run per cell; complete graph, at most one repair. rag is one fixed generation without repair.',
        limits=dict(calls=MAX_CALLS,tokens=MAX_TOKENS,perCellCalls=8,perCellTokens=100000),
        seed='not_sent',temperature='not_sent',cache='local corpus/index prewarmed; actual provider cache usage captured'))
    (out/'runner.py').write_bytes(Path(__file__).read_bytes())
    rows=[dict(caseId=c['caseId'],groupId=c['groupId'],split=c['split'],category=c['category'],arm=arm,
               config='flash-low-8192/repair-disabled-8192',graphVersion=graph.GRAPH_VERSION,promptVersion=PROMPT_VERSION,
               status='not_run',inputHash=sha(c),answer=None,evidence=[]) for c in cases for arm in arms]
    write_json(out/'rows.json',rows)
    return out,rows


def fixed_case(raw):
    # Metadata is for statistics only; fixture() explicitly whitelists model inputs.
    return fixture(raw)|{key:raw[key] for key in ('groupId','split','category')}


async def graph_cell(case,arm,out,row,shared):
    rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
    budget=BudgetedChat(configured_chat(CONFIG,repair_config=REPAIR),max_calls=8,max_tokens=100000,parent=shared)
    module=model_routing_graph()[0] if arm=='D' else graph
    policy=ready if arm=='B' else rule_route if arm=='C' else None
    model=module.build_graph(InMemorySaver(),RecordedChat(budget,rec),route_policy=policy,observe=rec)
    start=time.monotonic();row['status']='running'
    try:
        result=await asyncio.wait_for(model.ainvoke(module.initial_state(copy.deepcopy(case['session']),case['question'],case['mode']),
            {'configurable':{'thread_id':case['caseId']+'-'+arm},'recursion_limit':30}),timeout=240)
        row.update(status='completed' if not result.get('errors') else 'validation_failed',answer=result['answer'],
            errors=result.get('errors',[]),evidence=result['evidence'],trace=result['trace'],repairs=result['repairs'],
            sameCast=result['session']['cast']==case['session']['cast'],toolAttempts=result['attempts'])
    except Exception as exc:
        row.update(status='budget_stopped' if str(exc)=='EVALUATION_BUDGET_EXHAUSTED' else 'failed',
            error=str(exc) if isinstance(exc,ModelError) else type(exc).__name__)
        rec(dict(kind='execution_error',error=row['error']))
    prepared=next((e for e in rec.events if e['kind']=='prepare'),None)
    validations=[e for e in rec.events if e['kind']=='validation']
    if prepared:
        row.update(initialEvidence=prepared['evidence'],initialEvidenceIds=[e['id'] for e in prepared['evidence']],
            scopeIds=prepared['scopeIds'],targets=prepared['targets'],requireCitations=prepared['requireCitations'])
    if validations:
        row.update(finalCandidate=validations[-1]['candidate'],finalGenerationEvidence=validations[-1]['evidence'])
    row.update(durationMs=round((time.monotonic()-start)*1000),budget=budget.summary())
    print(case['caseId'],arm,row['status'],row['budget']['calls'],flush=True)


async def run_graph(stage):
    if stage=='smoke':
        old=ROOT/'evaluation/reports/semantic-20261010'
        cases=read(old/'release/inputs.json')[:1]+read(old/'stress/inputs.json')[:1]+read(old/'final/inputs.json')[:1]
        cases=[dict(c,caseId='smoke-'+str(i+1),groupId='smoke-'+str(i+1),split='smoke_seen',category='smoke') for i,c in enumerate(cases)]
        arms=['H']
    else:
        split='independent_test' if stage=='test' else 'development'
        cases=[fixed_case(c) for c in read(DATA/'data.json')['cases'] if c['split']==split]
        arms=ARMS
    out,rows=start_stage(stage,cases,arms);shared=shared_budget()
    load_release();lexical_index(CURRENT_RELEASE)
    for i,case in enumerate(cases):
        order=arms[i%len(arms):]+arms[:i%len(arms)]
        async def execute(arm):
            row=next(r for r in rows if r['caseId']==case['caseId'] and r['arm']==arm)
            await graph_cell(case,arm,out,row,shared)
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
        await asyncio.gather(*(execute(arm) for arm in order))
    if stage!='smoke':assert_frozen()
    print(json.dumps(shared.summary()),flush=True)


async def api_smoke():
    """Exercise real HTTP handlers, persistence and model with isolated storage."""
    import httpx
    from backend.app import create_app
    out=BASE/'smoke';cases=read(out/'inputs.json');rows=read(out/'rows.json')
    assert not any(c['caseId']=='smoke-http' for c in cases)
    shared=shared_budget();budget=BudgetedChat(configured_chat(CONFIG,repair_config=REPAIR),max_calls=8,max_tokens=100000,parent=shared)
    rec=Recorder(out/'smoke-http-H.jsonl','smoke-http','H');chat=RecordedChat(budget,rec)
    question='明晚老同学约吃饭，我这周有些累，但也想见见他们，怎么安排比较好？'
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='iching-formal-http-') as temp:
        app=create_app(Path(temp),chat=chat)
        async with app.router.lifespan_context(app):
            app.state.graph=graph.build_graph(app.state.saver,chat,observe=rec)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                headers={'authorization':'Bearer '+str(uuid4())}
                created=await client.post('/api/sessions',headers=headers,json={'requestId':str(uuid4()),'question':question})
                created.raise_for_status();session=created.json()
                case=dict(caseId='smoke-http',groupId='smoke-http',split='smoke_new',category='api_smoke',session=session,question=question,mode='initial')
                cases.append(case);write_json(out/'inputs.json',cases)
                body={'requestId':str(uuid4()),'question':question}
                response=await client.post('/api/sessions/'+session['id']+'/turns',headers=headers,json=body)
                response.raise_for_status();result=response.json();turn=result['turns'][-1]
                before=shared.calls
                repeated=await client.post('/api/sessions/'+session['id']+'/turns',headers=headers,json=body)
                unauthorized=await client.get('/api/sessions/'+session['id'])
                row=dict(caseId='smoke-http',groupId='smoke-http',split='smoke_new',category='api_smoke',arm='H',
                    config='flash-low-8192/repair-disabled-8192',graphVersion=graph.GRAPH_VERSION,promptVersion=PROMPT_VERSION,
                    status=turn['status'],answer=turn.get('answer'),evidence=turn['evidence'],trace=turn['trace'],error=turn.get('error'),
                    sameCast=session['cast']==result['cast'],inputHash=sha(case),durationMs=round((time.monotonic()-start)*1000),budget=budget.summary(),
                    apiChecks=dict(create=created.status_code,turn=response.status_code,idempotent=repeated.json()==result and shared.calls==before,unauthorized=unauthorized.status_code))
                validations=[e for e in rec.events if e['kind']=='validation']
                prepared=next(e for e in rec.events if e['kind']=='prepare')
                row.update(initialEvidence=prepared['evidence'],initialEvidenceIds=[e['id'] for e in prepared['evidence']],targets=prepared['targets'],requireCitations=prepared['requireCitations'])
                if validations:row.update(finalCandidate=validations[-1]['candidate'],finalGenerationEvidence=validations[-1]['evidence'])
                rows.append(row);write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
                assert row['apiChecks']['idempotent'] and unauthorized.status_code==404
    manifest=read(out/'manifest.json');manifest['caseHash']=sha(cases);manifest['httpSmoke']='Isolated temporary storage; real FastAPI handlers, cast creation, model and persisted turn. Auth secret not recorded.';write_json(out/'manifest.json',manifest)
    print(json.dumps(dict(status=row['status'],checks=row['apiChecks'],budget=shared.summary()),ensure_ascii=False))


async def run_rag():
    data=read(DATA/'data.json')
    ids={c['caseId'] for c in data['cases'] if c['ragEligible']}
    cases=[fixed_case(c) for c in data['cases'] if c['caseId'] in ids]
    out,rows=start_stage('rag',cases,['memory','classic','full']);shared=shared_budget()
    for i,case in enumerate(cases):
        original,state=await prepare(case,policy=rule_route)
        all_evidence=graph.generation_evidence(state)
        arms=['memory','classic','full'];arms=arms[i%3:]+arms[:i%3]
        async def execute(arm):
            messages=copy.deepcopy(original);targets=state['targets'];requires=state['require_citations']
            evidence=[e for e in all_evidence if arm=='full' or arm=='classic' and e['layer']=='classic']
            if arm=='memory':
                messages[0]['content']=messages[0]['content'].replace('文献解释依据本轮参考原文','文献解释可依据你已有的知识')+'\n本轮未提供可核验文献，citations 使用 []。'
                targets=[];requires=False
            context=json.loads(messages[1]['content']);context['参考原文']=[{k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')} for e in evidence]
            if arm=='memory':context['资料说明']=[]
            messages[1]['content']=graph.dumps(context)
            row=next(r for r in rows if r['caseId']==case['caseId'] and r['arm']==arm)
            row.update(evidence=evidence,initialEvidence=evidence,initialEvidenceIds=[e['id'] for e in evidence],targets=targets,requireCitations=requires,config='flash-low-8192/no-repair')
            rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            budget=BudgetedChat(configured_chat(CONFIG),max_calls=1,max_tokens=100000,parent=shared)
            start=time.monotonic()
            try:
                rec(dict(kind='model_phase',phase='generate'))
                response=await RecordedChat(budget,rec)(messages)
                try: candidate=json.loads(response['message']['content'])
                except (ValueError,TypeError):candidate=None
                issues=[];resolutions=[]
                answer,errors=validate_answer(candidate,evidence,targets,requires,issues=issues,quote_resolutions=resolutions)
                rec(dict(kind='validation',candidate=candidate,answer=answer,errors=errors,issues=issues,evidence=evidence,quoteResolutions=resolutions,repairNumber=0))
                row.update(status='completed' if answer else 'validation_failed',answer=answer,finalCandidate=candidate,errors=errors,finalGenerationEvidence=evidence)
            except Exception as exc:row.update(status='failed',error=str(exc) if isinstance(exc,ModelError) else type(exc).__name__)
            row.update(durationMs=round((time.monotonic()-start)*1000),budget=budget.summary())
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
            print('rag',case['caseId'],arm,row['status'],flush=True)
        await asyncio.gather(*(execute(arm) for arm in arms))
    assert_frozen()


async def run_continuous():
    groups=read(DATA/'continuous.json')['sessions']
    # All planned rows exist before the first call, although actual history is built later.
    cases=[]
    for group in groups:
        for index,question in enumerate(group['turns']):
            raw=dict(caseId=f"{group['groupId']}-turn{index+1}",groupId=group['groupId'],split=group['split'],category='continuous',
                question=group['turns'][0],currentQuestion=question,mode='initial' if index==0 else 'followup',lineValues=group['lineValues'],history=[])
            cases.append(fixed_case(raw))
    out,rows=start_stage('continuous',cases,['H']);shared=shared_budget()
    for group in groups:
        group_cases=[c for c in cases if c['groupId']==group['groupId']]
        session=copy.deepcopy(group_cases[0]['session'])
        for case in group_cases:
            case['session']=copy.deepcopy(session)
            row=next(r for r in rows if r['caseId']==case['caseId']);row['inputHash']=sha(case)
            write_json(out/'inputs.json',cases)
            await graph_cell(case,'H',out,row,shared)
            if row['status']=='completed':
                session['turns'].append(dict(id=case['caseId'],question=case['question'],mode=case['mode'],status='completed',answer=row['answer'],evidence=row['evidence']))
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',shared.summary())
    manifest=read(out/'manifest.json');manifest['caseHash']=sha(cases);manifest['history']='Real preceding completed answers; failed turns not treated as facts.';write_json(out/'manifest.json',manifest)
    assert_frozen()


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('stage',choices=['smoke','smoke-http','freeze','development','test','rag','continuous']);parser.add_argument('--output-root',type=Path);args=parser.parse_args();BASE=args.output_root or BASE
    BASE.mkdir(parents=True,exist_ok=True)
    if args.stage=='freeze':freeze()
    else:asyncio.run(api_smoke() if args.stage=='smoke-http' else run_rag() if args.stage=='rag' else run_continuous() if args.stage=='continuous' else run_graph(args.stage))
