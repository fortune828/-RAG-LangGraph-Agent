"""Experiment controls and accounting, not subjective answer-quality tests."""
import json

from evaluation import autonomous as experiment
from evaluation import autonomous_review as review


def test_fixture_whitelist_does_not_send_gold_or_review_notes():
    case=dict(caseId='control',question='继续完善项目？',currentQuestion='我已开始投递。',mode='followup',
        lineValues=[7]*6,history=[dict(user='还没投递',assistant='可能要继续准备')],
        expectedEvidenceGroups=[['SECRET_GOLD']],reviewPoints=['SECRET_REVIEW'],retrievalNecessity='SECRET_NECESSITY')
    prepared=experiment.fixture(case)
    content=json.dumps(prepared,ensure_ascii=False)
    assert 'SECRET_' not in content
    assert prepared['session']['turns'][0]['question']=='还没投递'
    assert prepared['session']['turns'][0]['answer']['paragraphs'][0]['text']=='可能要继续准备'
    assert prepared['question']=='我已开始投递。'
    assert experiment.fixture(case)==prepared  # UUID/coin sampling does not leak into fixtures.


def test_rescore_keeps_unknown_components_and_tool_statuses(tmp_path):
    rows=[dict(caseId='case',arm='arm',status='failed',durationMs=10)]
    (tmp_path/'rows.json').write_text(json.dumps(rows))
    events=[dict(kind='model_result',budgetCounted=True,response={'usage':{'total_tokens':23}}),
        dict(kind='model_result',budgetCounted=True,response={}),
        dict(kind='model_result',budgetCounted=False,response={}),
        dict(kind='tool_request'),dict(kind='tool_result',executionStatus='rejected',budgetCounted=True)]
    (tmp_path/'case-arm.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    experiment.summarize(tmp_path)
    row=json.loads((tmp_path/'rows.json').read_text())[0]
    assert row['auto']['calls']==2 and row['auto']['reportedTokens']==23 and row['auto']['unknownUsage']==1
    assert row['auto']['promptTokens'] is None and row['auto']['completionTokens'] is None and row['auto']['reasoningTokens'] is None
    assert row['toolCounts']==dict(ok=0,empty=0,rejected=1,budgetCounted=1)


def test_cost_estimate_cannot_fill_missing_usage_with_zero(tmp_path,monkeypatch):
    monkeypatch.setattr(review,'BASE',tmp_path)
    cap=tmp_path/'capabilities';cap.mkdir()
    (cap/'price_table.json').write_text(json.dumps(dict(source='test',observedDate='2026-10-10',models={'model':{'off_peak':dict(input_cache_hit=1,input_cache_miss=2,output=3)}})))
    run=tmp_path/'stage';run.mkdir()
    events=[dict(kind='model_request',callId=1,payload={'model':'model','thinking':{'type':'disabled'}}),
        dict(kind='model_result',callId=1,budgetCounted=True,stage='generate',status='error',durationMs=1,response={'usage':{'total_tokens':100,'completion_tokens':90}})]
    (run/'trace.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    review.usage_report()
    report=json.loads((tmp_path/'usage.json').read_text())
    assert report['unknownCostCalls']==1 and report['rows'][0]['costEstimateUSD'] is None
    assert report['knownTotalTokens']==100 and report['calls']==1
