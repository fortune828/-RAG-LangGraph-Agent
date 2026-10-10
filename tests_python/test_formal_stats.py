import copy
import csv
import json
from pathlib import Path

import pytest

from evaluation.formal_stats import (
    DIMENSIONS, export_human, percentile, recompute, wilson,
)
from evaluation.semantic_reports import PRICE_PATH

USAGE = dict(total_tokens=30, prompt_tokens=10, completion_tokens=20,
             prompt_cache_hit_tokens=2, prompt_cache_miss_tokens=8,
             completion_tokens_details={'reasoning_tokens': 12})
EVIDENCE = dict(id='source-1', text='君子以自強不息。', authorNames=[], workTitle='周易', layer='classic')
ANSWER = dict(status='answered', paragraphs=[dict(text='可以从持续用功来看待这个问题。', citations=[
    dict(evidenceId=EVIDENCE['id'], quote='君子以自強不息', claim='持续用功')])], limitations=[])


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def request(call_id=1, stage='generate', date='2026-10-10'):
    return dict(kind='model_request', callId=call_id, stage=stage,
        timestamp=date + 'T10:00:00+00:00', payload=dict(model='deepseek-flash',
            thinking={'type': 'enabled'}, reasoning_effort='low', max_tokens=8192,
            messages=[dict(role='user', content='合成问题')]))


def result(call_id=1, stage='generate', value=ANSWER, usage=USAGE, *, counted=True, status='ok'):
    return dict(kind='model_result', callId=call_id, stage=stage, budgetCounted=counted,
        status=status, durationMs=100, response=dict(usage=usage,
            message=dict(content=json.dumps(value, ensure_ascii=False))))


def validation(value=ANSWER, errors=None, repair=0):
    return dict(kind='validation', candidate=value, evidence=[EVIDENCE], errors=errors or [],
        answer=value if not errors else None, repairNumber=repair)


def cell(base, *, stage='test', arm='H', case_id='case', events=None, status='completed', extra=None, scenario='工作'):
    directory = base / stage
    row = dict(caseId=case_id, arm=arm, split=stage, groupId='group-' + case_id,
        config={'model': 'deepseek-flash', 'thinking': 'enabled', 'reasoning_effort': 'low', 'max_tokens': 8192},
        graphVersion='python-graph-v16', promptVersion='evidence-agent-python-v16', status=status,
        durationMs=200, answer=ANSWER if status == 'completed' else None, evidence=[EVIDENCE],
        finalGenerationEvidence=[EVIDENCE], finalCandidate=ANSWER) | (extra or {})
    path = directory / 'rows.json'
    rows = json.loads(path.read_text()) if path.exists() else []
    rows.append(row)
    save(path, rows)
    inputs_path = directory / 'inputs.json'
    inputs = json.loads(inputs_path.read_text()) if inputs_path.exists() else {'cases': []}
    if not any(c['caseId'] == case_id for c in inputs['cases']):
        inputs['cases'].append(dict(caseId=case_id, split=stage, scenario=scenario, question='当下怎么办 <script>？',
            session=dict(question='最初的问题', cast={'lines': [7]*6}, turns=[dict(question='历史问题',
                answer=ANSWER, evidence=[EVIDENCE], trace=[{'model': 'SHOULD_NOT_LEAK'}])]), mode='followup'))
    save(inputs_path, inputs)
    if events is not None:
        trace = directory / f'{case_id}-{arm}.jsonl'
        trace.write_text('\n'.join(json.dumps(event | dict(caseId=case_id, variant=arm, eventId=i+1), ensure_ascii=False)
            for i, event in enumerate(events)) + '\n')
        return trace


