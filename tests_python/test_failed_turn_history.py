"""User corrections survive a failed answer; untrusted assistant output does not."""
import copy
import json
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from backend.graph import history, build_graph, initial_state
from tests_python.test_migration import session, response


def failure_session():
    saved=session()
    saved['turns']=[
        dict(status='completed',question='原定周末搬家。',evidence=[],answer=dict(paragraphs=[dict(text='先确定搬家时间。',citations=[])])),
        dict(status='failed',question='改成周三上午了，妈妈也同意不点火。',evidence=[],
             answer=dict(summary='INVALID_ASSISTANT_GUESS',paragraphs=[dict(text='UNTRUSTED_FAILED_CANDIDATE',citations=[])],limitations=['UNTRUSTED_LIMITATION']),
             trace=[dict(text='INTERNAL_ERROR')]),
        dict(status='running',question='那给我一张当天清单。',evidence=[])]
    return saved


def test_failed_user_preserved_without_failed_assistant_or_current_running():
    saved=failure_session();before=copy.deepcopy(saved)
    visible=history(saved)
    assert [t['user'] for t in visible]==['原定周末搬家。','改成周三上午了，妈妈也同意不点火。']
    assert visible[0]['assistant'].endswith('先确定搬家时间。')
    assert visible[1]['assistant']==''
    assert saved==before
    # A retry marks the existing failed row running; it is not another history entry.
    saved['turns'][1]['status']='running'
    assert len(history(saved))==1


async def test_both_router_and_generation_see_failed_correction_and_same_cast():
    saved=failure_session();seen=[]
    async def chat(messages,tools=None):
        context=json.loads(messages[1]['content'])
        past=context['recentHistory' if tools else '近期对话']
        assert past[-1]==dict(user='改成周三上午了，妈妈也同意不点火。',assistant='')
        assert all(t['user']!='那给我一张当天清单。' for t in past)
        assert 'UNTRUSTED' not in messages[1]['content'] and 'INVALID_ASSISTANT_GUESS' not in messages[1]['content']
        seen.append('route' if tools else 'generate')
        return response({'ready':True} if tools else dict(status='answered',paragraphs=[dict(text='按周三上午、不用明火的安排准备即可。',citations=[])]))
    result=await build_graph(InMemorySaver(),chat).ainvoke(initial_state(saved,'那给我一张当天清单。','followup'),
        {'configurable':{'thread_id':'failed-correction-preserved'}})
    assert seen==['route','generate'] and not result['errors']
    assert result['session']['cast']==saved['cast']


def test_actual_random_failure_replay_recovers_missing_user_facts():
    source=Path(__file__).parents[1]/'evaluation/reports/context-20261011/confirmation/rows.json'
    row=next(r for r in json.loads(source.read_text()) if r['recordId']=='context_movein_resolved-3')
    visible=history(row['inputSession'])
    assert len(visible)==2 and visible[-1]['assistant']==''
    assert '周三上午' in visible[-1]['user'] and '妈妈已经同意不点火了' in visible[-1]['user']
    assert '周末没空' in visible[-1]['user']


def test_six_terminal_turn_window_keeps_failed_user_when_assistants_are_long():
    saved=session()
    saved['turns']=[dict(status='completed' if i%2==0 else 'failed',question=f'第{i}条用户条件',
        answer=dict(paragraphs=[dict(text='前提'+('长分析'*3000)+'末尾限制',citations=[])])) for i in range(9)]
    visible=history(saved)
    assert [t['user'] for t in visible]==[t['question'] for t in saved['turns'][-6:]]
    assert all(not t['assistant'] for t in visible if int(t['user'][1])%2==1)
