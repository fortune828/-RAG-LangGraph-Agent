import json
from backend.domain import sample_cast
from evaluation.context_results import recompute
from evaluation.paired import write_json


def test_failure_unknown_usage_and_pending_are_distinct(tmp_path):
    write_json(tmp_path/'plan.json',dict(price=dict(cacheHit=.003,cacheMiss=.15,output=.6)))
    out=tmp_path/'confirmation';out.mkdir()
    cast=sample_cast()
    failed=dict(recordId='failed',groupId='one',cast=cast,status='failed',error='MODEL_TIMEOUT_OR_NETWORK',durationMs=1000,errors=[])
    pending=dict(recordId='pending',groupId='one',cast=cast,status='running',errors=[])
    write_json(out/'rows.json',[failed,pending])
    event=dict(kind='model_result',callId=1,status='error',budgetCounted=True,durationMs=1000,
               response=dict(usage=dict(total_tokens=None,prompt_tokens=None,completion_tokens=None,completion_tokens_details=None)))
    (out/'failed.jsonl').write_text(json.dumps(event)+'\n')
    recompute(tmp_path)
    summary=json.loads((tmp_path/'summary.json').read_text())
    assert summary['groups'][0]['pending']==1
    assert len(summary['groups'][0]['failures'])==1
    assert summary['totals']['unknownTotalUsage']==1 and summary['totals']['unknownCostCalls']==1
    assert summary['totals']['reasoningUnknownCalls']==1
    usage=json.loads((tmp_path/'usage.json').read_text())
    assert usage[0]['usage']['total_tokens'] is None and usage[0]['usdEstimate'] is None