def test_schema_repair_and_quote_matching_separate_from_semantics(tmp_path):
    invalid = copy.deepcopy(ANSWER)
    invalid['paragraphs'][0]['citations'][0]['quote'] = '君子以自强不息'  # unique source restoration
    first_events = [request(), result(value=invalid), validation(invalid, ['AUTHOR_ATTRIBUTION_MISMATCH:example']),
                    request(2, 'repair'), result(2, 'repair'), validation(repair=1)]
    path = cell(tmp_path, events=first_events)
    original = path.read_bytes()
    result_data = recompute(tmp_path)
    group = result_data['groups'][0]
    assert group['mechanicalComplete']['rate'] == 1
    assert group['firstSchema']['rate'] == 1  # validity doesn't bless attribution/meaning
    assert group['repair']['triggered'] == group['repair']['successful'] == 1
    assert group['firstQuotes']['resolved'] == 1 and group['finalQuotes']['direct'] == 1
    assert group['usage']['reasoning_tokens']['total'] == 24
    assert result_data['accounting']['calls'] == 2 and result_data['accounting']['reportedTokens'] == 60
    assert result_data['cost']['estimatedUSD'] == pytest.approx(2 * (2*.003 + 8*.15 + 20*.6)/1_000_000)
    assert path.read_bytes() == original
    assert recompute(tmp_path) == result_data


def test_length_failure_parseable_schema_is_not_deliverable_and_usage_preserved(tmp_path):
    events = [request(), result(status='error'), validation(errors=['MODEL_INCOMPLETE_RESPONSE']),
              request(2, 'repair'), result(2, 'repair', usage=None, status='error'),
              validation(errors=['MODEL_INCOMPLETE_RESPONSE'], repair=1)]
    cell(tmp_path, events=events, status='validation_failed')
    report = recompute(tmp_path)
    group = report['groups'][0]
    assert group['firstSchema']['rate'] == 1 and group['firstDeliverableSchema']['rate'] == 0
    assert group['mechanicalComplete']['rate'] == group['repair']['success']['rate'] == 0
    assert group['usage']['total_tokens'] == dict(reportedSum=30, unknownCalls=1, total=None)
    assert report['cost']['estimatedUSD'] is None
    assert report['cost']['reportedEstimateUSD'] > 0
    assert report['accounting']['calls'] == 2


def test_pending_and_unknown_dispatch_are_never_known_zero_success_or_cost(tmp_path):
    cell(tmp_path, case_id='pending', status='pending', extra={'durationMs': None, 'finalCandidate': None})
    cell(tmp_path, case_id='inflight', events=[request()], status='running', extra={'durationMs': None})
    report = recompute(tmp_path)
    group = report['groups'][0]
    assert group['n'] == 2 and group['mechanicalComplete']['n'] == 2
    assert group['mechanicalAmongTerminal']['n'] == 0 and group['firstSchema']['n'] == 0
    assert group['p95Ms'] is None and report['accounting']['unknownDispatch'] == 1
    assert report['accounting']['usage']['total_tokens']['total'] is None
    assert report['cost']['estimatedUSD'] is None
    assert not report['integrityIssues']


def test_tool_requests_attempts_and_rejections_remain_distinct(tmp_path):
    events = [request(), result(), validation()]
    for i, (status, counted) in enumerate([('ok', True), ('empty', True), ('rejected', True), ('rejected', False)]):
        events += [dict(kind='tool_request', request={'id': str(i)}), dict(kind='tool_result',
            requestId=str(i), executionStatus=status, budgetCounted=counted)]
    events += [request(2, 'repair'), result(2, 'repair', usage=None, counted=False, status='budget_stopped')]
    cell(tmp_path, events=events, status='failed')
    group = recompute(tmp_path)['groups'][0]
    assert [group[k] for k in ('toolRequests', 'toolAttempts', 'toolOk', 'toolEmpty', 'toolRejected')] == [4, 3, 1, 1, 2]
    assert group['calls'] == 1 and group['modelRequests'] == 2
    assert group['repair']['triggered'] == 0 and group['repairRequests'] == 1


def test_date_validation_and_missing_reasoning_do_not_invent_usage(tmp_path):
    usage = {k:v for k,v in USAGE.items() if k != 'completion_tokens_details'}
    cell(tmp_path, events=[request(date='2026-10-11'), result(usage=usage), validation()])
    report = recompute(tmp_path)
    assert report['cost']['estimatedUSD'] is None
    assert report['accounting']['usage']['reasoning_tokens'] == dict(reportedSum=0, unknownCalls=1, total=None)
    calls = json.loads((tmp_path / 'analysis/usage.json').read_text())['calls']
    assert calls[0]['estimate']['reason'] == 'frozen_weekend_price_not_applicable'


