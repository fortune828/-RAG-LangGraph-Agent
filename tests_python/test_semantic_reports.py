import copy
import json
from pathlib import Path

import pytest

from evaluation.semantic_reports import PRICE_PATH, estimate_cost, export, recompute


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def request(call_id=1, phase='generate', content='合成输入'):
    return dict(kind='model_request', callId=call_id, stage=phase, eventId=call_id * 2 - 1,
        timestamp='2026-10-10T10:00:00+00:00',
        payload=dict(model='deepseek-flash', thinking={'type': 'enabled'}, reasoning_effort='low', max_tokens=64,
                     messages=[dict(role='user', content=content)], response_format={'type': 'json_object'}))


def result(call_id=1, phase='generate', usage=None, status='ok', counted=True):
    return dict(kind='model_result', callId=call_id, stage=phase, eventId=call_id * 2,
                budgetCounted=counted, status=status, durationMs=100, response=dict(usage=usage))


USAGE = dict(total_tokens=30, prompt_tokens=10, completion_tokens=20,
             prompt_cache_hit_tokens=2, prompt_cache_miss_tokens=8,
             completion_tokens_details={'reasoning_tokens': 12})


def cell(base, stage, arm, events, *, status='completed', case_id='case', extra=None):
    directory = base / stage
    row = dict(caseId=case_id, arm=arm, config='test-config', graphVersion='test-graph',
        status=status, durationMs=200, evidence=[], answer=dict(status='answered', paragraphs=[dict(text='正文', citations=[])]),
        auto=dict(calls=999, reportedTokens=999)) | (extra or {})
    save(directory / 'rows.json', [row])
    save(directory / 'inputs.json', [dict(caseId=case_id, question='当前条件', mode='followup',
         session=dict(question='核心问题', turns=[], cast=dict(lines=[7] * 6)))])
    path = directory / f'{case_id}-{arm}.jsonl'
    path.write_text('\n'.join(json.dumps(event | dict(caseId=case_id, variant=arm), ensure_ascii=False) for event in events) + '\n')
    return path


def ledger(base, calls, reported, accounted=None, unknown=0):
    save(base / 'ledger.json', dict(calls=calls, reportedTokens=reported,
                                  accountedTokens=reported if accounted is None else accounted, missingUsageCalls=unknown))


def test_recomputation_counts_failed_usage_corrects_only_known_stage_erratum_and_preserves_scores(tmp_path):
    path = cell(tmp_path, 'context', 'roles', [request(phase='repair'), result(phase='repair', usage=USAGE, status='error')], status='failed')
    raw = path.read_bytes()
    ledger(tmp_path, 1, 30)
    # An archived copy must not be counted a second time.
    archived = tmp_path / 'context/source/copied.jsonl'
    archived.parent.mkdir(parents=True)
    archived.write_bytes(raw)
    original_score = dict(answerId='Q01', scores={'userFacts': None}, comment='无正文，不评语义。')
    scores = dict(reviewer='独立 AI；非人工专家', results=[original_score], summary={'total': 1})
    save(tmp_path / 'blind/context/scores.json', scores)
    save(tmp_path / 'context/review-key.json', [dict(answerId='Q01', caseId='case', arm='roles')])
    totals = recompute(tmp_path)
    group = totals['groups'][0]
    assert group['n'] == group['failed'] == group['calls'] == 1
    assert group['mechanicalCompleted'] == group['repairs'] == 0
    assert group['reportedTokens'] == 30 and group['unknownUsageCalls'] == 0
    assert totals['ledger']['consistent'] and totals['labelAudit']['passed']
    assert totals['cost']['estimatedUSD'] == pytest.approx((2 * .003 + 8 * .15 + 20 * .6) / 1_000_000)
    calls = json.loads((tmp_path / 'usage.json').read_text())['calls']
    assert calls[0]['usage'] == USAGE and calls[0]['rawStage'] == 'repair' and calls[0]['effectiveStage'] == 'generate'
    assert len(totals['errata']) == 1
    assert path.read_bytes() == raw
    revealed = json.loads((tmp_path / 'review-revealed.json').read_text())['results'][0]
    assert revealed['result'] == original_score and revealed['arm'] == 'roles'
    assert json.loads((tmp_path / 'blind/context/scores.json').read_text()) == scores
    assert 'estimatedUSD' in (tmp_path / 'tables.csv').read_text()
    assert recompute(tmp_path) == totals


def test_unknown_usage_and_denied_repair_preserve_null_totals_and_reservation(tmp_path):
    first = request()
    events = [first, result(), request(2, 'repair'), result(2, 'repair', status='budget_stopped', counted=False)]
    cell(tmp_path, 'release', 'candidate', events, status='failed')
    reservation = len(json.dumps(dict(messages=first['payload']['messages'], tools=None), ensure_ascii=False).encode()) + 64 + 1024
    ledger(tmp_path, 1, 0, accounted=reservation, unknown=1)
    totals = recompute(tmp_path)
    assert totals['ledger']['consistent']
    assert totals['usage']['total_tokens'] == dict(reportedSum=0, unknownCalls=1, total=None)
    assert totals['cost']['estimatedUSD'] is None and totals['cost']['unknownCalls'] == 1
    group = totals['groups'][0]
    assert group['calls'] == 1 and group['modelRequests'] == 2 and group['budgetRejectedRequests'] == 1
    assert group['repairs'] == 0 and group['repairRequests'] == 1
    assert group['totalTokens'] is None


