"""Development fixtures must not silently inject an unrelated conversation."""
import json

import pytest

from backend.graph import history
from evaluation.development_checks import CASES, scripted_chat, session_for


@pytest.mark.parametrize('case', [c for c in CASES if sum(x['session'] == c['session'] for x in CASES) == 1], ids=lambda c: c['id'])
def test_independent_scenario_has_own_root_question(case):
    session = session_for(case)
    assert session['question'] == case['question']
    # Paired cases intentionally reuse a question with a different cast.
    assert session['cast']['lines'] == case.get('lines', [7] * 6)
    if case['id'] != 'history-window':
        assert history(session) == []


def test_life_chain_preserves_root_and_corrected_facts():
    life = [c for c in CASES if c['session'] == 'life']
    session = session_for(life[0])
    for case in life:
        assert session_for(case)['question'] == life[0]['question']
        assert [entry['user'] for entry in history(session)] == [turn['question'] for turn in session['turns']]
        session['turns'].append(dict(status='completed', question=case['question'],
                                    answer=dict(summary='模拟回答', paragraphs=[])))
    assert history(session)[2]['user'] == life[2]['question']
    assert '一个月' in history(session)[2]['user']


def test_history_window_cannot_leak_evicted_savings_from_root():
    case = next(c for c in CASES if c['id'] == 'history-window')
    session = session_for(case)
    assert len(session['turns']) == 7
    visible = history(session)
    assert len(visible) == 6
    assert '17个月' not in json.dumps(dict(question=session['question'], history=visible), ensure_ascii=False)
    assert visible[0]['user'] == session['turns'][1]['question']
    assert visible[-1]['user'] == session['turns'][-1]['question']


@pytest.mark.asyncio
async def test_scripted_generation_uses_final_evidence_and_scoped_citation():
    case = next(c for c in CASES if c['id'] == 'source-line')
    evidence = dict(id='zy-1-line-2', text='见龙在田，利见大人。')
    messages = [dict(role='system', content='test'), dict(role='user', content=json.dumps(
        {'当前问题': case['question'], '参考原文': [evidence]}, ensure_ascii=False))]
    result = await scripted_chat(messages)
    paragraph = json.loads(result['message']['content'])['paragraphs'][0]
    assert 'kind' not in paragraph
    assert paragraph['citations'][0]['claim'] in paragraph['text']
    assert paragraph['citations'][0]['evidenceId'] == evidence['id']


@pytest.mark.asyncio
async def test_scripted_missing_material_is_uncited_body():
    case = next(c for c in CASES if c['id'] == 'source-missing')
    messages = [dict(role='system', content='test'), dict(role='user', content=json.dumps(
        {'当前问题': case['question'], '参考原文': []}, ensure_ascii=False))]
    result = await scripted_chat(messages)
    answer = json.loads(result['message']['content'])
    assert answer['status'] == 'insufficient'
    assert 'kind' not in answer['paragraphs'][0] and answer['paragraphs'][0]['text']
    assert answer['paragraphs'][0]['citations'] == []