def test_pairing_uses_same_inputs_retains_missing_failures_and_separates_splits(tmp_path):
    events = [request(), result(), validation()]
    cell(tmp_path, arm='B', events=events, status='failed', extra={'durationMs': 100})
    cell(tmp_path, arm='H', events=events, extra={'durationMs': 300})
    cell(tmp_path, arm='B', case_id='missing', status='pending', extra={'durationMs': None})
    cell(tmp_path, arm='B', case_id='different', events=events, extra={'inputHash': 'left'})
    cell(tmp_path, arm='H', case_id='different', events=events, extra={'inputHash': 'right'})
    cell(tmp_path, stage='development', arm='H', events=events)
    cell(tmp_path, stage='continuous', arm='B', events=events)
    cell(tmp_path, stage='continuous', arm='H', events=events)
    report = recompute(tmp_path)
    pair = report['pairs'][0]
    assert pair['n'] == pair['rightWins'] == 1 and pair['leftWins'] == 0
    assert pair['mechanicalRateDifference'] == 1
    assert pair['differences']['durationMs']['mean'] == 200
    assert pair['incompatibleInputs'] == ['different'] and pair['missingRight'] == ['missing']
    assert {g['split'] for g in report['groups']} == {'development', 'test', 'continuous'}
    assert all(p['stage'] != 'continuous' for p in report['pairs'])
    assert 'mechanicalComplete' not in report  # no pooled success rate
    assert wilson(0, 0)['rate'] is None
    assert wilson(1, 2)['low'] < .5 < wilson(1, 2)['high']
    assert percentile([100, 200, 300, 400], .95) == 385


def test_missing_trace_completed_row_surfaces_integrity_failure(tmp_path):
    cell(tmp_path, status='completed')
    report = recompute(tmp_path)
    assert report['integrityIssues']
    assert report['groups'][0]['mechanicalComplete']['rate'] == 0


def test_anonymous_full_export_separates_key_escapes_html_and_never_overwrites(tmp_path):
    events = [request(), result(), validation()]
    cell(tmp_path, arm='H', events=events, scenario='乙')
    cell(tmp_path, arm='B', case_id='second', events=events, scenario='甲', status='failed')
    created = export_human('test', base=tmp_path)
    materials = json.loads(Path(created['materials']).read_text())
    keys = json.loads(Path(created['key']).read_text())
    assert [m['scenario'] for m in materials] == sorted(['甲', '乙'])
    assert Path(created['key']).parent == tmp_path / 'analysis'
    raw = Path(created['materials']).read_text()
    assert 'deepseek' not in raw and 'SHOULD_NOT_LEAK' not in raw and '"arm"' not in raw
    assert all(m['history'][0]['answer'] == ANSWER for m in materials)
    assert all(m['evidence'][0]['text'] == EVIDENCE['text'] for m in materials)
    failed = next(m for m in materials if m['status'] == 'failed')
    assert failed['answer'] is None and failed['candidate'] == ANSWER
    page = Path(created['html']).read_text()
    assert '<script>' not in page and '&lt;script&gt;' in page
    with Path(created['scores']).open(newline='') as stream:
        scores = list(csv.DictReader(stream))
    assert {row['answerId'] for row in scores} == {row['answerId'] for row in keys}
    assert all(row[field] == '' for row in scores for field in DIMENSIONS)
    with pytest.raises(FileExistsError):
        export_human('test', base=tmp_path)
    assert Path(created['materials']).read_text() == raw


def test_reviews_keep_ai_human_and_unknown_scores_separate_without_overwriting(tmp_path):
    events = [request(), result(), validation()]
    cell(tmp_path, events=events)
    created = export_human('test', base=tmp_path)
    key = json.loads(Path(created['key']).read_text())[0]
    ai = dict(reviewerType='ai', reviewer='independent-evaluator', results=[dict(answerId=key['answerId'],
        scores={'understanding': 2, 'customKnowledgeDimension': None}, reason='辅助评分')])
    human = dict(reviewerType='human', reviewer='reader', results=[dict(answerId=key['answerId'],
        scores={'understanding': 1}, reason='人工意见')])
    save(tmp_path / 'ai.json', ai)
    save(tmp_path / 'human.json', human)
    specs = [dict(scores=name, key='analysis/test-key.json') for name in ('ai.json', 'human.json')]
    recompute(tmp_path, review_paths=specs)
    revealed = json.loads((tmp_path / 'analysis/reviews.json').read_text())
    assert {r['reviewerType'] for r in revealed['results']} == {'ai', 'human'}
    assert len(revealed['summaries']) == 3
    assert next(r for r in revealed['summaries'] if r['dimension'] == 'customKnowledgeDimension')['mean'] is None
    assert json.loads((tmp_path / 'ai.json').read_text()) == ai
    assert 'composite' not in revealed


