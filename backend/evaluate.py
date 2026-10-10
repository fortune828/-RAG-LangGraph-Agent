"""python -m backend.evaluate [--live --max-calls 8 --max-tokens 100000].

Default is offline. Live runs synthetic development cases only, never runtime
user sessions; outputs are explicitly not held-out or expert quality scores.
"""
import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import MODEL, ROOT, MAX_COMPLETION_TOKENS
from .domain import calculate_hexagram, now, sample_cast
from .graph import GRAPH_VERSION, build_graph, initial_state
from .knowledge import CURRENT_RELEASE, retrieve_exact
from .llm import ModelError, deepseek_chat
from .prompts import PROMPT_VERSION


class BudgetedChat:
    def __init__(self, chat=deepseek_chat, max_calls=8, max_tokens=100000):
        self.chat, self.max_calls, self.max_tokens = chat, max_calls, max_tokens
        self.calls = self.reported = self.accounted = self.unknown = 0

    async def __call__(self, messages, tools=None):
        reservation = len(json.dumps(dict(messages=messages, tools=tools), ensure_ascii=False).encode()) + MAX_COMPLETION_TOKENS + 1024
        if self.calls >= self.max_calls or self.accounted + reservation > self.max_tokens:
            raise ModelError('EVALUATION_BUDGET_EXHAUSTED')
        self.calls += 1
        self.accounted += reservation
        try:
            result = await self.chat(messages, tools=tools)
        except Exception:
            self.unknown += 1
            raise
        used = result.get('usage', {}).get('total_tokens')
        if isinstance(used, int) and used >= 0:
            self.reported += used
            self.accounted += used - reservation
        else:
            self.unknown += 1
        return result

    def summary(self):
        return dict(calls=self.calls, reportedTokens=self.reported, accountedTokens=self.accounted,
                    missingUsageCalls=self.unknown, maxCalls=self.max_calls, maxTokens=self.max_tokens)


async def live(args):
    directory = ROOT / 'evaluation/results' / ('python-live-' + time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid4().hex[:6])
    directory.mkdir(parents=True)
    model = BudgetedChat(max_calls=args.max_calls, max_tokens=args.max_tokens)
    report = dict(mode='real_model_development_smoke', humanReviewed=False, model=MODEL, thinking="enabled", reasoningEffort="high", maxCompletionTokens=MAX_COMPLETION_TOKENS,
                  releaseId=CURRENT_RELEASE, promptVersion=PROMPT_VERSION, graphVersion=GRAPH_VERSION, rows=[])
    s = dict(id=str(uuid4()), question='我在考虑换工作，但担心收入不稳定，怎样整理思路？',
             cast=sample_cast() | calculate_hexagram([7] * 6), releaseId=CURRENT_RELEASE, turns=[], createdAt=now())
    # This is an explicit fixed fixture, not a production random cast claim.
    s['cast'].pop('coins')
    s['cast']['algorithmVersion'] = 'evaluation-fixed-lines'
    cases = [('initial', s['question']), ('followup', '我有三个月的积蓄，目前没有拿到新公司的录用通知，这一点怎么考虑？'),
             ('followup', '请给出本卦第二爻原文，并结合刚才的处境说明。')]
    def save():
        report['budget'] = model.summary()
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='yijing-python-eval-') as temp:
        async with AsyncSqliteSaver.from_conn_string(str(Path(temp) / 'checkpoints.sqlite')) as saver:
            graph = build_graph(saver, model)
            for mode, question in cases:
                tid = str(uuid4())
                try:
                    result = await asyncio.wait_for(graph.ainvoke(initial_state(s, question, mode),
                        {'configurable': {'thread_id': tid}}, durability='sync'), 150)
                    turn = dict(id=tid, question=question, mode=mode,
                                status='failed' if result.get('errors') else 'completed', createdAt=now(), model=MODEL,
                                answer=result['answer'], evidence=result['evidence'], trace=result['trace'],
                                validationErrors=result.get('errors', []))
                    s['turns'].append(turn)
                    report['rows'].append(turn | {'semanticReview': 'pending', 'sameCast': result['session']['cast'] == s['cast']})
                except Exception as exc:
                    report['rows'].append(dict(question=question, status='failed', error=str(exc) if isinstance(exc, ModelError) else type(exc).__name__))
                    save()
                    break
                save()
    print(json.dumps(dict(report=str(directory / 'report.json'), budget=model.summary(),
                         statuses=[r.get('answer', {}).get('status', r['status']) for r in report['rows']]), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--max-calls', type=int, default=8)
    parser.add_argument('--max-tokens', type=int, default=100000)
    args = parser.parse_args()
    if args.max_calls < 1 or args.max_tokens < 1:
        parser.error('budgets must be positive')
    if args.live:
        asyncio.run(live(args))
    else:
        for number in range(1, 65):
            records = retrieve_exact(dict(hexagramId=number, sections=['judgment']))['evidence']
            assert [e['id'] for e in records] == [f'zy-{number}-judgment']
        print(json.dumps(dict(mode='offline_exact_contract', passed=64, total=64, releaseId=CURRENT_RELEASE)))


if __name__ == '__main__':
    main()
