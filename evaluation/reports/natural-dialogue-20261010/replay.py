"""Offline final-code replay of the saved synthetic HTTP sessions; no model API.
Run from the repository: .venv/bin/python evaluation/reports/natural-dialogue-20261010/replay.py
"""
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from langgraph.checkpoint.memory import InMemorySaver
from backend.graph import build_graph, initial_state
from backend.llm import ModelError, request_payload
from evaluation.paired import sha, write_json

DIRECTORY = Path(__file__).resolve().parent


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if k not in ('reasoning_content', 'reasoningOmitted', 'durationMs')}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


async def run():
    sessions = json.loads((DIRECTORY / 'independent/api-sessions.json').read_text())
    events = [json.loads(s) for s in (DIRECTORY / 'independent/model-calls.jsonl').read_text().splitlines()]
    results = {e['callId']: e for e in events if e['kind'] == 'model_result'}
    by_question = {}
    for event in events:
        if event['kind'] != 'model_request': continue
        context = json.loads(event['payload']['messages'][1]['content'])
        question = context.get('currentQuestion', context.get('当前问题'))
        by_question.setdefault(question, []).append(event)
    rows = []
    for saved in sessions:
        session = dict(saved, turns=[])
        for turn in saved['turns']:
            requests = by_question.get(turn['question'], [])
            consumed = []
            mismatches = []

            async def replay(messages, tools=None):
                index = len(consumed)
                if index >= len(requests): raise AssertionError('FINAL_CODE_REQUESTED_EXTRA_MODEL_CALL')
                request = requests[index]
                consumed.append(request['callId'])
                if canonical(request_payload(messages, tools)) != canonical(request['payload']):
                    mismatches.append(request['callId'])
                response = results[request['callId']]
                if response['status'] != 'ok':
                    raise ModelError(response['error'], response.get('response'))
                return copy.deepcopy(response['response'])

            row = dict(sessionId=saved['id'], turnId=turn['id'], question=turn['question'])
            try:
                result = await build_graph(InMemorySaver(), replay).ainvoke(
                    initial_state(session, turn['question'], turn['mode']),
                    {'configurable': {'thread_id': turn['id']}})
                row.update(status='completed' if not result['errors'] else 'failed',
                           validationErrors=result['errors'], answerIdentical=result['answer']==turn.get('answer'),
                           castPreserved=result['session']['cast']==saved['cast'])
            except Exception as exc:
                row.update(status='failed', error=str(exc), errorIdentical=str(exc)==turn.get('error'))
            row.update(payloadMismatches=mismatches, callsConsumed=len(consumed), recordedCalls=len(requests))
            rows.append(row)
            session['turns'].append(turn)
    write_json(DIRECTORY / 'final-code-replay.json', dict(mode='offline_recorded_response_replay_not_new_generation',
        note='Visible payload equality excludes internal reasoning and timing; validates final boundary/history fixes against actual sampled inputs, not unseen queries or semantic truth.',
        sourceHashes={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (ROOT/'backend').glob('*.py')}, rows=rows))
    print(json.dumps(dict(turns=len(rows), payloadMismatches=sum(bool(r['payloadMismatches']) for r in rows),
        changedAnswers=sum(r.get('answerIdentical') is False for r in rows),
        changedErrors=sum(r.get('errorIdentical') is False for r in rows)), ensure_ascii=False))

if __name__ == '__main__':
    asyncio.run(run())