def test_cluster_bootstrap_keeps_related_turns_together_and_preserves_signed_differences():
    from evaluation.formal_stats import cluster_bootstrap
    observations = [('same-conversation', 1), ('same-conversation', 1), ('same-conversation', 1), ('other', -1)]
    summary = cluster_bootstrap(observations)
    assert summary['n'] == 4 and summary['groups'] == 2 and summary['estimate'] == .5
    assert summary['low'] == -1 and summary['high'] == 1
    assert summary['seed'] == 1729 and summary['iterations'] == 2000
    assert summary == cluster_bootstrap(list(reversed(observations)))
    assert cluster_bootstrap([('one', -1), ('one', -1)])['low'] is None


def test_formal_review_schema_targets_metadata_unknown_tags_and_cluster_pairs(tmp_path):
    events = [request(), result(), validation()]
    for case_id in ('case-1', 'case-2'):
        for arm in ('B', 'H'):
            cell(tmp_path, arm=arm, case_id=case_id, events=events)
    created = export_human('test', base=tmp_path)
    keys = json.loads(Path(created['key']).read_text())
    reviewer = dict(reviewerId='blind-reader', kind='AI', expertStatus='non_expert', blind=True, independent=True)
    rows = []
    for key in keys:
        rows.append(dict(answerId=key['answerId'], reviewer=reviewer, taskSuccess=True, evaluations=[
            dict(target='delivered', dimensions={'understanding': dict(value=2 if key['arm'] == 'H' else 1)},
                 errors=[dict(type='new_noncatalogued_error', reason='Preserved rather than silently discarded')]),
            dict(target='candidate', dimensions={'understanding': dict(value=None, naReason='not_applicable')})]))
    save(tmp_path / 'scored.json', rows)
    specs = [dict(scores='scored.json', key='analysis/test-key.json')]
    recompute(tmp_path, review_paths=specs)
    reviews = json.loads((tmp_path / 'analysis/reviews.json').read_text())
    assert len(reviews['results']) == 8
    assert all(row['reviewerType'] == 'ai' and row['reviewer'] == reviewer for row in reviews['results'])
    assert reviews['results'][0]['original']['evaluations'][0]['errors'][0]['type'] == 'new_noncatalogued_error'
    paired = next(p for p in reviews['pairs'] if p['target'] == 'delivered')
    assert paired['n'] == 2 and paired['clusterCI']['groups'] == 2
    assert paired['clusterCI']['estimate'] == paired['clusterCI']['low'] == paired['clusterCI']['high'] == 1
    assert next(p for p in reviews['pairs'] if p['target'] == 'candidate')['n'] == 0
    with pytest.raises(ValueError, match='DUPLICATE_REVIEW'):
        recompute(tmp_path, review_paths=specs + specs)


def test_blank_human_placeholders_are_not_human_reviews_and_source_rubric_is_used(tmp_path):
    cell(tmp_path, events=[request(), result(), validation()])
    created = export_human('test', base=tmp_path)
    specs = [dict(scores=created['scores'], key=created['key'], reviewerType='human')]
    recompute(tmp_path, review_paths=specs)
    reviews = json.loads((tmp_path / 'analysis/reviews.json').read_text())
    assert reviews['results'] == reviews['summaries'] == []
    rubric = json.loads((Path(created['materials']).parent / 'rubric.json').read_text())
    assert 'unhelpful_expansion' in rubric['errors']['types']
    assert 'overlong' not in rubric['errors']['types']


