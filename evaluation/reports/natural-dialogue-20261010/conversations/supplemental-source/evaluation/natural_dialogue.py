"""Fixed-context v14/v15 generation comparison, real or explicitly scripted.

This isolates generation: identical historical user payload, evidence and history;
no claim that routing/history changes have been tested by the paired comparison.
Actual production-path multiround experience is recorded separately via the API.
"""
import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path

from backend.config import ROOT, MODEL
from backend.evaluate import BudgetedChat
from backend.llm import deepseek_chat, request_payload
from backend.prompts import SYSTEM, PROMPT_VERSION
from backend.graph import GRAPH_VERSION
from backend.knowledge import CURRENT_RELEASE, load_release
from backend.validation import validate_answer
from evaluation.paired import Recorder, RecordedChat, sha, write_json, smoke_chat
from evaluation.scoring import schema_valid

ARCHIVE = ROOT / 'evaluation/reports/phase1-20261010/complete'
CASES = ('lunch-choice', 'haircut-tai', 'haircut-kan', 'project-decision', 'life-correction', 'lunch-specific')


def inputs():
    rows = json.loads((ARCHIVE / 'rows.json').read_text())
    selected = []
    for cid in CASES:
        row = next(r for r in rows if r['caseId'] == cid and r['variant'] == 'D')
        events = [json.loads(s) for s in (ARCHIVE / row['traceFile']).read_text().splitlines()]
        request = next(e for e in events if e['kind'] == 'model_request' and e['stage'] == 'generate')
        selected.append(dict(caseId=cid, messages=request['payload']['messages'], archivedTrace=row['traceFile']))
    return selected


async def run(output, live):
    output.mkdir(parents=True, exist_ok=False)
    cases = inputs()
    write_json(output / 'inputs.json', cases)
    budget = BudgetedChat(deepseek_chat if live else smoke_chat, max_calls=12, max_tokens=400000)
    files = list((ROOT / 'backend').glob('*.py')) + list((ROOT / 'evaluation').glob('*.py')) + [ROOT / 'requirements.txt']
    for p in files:
        target = output / 'source' / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
    write_json(output / 'manifest.json', dict(mode='real_model' if live else 'scripted_mechanical_only',
        generationOnly=True, versions=['evidence-agent-python-v14', PROMPT_VERSION], graphVersion=GRAPH_VERSION,
        releaseId=CURRENT_RELEASE, model=MODEL, modelOptions=request_payload([]),
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
        sourceHashes={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in files}, inputSha256=sha(cases),
        note='Six development pairs, same user messages/evidence/history. Alternating order. One generation per arm; no repair or best-of selection. Alias drift and sampling noise remain. Not held-out or expert scores.'))
    rows = []
    corpus = {e['id']: e for e in load_release()[0]}
    for index, case in enumerate(cases):
        for version in (('v14', 'v15') if index % 2 == 0 else ('v15', 'v14')):
            messages = json.loads(json.dumps(case['messages']))
            if version == 'v15':
                messages[0]['content'] = SYSTEM
            recorder = Recorder(output / f"{case['caseId']}-{version}.jsonl", case['caseId'], version)
            chat = RecordedChat(budget, recorder)
            started = time.monotonic()
            row = dict(caseId=case['caseId'], version=version, inputSha256=sha(messages[1:]))
            try:
                result = await chat(messages)
                candidate = json.loads(result['message']['content'])
                row.update(answer=candidate, schemaValid=schema_valid(result['message']['content'], legacy=version=='v14'),
                           usage=result.get('usage'), status='completed')
                if version == 'v15':
                    context = json.loads(messages[1]['content'])
                    ev = [corpus[e['id']] for e in context.get('参考原文', [])]
                    _, errors = validate_answer(candidate, ev, require_citations=context.get('本轮阶段')=='首次回应')
                    row['validationErrors'] = errors
            except Exception as exc:
                row.update(status='failed', error=type(exc).__name__ + ':' + str(exc))
            row['durationMs'] = round((time.monotonic() - started)*1000)
            rows.append(row)
            write_json(output / 'rows.json', rows)
            write_json(output / 'budget.json', budget.summary())
            print(case['caseId'], version, row['status'], flush=True)
    lines = ['# 同输入生成对照（开发样例，非正式评分）', '', '只改变系统提示词和输出契约；完整输入、调用、错误见同目录 JSON/JSONL。未做修复重试或挑选最佳输出。', '']
    for case in cases:
        lines += ['## ' + case['caseId'], '', json.loads(case['messages'][1]['content'])['当前问题'], '']
        for row in [r for r in rows if r['caseId']==case['caseId']]:
            a = row.get('answer', {})
            lines += ['### ' + row['version'], '', a.get('summary', '')]
            lines += [p['text'] + '\n' for p in a.get('paragraphs', [])]
            if row.get('error'): lines += [row['error']]
    (output / 'answers.md').write_text('\n'.join(lines))
    print(json.dumps(budget.summary()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args.output, args.live))
