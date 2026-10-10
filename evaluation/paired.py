"""Phase-one paired runner. Called by backend.evaluate; never reads runtime/.

Inputs are fixed, synthetic development turns. C and D use the same graph with
only the retrieval decision changed. No gold loader or automatic semantic judge.
"""
import asyncio
import copy
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.config import MODEL, ROOT
from backend.domain import now
from backend.evaluate import BudgetedChat
from backend.graph import GRAPH_VERSION, build_graph, initial_state
from backend.knowledge import CURRENT_RELEASE, load_release, lexical_index
from backend.llm import ModelError, deepseek_chat, request_payload
from backend.prompts import PROMPT_VERSION
from evaluation.workflow import POLICY_VERSION, rule_route

PROTOCOL = 'paired-phase1-v1'
CASE_PATH = ROOT / 'evaluation/phase1/cases.json'
BASELINE_PATH = ROOT / 'evaluation/baselines/phase1-v1.json'


def sha(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def visible(value):
    """Keep visible protocol data; never export model-internal reasoning text."""
    if isinstance(value, list):
        return [visible(v) for v in value]
    if isinstance(value, dict):
        out = {k: visible(v) for k, v in value.items() if k != 'reasoning_content'}
        if value.get('reasoning_content'):
            out['reasoningOmitted'] = dict(sha256=sha(value['reasoning_content']), characters=len(value['reasoning_content']))
        return out
    return value


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


class Recorder:
    def __init__(self, path, case_id, variant):
        self.path, self.case_id, self.variant = path, case_id, variant
        self.events = []

    def __call__(self, event):
        row = visible(dict(event, eventId=len(self.events) + 1, timestamp=now(),
                           caseId=self.case_id, variant=self.variant))
        self.events.append(row)
        with self.path.open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


class RecordedChat:
    def __init__(self, budget, recorder):
        self.budget, self.recorder = budget, recorder
        self.sequence = 0

    async def __call__(self, messages, tools=None):
        self.sequence += 1
        call_id = self.sequence
        stage = 'route' if tools else 'repair' if len(messages) > 2 else 'generate'
        payload = request_payload(messages, tools)
        self.recorder(dict(kind='model_request', callId=call_id, stage=stage,
                           payload=payload, payloadSha256=sha(payload)))
        started, before = time.monotonic(), self.budget.calls
        try:
            result = await self.budget(messages, tools=tools)
        except BaseException as exc:
            self.recorder(dict(kind='model_result', callId=call_id, stage=stage,
                status='budget_stopped' if str(exc) == 'EVALUATION_BUDGET_EXHAUSTED' else 'error',
                budgetCounted=self.budget.calls > before, error=str(exc) if isinstance(exc, ModelError) else type(exc).__name__,
                response=getattr(exc, 'metadata', {}), durationMs=round((time.monotonic() - started) * 1000)))
            raise
        self.recorder(dict(kind='model_result', callId=call_id, stage=stage, status='ok',
                           budgetCounted=True, response=result, durationMs=round((time.monotonic() - started) * 1000)))
        return result


async def smoke_chat(messages, tools=None):
    """A graph/recorder smoke, not a model-quality or C/D performance claim."""
    if tools:
        message = dict(role='assistant', content='{"ready":true}')
    else:
        context = json.loads(messages[1]['content'])
        evidence = context.get('参考原文', [])
        missing = any('未收录' in note or '书名尚未确认' in note for note in context.get('资料说明', []))
        answer = dict(status='insufficient' if missing else 'answered', summary='模拟输出，只验证采集。',
            paragraphs=[dict(kind='interpretation' if evidence and not missing else 'reflection', text='模拟内容。',
                citations=[dict(evidenceId=e['id'], quote=e['text'][:10]) for e in evidence[:5]] if evidence and not missing else [])],
            limitations=['模拟资料不足。'] if missing else [])
        message = dict(role='assistant', content=json.dumps(answer, ensure_ascii=False))
    return dict(message=message, usage={'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10},
                model='scripted-phase1', finishReason='stop')


def load_cases():
    data = json.loads(CASE_PATH.read_text())
    assert 12 <= len(data['cases']) <= 16
    assert len({c['caseId'] for c in data['cases']}) == len(data['cases'])
    allowed = {'caseId', 'groupId', 'scenario', 'split', 'mode', 'question', 'session', 'inputOrigin', 'historyOrigin'}
    for case in data['cases']:
        assert set(case) <= allowed and case['split'] == 'development'
        assert case['session']['releaseId'] == CURRENT_RELEASE
    return data


def manifest(args, output):
    baseline = json.loads(BASELINE_PATH.read_text())
    # Freeze actual behavior, not merely the human-readable version strings.
    for name in ('backend/prompts.py', 'backend/knowledge.py', 'backend/models.py', 'backend/validation.py', 'backend/domain.py'):
        if sha((ROOT / name).read_bytes()) != baseline['files'][name]:
            raise ValueError('FROZEN_BEHAVIOR_CHANGED:' + name)
    if (GRAPH_VERSION, PROMPT_VERSION, CURRENT_RELEASE) != (baseline['graphVersion'], baseline['promptVersion'], baseline['releaseId']):
        raise ValueError('FROZEN_VERSION_CHANGED')
    if MODEL != baseline['modelId']:
        raise ValueError('FROZEN_MODEL_CHANGED')
    corpus_manifest = ROOT / 'data/knowledge/releases' / CURRENT_RELEASE / 'manifest.json'
    if sha(corpus_manifest.read_bytes()) != baseline['corpusManifestSha256']:
        raise ValueError('FROZEN_CORPUS_CHANGED')
    files = sorted(list((ROOT / 'backend').glob('*.py')) + list((ROOT / 'evaluation').glob('*.py')))
    files += [ROOT / 'requirements.txt', CASE_PATH, BASELINE_PATH]
    return dict(protocol=PROTOCOL, runId=output.name, createdAt=now(),
        mode='real_model_development' if args.live else 'mock_mechanical_smoke', split='development',
        baseline=baseline, runtimeCommit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
        codeHashes={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in files},
        python=platform.python_version(), model=MODEL if args.live else 'scripted-phase1',
        modelOptions={k:v for k,v in request_payload([]).items() if k != 'messages'},
        temperature='not_sent', seed='not_sent', policies={'C': POLICY_VERSION, 'D': 'production-v9-model-route'},
        limits=dict(perCaseCalls=args.case_max_calls, perCaseTokens=args.case_max_tokens,
                    globalCalls=args.max_calls, globalTokens=args.max_tokens, toolAttempts=3, repairs=1, timeoutSeconds=150),
        order='alternate C,D / D,C by fixed case index; serial execution',
        cache='corpus and lexical index prewarmed outside timed turns; provider cache usage retained when reported',
        capture='synthetic fixtures only; no credentials, runtime sessions or model-internal reasoning text; omitted reasoning hashed',
        semanticScoring='pending human review; no gold or expected-answer data loaded by runner or policies')


async def run(args):
    from evaluation.scoring import rescore, make_review_pack
    dataset = load_cases()
    label = 'real' if args.live else 'mock'
    output = ROOT / 'evaluation/results' / f'paired-{label}-{time.strftime("%Y%m%d-%H%M%S", time.gmtime())}-{uuid4().hex[:6]}'
    run_manifest = manifest(args, output)
    output.mkdir(parents=True)
    (output / 'traces').mkdir()
    write_json(output / 'manifest.json', run_manifest)
    write_json(output / 'inputs.json', dataset)
    write_json(output / 'baseline.json', run_manifest['baseline'])
    (output / 'requirements.txt').write_bytes((ROOT / 'requirements.txt').read_bytes())
    # Save source snapshots to support inspection even before a git commit exists.
    for name in run_manifest['codeHashes']:
        dest = output / 'source' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / name).read_bytes())
    shared = BudgetedChat(max_calls=args.max_calls, max_tokens=args.max_tokens)
    rows = [dict(caseId=c['caseId'], groupId=c['groupId'], scenario=c['scenario'], variant=v,
                 status='not_run', semanticReview='pending') for c in dataset['cases'] for v in ('C','D')]
    write_json(output / 'rows.json', rows)
    load_release(CURRENT_RELEASE)
    lexical_index(CURRENT_RELEASE)
    # Persist progress while running; remove checkpoints after exporting node updates.
    async with AsyncSqliteSaver.from_conn_string(str(output / 'checkpoints.sqlite')) as saver:
        for index, case in enumerate(dataset['cases']):
            for variant in (('C','D') if index % 2 == 0 else ('D','C')):
                row = next(r for r in rows if r['caseId'] == case['caseId'] and r['variant'] == variant)
                recorder = Recorder(output / 'traces' / f"{case['caseId']}-{variant}.jsonl", case['caseId'], variant)
                budget = BudgetedChat(chat=deepseek_chat if args.live else smoke_chat,
                    max_calls=args.case_max_calls, max_tokens=args.case_max_tokens, parent=shared)
                graph = build_graph(saver, RecordedChat(budget, recorder),
                    route_policy=rule_route if variant == 'C' else None, observe=recorder)
                session = copy.deepcopy(case['session'])
                state = initial_state(session, case['question'], case['mode'])
                config = dict(configurable={'thread_id': f"{case['caseId']}:{variant}"}, recursion_limit=24)
                row.update(status='running', inputSha256=sha(case), traceFile=f"traces/{case['caseId']}-{variant}.jsonl")
                write_json(output / 'rows.json', rows)
                started = time.monotonic()
                async def execute():
                    async for update in graph.astream(state, config, stream_mode='updates', durability='sync'):
                        recorder(dict(kind='node_update', update=update))
                    return (await graph.aget_state(config)).values
                try:
                    result = await asyncio.wait_for(execute(), timeout=150)
                    row.update(status='failed' if result.get('errors') else 'completed',
                               error='ANSWER_VALIDATION_FAILED' if result.get('errors') else None)
                except Exception as exc:
                    code = str(exc) if isinstance(exc, ModelError) else type(exc).__name__
                    row.update(status='budget_stopped' if code == 'EVALUATION_BUDGET_EXHAUSTED' else 'failed', error=code)
                    snapshot = await graph.aget_state(config)
                    result = snapshot.values
                    row['pendingNodes'] = list(snapshot.next)
                    recorder(dict(kind='execution_error', error=code))
                row.update(durationMs=round((time.monotonic() - started) * 1000), budget=budget.summary(),
                           answer=result.get('answer'), finalEvidence=result.get('evidence', []),
                           validationErrors=result.get('errors', []), trace=result.get('trace', []),
                           sameCast=result.get('session', {}).get('cast') == case['session']['cast'])
                prepared = next((e for e in recorder.events if e['kind'] == 'prepare'), None)
                row['initialEvidence'] = prepared['evidence'] if prepared else None
                row['initialEvidenceSha256'] = sha(row['initialEvidence']) if prepared else None
                validations = [e for e in recorder.events if e['kind'] == 'validation']
                row['finalGenerationEvidence'] = validations[-1]['evidence'] if validations else None
                write_json(output / 'rows.json', rows)
                write_json(output / 'budget.json', shared.summary())
                print(json.dumps(dict(caseId=case['caseId'], variant=variant, status=row['status'], calls=budget.calls), ensure_ascii=False), flush=True)
            if shared.calls >= shared.max_calls or (row['status'] == 'budget_stopped' and row['budget']['calls'] == 0):
                break
    for suffix in ('', '-wal', '-shm'):
        (output / ('checkpoints.sqlite' + suffix)).unlink(missing_ok=True)
    rescore(output)
    make_review_pack(output)
    print(json.dumps(dict(output=str(output), budget=shared.summary()), ensure_ascii=False))
    return output