def test_actual_continuous_row_input_replaces_static_history_in_blind_material(tmp_path):
    actual = dict(caseId='case', split='continuous', category='更正', question='本轮实际问题', mode='followup',
        session=dict(question='原始问题', cast={'lines': [7]*6, 'id': 'leaky-H-cast-id'},
            turns=[dict(question='实际上一轮用户', answer=ANSWER)]))
    cell(tmp_path, stage='continuous', events=[request(), result(), validation()], extra={'input': actual})
    created = export_human('continuous', base=tmp_path)
    material = json.loads(Path(created['materials']).read_text())[0]
    assert material['question'] == '本轮实际问题'
    assert material['history'][0]['question'] == '实际上一轮用户'
    assert material['cast'] == {'lines': [7]*6}


def test_blind_batches_preserve_actual_reference_scope_delivery_and_claim_coverage(tmp_path):
    from evaluation.formal_stats import export_blind, export_secondary
    for i in range(8):
        cell(tmp_path, case_id=f'case-{i}', events=[request(), result(), validation()],
             status='validation_failed' if i == 0 else 'completed')
    references = {f'case-{i}': [EVIDENCE, dict(id='reference-only', text='求小得。', workTitle='周易',
        semanticReference='DO_NOT_EXPORT_LABEL', expectedAnswer='DO_NOT_EXPORT_LABEL')] for i in range(8)}
    created = export_blind('test', base=tmp_path, references=references)
    assert created['n'] == 8 and len(created['packages']) == 1
    path = Path(created['packages'][0]['materials'])
    materials = json.loads(path.read_text())
    assert all(m['referenceOnlyEvidence'] == [dict(id='reference-only', text='求小得。', workTitle='周易',
        referenceOnly=True, availableToAnswer=False)] for m in materials)
    assert all(m['evidence'] == [EVIDENCE] for m in materials)
    failed = next(m for m in materials if m['status'] == 'validation_failed')
    assert failed['answer'] is None and failed['candidate'] == ANSWER
    assert failed['delivery']['candidateOnly'] is True
    assert failed['citationClaims'][0]['target'] == 'candidate'
    assert all(m['citationClaims'][0]['claim'] == ANSWER['paragraphs'][0]['citations'][0]['claim'] for m in materials)
    assert '"arm"' not in path.read_text() and 'deepseek' not in path.read_text() and 'DO_NOT_EXPORT_LABEL' not in path.read_text()
    assert Path(created['key']).parent == tmp_path / 'analysis'
    secondary = export_secondary('test', [f'case-{i}' for i in range(8)], base=tmp_path, references=references)
    second = json.loads(Path(secondary['packages'][0]['materials']).read_text())
    assert not ({m['answerId'] for m in materials} & {m['answerId'] for m in second})
    with pytest.raises(FileExistsError):
        export_blind('test', base=tmp_path)


def test_batch_partitions_are_balanced_and_do_not_export_unfinished_rows(tmp_path):
    from evaluation.formal_stats import batch_sizes, export_blind
    for n in (16, 36, 40, 160, 248):
        sizes = batch_sizes(n)
        assert sum(sizes) == n and all(8 <= value <= 12 for value in sizes)
        assert max(sizes) - min(sizes) <= 1
    assert batch_sizes(4, allow_small=True) == [4]
    with pytest.raises(ValueError):
        batch_sizes(13)
    cell(tmp_path, stage='smoke', status='running')
    with pytest.raises(ValueError, match='NOT_FINISHED'):
        export_blind('smoke', base=tmp_path)


def test_internal_fallback_is_not_misrepresented_as_api_delivery():
    from evaluation.formal_stats import review_material
    case = dict(question='问题', session=dict(question='问题', turns=[], cast={'lines':[7]*6}))
    fallback = dict(status='insufficient', paragraphs=[dict(text='暂时未完成。', citations=[])], limitations=[])
    material = review_material(dict(status='validation_failed', answer=fallback, finalCandidate=ANSWER,
        finalGenerationEvidence=[EVIDENCE]), case, 'anonymous')
    assert material['answer'] is None and material['internalFallback'] == fallback
    assert material['candidate'] == ANSWER and material['delivery']['visibleKind'] == 'none'
    visible = review_material(dict(status='failed', visibleAnswer=fallback, visibleKind='verification_failure_notice'), case, 'visible')
    assert visible['answer'] == fallback and visible['delivery']['visibleKind'] == 'verification_failure_notice'