def test_tool_statuses_quote_resolutions_label_leaks_and_missing_ledger_are_visible(tmp_path):
    events = [request(content='{"reviewPoints":["独立评测专用要求"]}'), result(usage=USAGE)]
    for index, (status, counted) in enumerate([('rejected', True), ('empty', True), ('ok', True), ('rejected', False)]):
        events.extend([dict(kind='tool_request', request={'id': str(index)}),
                       dict(kind='tool_result', requestId=str(index), executionStatus=status, budgetCounted=counted)])
    events.append(dict(kind='validation', quoteResolutions=[{'sourceQuote': '原文'}]))
    cell(tmp_path, 'final', 'candidate', events, extra={'quoteResolutions': [{'sourceQuote': '原文'}]})
    ledger(tmp_path, 2, 999)
    totals = recompute(tmp_path)
    group = totals['groups'][0]
    assert [group[key] for key in ('toolRequests', 'toolAttempts', 'toolOk', 'toolEmpty', 'toolRejected')] == [4, 3, 1, 1, 2]
    assert group['quoteResolutions'] == 1  # row and validation copies are not double-counted
    assert not totals['labelAudit']['passed'] and totals['labelAudit']['violations'][0]['labelFields'] == ['reviewPoints']
    assert not totals['ledger']['consistent']
    assert totals['ledger']['comparisons']['reportedTokens'] == dict(recorded=999, recomputed=30, match=False)


def test_unresolved_request_is_not_known_zero_cost_or_a_dispatched_call(tmp_path):
    cell(tmp_path, 'continuous', 'candidate', [request()], status='running')
    totals = recompute(tmp_path)
    assert totals['accounting']['calls'] == 0 and totals['unresolvedRequests'] == 1
    assert totals['usage']['total_tokens']['total'] is None
    assert totals['cost']['estimatedUSD'] is None and totals['cost']['unknownCalls'] == 1
    assert totals['groups'][0]['unfinished'] == 1


def test_cost_requires_same_date_and_complete_cache_components():
    prices = json.loads(PRICE_PATH.read_text())
    missing = copy.deepcopy(USAGE)
    del missing['prompt_cache_miss_tokens']
    assert estimate_cost(request(), missing, prices)['usd'] is None
    outside = request() | dict(timestamp='2026-10-11T10:00:00Z')
    assert estimate_cost(outside, USAGE, prices)['usd'] is None
    assert estimate_cost(request() | dict(timestamp='2026-10-10T10:00:00'), USAGE, prices)['usd'] is None


def test_label_audit_detects_unkeyed_reference_prose_but_not_legitimate_evidence_ids(tmp_path):
    label = '这里是只有评测专用标签才记录的一段独立判定，不是用户输入。'
    evidence_id = 'zhu-xi@528287:h29:line4:99879a18e8'
    save(tmp_path / 'fixtures/development.json', {'cases': [dict(caseId='case',
        semanticReference={'review': label, 'evidenceIds': [evidence_id]},
        initialEvidenceAudit={'intent': 'explanation_followup'})]})
    cell(tmp_path, 'final', 'candidate', [request(content=label), result(usage=USAGE)])
    assert recompute(tmp_path)['labelAudit']['violations'][0]['exactLabelSpans'] == [label]
    cell(tmp_path, 'final', 'candidate', [request(content=evidence_id + ' explanation_followup'), result(usage=USAGE)])
    assert recompute(tmp_path)['labelAudit']['passed']


def test_blind_export_omits_arm_and_metrics_preserves_failed_candidate_and_refuses_overwrite(tmp_path):
    cell(tmp_path, 'continuous', 'candidate', [request(), result(usage=USAGE)], status='validation_failed')
    created = export('continuous', base=tmp_path)
    materials = json.loads(Path(created['materials']).read_text())
    keys = json.loads(Path(created['key']).read_text())
    assert materials[0]['status'] == 'validation_failed' and materials[0]['answer']['paragraphs'][0]['text'] == '正文'
    assert set(materials[0]) == {'answerId', 'question', 'coreQuestion', 'history', 'cast', 'answer', 'evidence', 'status'}
    assert keys == [dict(answerId='A01', stage='continuous', caseId='case', arm='candidate')]
    before = Path(created['materials']).read_bytes()
    with pytest.raises(FileExistsError):
        export('continuous', base=tmp_path)
    assert Path(created['materials']).read_bytes() == before
    custom = export('continuous', 'second-reading', base=tmp_path)
    assert Path(custom['key']) == tmp_path / 'second-reading-review-key.json'
