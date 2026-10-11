"""Evaluation must run the real session API without replacing sampled casts."""
import copy
import json

import pytest
from backend import store
from backend.domain import calculate_hexagram
from backend.llm import request_payload
from evaluation import context_experiment as experiment


def test_role_replay_preserves_all_history_and_evidence_without_mutation():
    context={'核心问题':'选哪天？','当前问题':'改成周日了，怎么通知？',
             '近期对话':[{'user':'原来周六。','assistant':'还需确认。'}],
             '卦象':{'本卦':'乾','变卦':'坤'},'参考原文':[{'id':'example','text':'原典文字'}]}
    original=[dict(role='system',content='任务及协议'),dict(role='user',content=json.dumps(context,ensure_ascii=False))]
    saved=copy.deepcopy(original)
    role=experiment.rearrange(original,'roles')
    background=experiment.rearrange(original,'background')
    assert original==saved
    assert role[1:3]==[dict(role='user',content='原来周六。'),dict(role='assistant',content='还需确认。')]
    assert json.loads(role[-1]['content'])['参考原文']==context['参考原文']
    assert background[-1]==dict(role='user',content=context['当前问题'])
    assert json.loads(background[1]['content'])['卦象']==context['卦象']


async def test_api_runner_samples_once_persists_history_and_refuses_overwrite(tmp_path,monkeypatch):
    monkeypatch.setattr(experiment,'BASE',tmp_path)
    sampled=[]
    original=store.sample_cast
    def record_cast():
        value=original();sampled.append(copy.deepcopy(value));return value
    monkeypatch.setattr(store,'sample_cast',record_cast)
    async def model(messages,tools=None):
        if tools:
            message=dict(role='assistant',content='{"ready":true}')
        else:
            contexts=[]
            for m in messages:
                try: value=json.loads(m['content'])
                except (TypeError,ValueError):continue
                if isinstance(value,dict) and '参考原文' in value:contexts.append(value)
            e=contexts[-1]['参考原文'][0]
            answer=dict(status='answered',paragraphs=[dict(text='模拟回应。',citations=[dict(evidenceId=e['id'],quote=e['text'],claim='模拟回应。')])],limitations=[])
            message=dict(role='assistant',content=json.dumps(answer,ensure_ascii=False))
        return dict(message=message,usage=dict(prompt_tokens=3,completion_tokens=2,total_tokens=5),model='mock',finishReason='stop')
    model.request_payload=request_payload
    monkeypatch.setattr(experiment,'configured_chat',lambda *args,**kwargs:model)
    cases=[dict(caseId='one',questions=['今天出门走走？','我改成傍晚去。']),dict(caseId='two',questions=['要去看个画展吗？'])]
    rows=await experiment.conversations('confirmation',cases)
    assert len(sampled)==2 and len(rows)==3
    for row in rows:
        cast=next(c for c in sampled if c['id']==row['cast']['id'])
        assert row['cast']==cast and row['sameCast']
        assert [6+sum(coins) for coins in cast['coins']]==cast['lines']
        assert calculate_hexagram(cast['lines'])['changed']==cast['changed']
        assert row['status']=='completed'
    assert rows[1]['inputSession']['turns'][0]['answer']==rows[0]['answer']
    with pytest.raises(FileExistsError):
        await experiment.conversations('confirmation',cases)
    assert len(sampled)==2
