"""Synthetic event audit: never loads frozen test questions or review scores."""
import json

from evaluation.formal_analysis import evidence_analysis


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def fixture(base, rows, events=None, *, initial=('base',), arms=('B', 'C', 'D', 'H')):
    save(base / 'development/rows.json', rows)
    save(base / 'development/manifest.json', dict(arms=list(arms)))
    for arm, values in (events or {}).items():
        path = base / f'development/case-{arm}.jsonl'
        path.write_text('\n'.join(json.dumps(value, ensure_ascii=False) for value in values) + '\n')
    return dict(cases=[dict(caseId='case', evidenceGroups=[['target']], retrievalRequired=True,
        availability='ingested', initialEvidenceAudit=dict(evidenceIds=list(initial)))])


def row(arm='H', **extra):
    return dict(caseId='case', arm=arm, groupId='group', category='source', status='failed', evidence=[]) | extra


def accepted():
    return dict(kind='tool_result', requestId='tool-1', executionStatus='ok', budgetCounted=True,
        returnedResult=dict(evidence=[dict(id='target', text='真实材料')]))


def counted_generation(ids, *, counted=True, call_id=1, phase='generate'):
    return [dict(kind='model_request', callId=call_id, stage=phase, payload=dict(messages=[dict(role='user',
        content=json.dumps({'参考原文': [dict(id=eid, text='材料') for eid in ids]}, ensure_ascii=False))])),
        dict(kind='model_result', callId=call_id, stage=phase, status='error', budgetCounted=counted)]


def report(base, labels):
    evidence_analysis(base, labels=labels)
    return json.loads((base / 'analysis/evidence.json').read_text())


def test_incomplete_controls_are_unknown_and_observed_counterexample_is_false(tmp_path):
    rows = [row('B', initialEvidenceIds=['base'], scopeIds=[1], sameCast=True), row('C')]
    labels = fixture(tmp_path, rows)
    controls = report(tmp_path, labels)['controls'][0]
    assert controls['observedArms'] == 1
    assert controls['identicalInitialEvidence'] is None
    assert controls['identicalScope'] is None and controls['castUnchanged'] is None
    assert controls['missingInitialArms'] == ['C', 'D', 'H']
    rows[1].update(initialEvidenceIds=['different'], scopeIds=[2], sameCast=False)
    labels = fixture(tmp_path, rows)
    controls = report(tmp_path, labels)['controls'][0]
    assert controls['identicalInitialEvidence'] is controls['identicalScope'] is controls['castUnchanged'] is False


def test_all_four_observed_initial_scope_and_cast_checks_pass(tmp_path):
    rows = [row(arm, status='completed', initialEvidenceIds=['base'], scopeIds=None, sameCast=True)
            for arm in ('B', 'C', 'D', 'H')]
    labels = fixture(tmp_path, rows)
    controls = report(tmp_path, labels)['controls'][0]
    assert controls['observedArms'] == 4
    assert controls['identicalInitialEvidence'] and controls['identicalScope'] and controls['castUnchanged']


def test_generation_failure_preserves_positive_acquisition_without_inventing_final_state(tmp_path):
    events = [dict(kind='prepare', evidence=[dict(id='base')], scopeIds=[1]),
              dict(kind='tool_request', request={'id':'tool-1'}), accepted()]
    events += counted_generation(['base', 'target'])
    labels = fixture(tmp_path, [row()], {'H': events})
    data = report(tmp_path, labels)
    case, group = data['cases'][0], data['groups'][0]
    assert case['initialAuditMatches'] is True
    assert case['gapResolved'] is True and case['gapResolvedForGeneration'] is True
    assert case['finalEvidenceKnown'] is False and case['allRequiredEvidence'] is None
    assert case['finalCovered'] is None and case['newEvidenceIds'] is None
    assert case['targetContributingCalls'] == 1
    assert case['generationEvidenceSource'] == 'last_counted_generation_request'
    assert group['gapResolution']['rate'] == group['generationGapResolution']['rate'] == 1
    assert group['finalGapResolution']['n'] == 0 and group['finalGapResolution']['unknown'] == 1


def test_absent_capture_on_failure_is_unknown_and_not_false_retrieval_failure(tmp_path):
    labels = fixture(tmp_path, [row(initialEvidenceIds=['base'])])
    data = report(tmp_path, labels)
    case, group = data['cases'][0], data['groups'][0]
    assert case['gapInitiallyPresent'] is True
    assert case['gapResolved'] is None and case['gapResolvedForGeneration'] is None
    assert group['gapResolution']['rate'] is None and group['gapResolution']['unknown'] == 1
    assert group['gapResolution']['plannedN'] == 1


def test_acquisition_and_actual_generation_visibility_have_separate_denominators(tmp_path):
    events = [accepted(), dict(kind='validation', evidence=[])]
    labels = fixture(tmp_path, [row(status='completed', initialEvidenceIds=['base'],
        evidence=[dict(id='base'), dict(id='target')])], {'H': events})
    data = report(tmp_path, labels)
    case, group = data['cases'][0], data['groups'][0]
    assert case['gapResolved'] is True and case['finalGapResolved'] is True
    assert case['gapResolvedForGeneration'] is False
    assert group['retrievedButNotVisible'] == ['case']
    assert group['generationGapResolution']['n'] == 1 and group['generationGapResolution']['rate'] == 0


def test_request_counts_budget_attempts_and_case_counts_are_not_confused(tmp_path):
    events = []
    for index in range(3):
        events += [dict(kind='tool_request', request={'id':str(index)}),
                   dict(kind='tool_result', executionStatus='rejected', budgetCounted=True)]
    events += counted_generation([], counted=False, phase='route')
    events += counted_generation([], counted=True, call_id=2, phase='route')
    events += [dict(kind='model_request', callId=3, stage='route')]
    labels = fixture(tmp_path, [row(status='completed', initialEvidenceIds=['target'], toolAttempts=999)],
        {'H': events}, initial=('target',))
    data = report(tmp_path, labels)
    case, group = data['cases'][0], data['groups'][0]
    assert case['routeModelRequests'] == 3 and case['routeModelCalls'] == 1 and case['routeDispatchUnknown'] == 1
    assert group['casesWithCallsWhenSufficient'] == 1
    assert group['toolAttemptsWhenSufficient'] == group['toolRequestsWhenSufficient'] == 3
    assert 'callsWhenSufficient' not in group


def test_frozen_labels_are_used_for_reproduction_without_working_tree_labels(tmp_path):
    labels = fixture(tmp_path, [row(initialEvidenceIds=['base'])])
    frozen = tmp_path / 'source/evaluation/formal/labels.json'
    save(frozen, labels)
    evidence_analysis(tmp_path)
    data = json.loads((tmp_path / 'analysis/evidence.json').read_text())
    assert data['labelSource'] == str(frozen)


def test_reference_groups_require_every_object_but_allow_alternative_editions():
    from evaluation.formal_analysis import coverage
    assert coverage([['a', 'b'], ['c']], ['b', 'x']) == [True, False]
    assert coverage([['a', 'b'], ['c']], ['a', 'c']) == [True, True]
    assert coverage([], ['a']) == []


def test_score_distribution_exports_numeric_categories_to_csv(tmp_path):
    from evaluation.formal_stats import write_csv
    import csv
    path = tmp_path/'scores.csv'
    write_csv(path, [{'distribution': {0: 2, 1: 3, 2: 4}}])
    with path.open() as stream:
        assert next(csv.DictReader(stream)) == {'distribution.0': '2', 'distribution.1': '3', 'distribution.2': '4'}
