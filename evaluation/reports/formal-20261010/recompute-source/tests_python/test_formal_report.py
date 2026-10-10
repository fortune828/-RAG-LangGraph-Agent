import json
from evaluation.formal_report import analyze_reviews
from evaluation.formal_stats import DIMENSIONS


def test_unknown_tasks_and_reference_only_claims_do_not_become_failure_or_faithfulness(tmp_path):
    def write(name, value):
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))
    write('source/evaluation/formal/data.json', {'cases': [{'caseId': 'x', 'groupId': 'g', 'category': 'life'}]})
    write('review-manifest.json', {'reviews': [{'scores': 'a.json', 'role': 'primary'}]})
    write('analysis/cells.json', [])
    rs=[]
    for arm, task in [('B', 'unknown'), ('C', True)]:
        rs.append(dict(target='delivered', stage='test', caseId='x', arm=arm, groupId='g', source=str(tmp_path/'a.json'),
            reviewer='test-ai', mechanicalComplete=True, scores={d:2 for d in DIMENSIONS},
            original=dict(taskSuccess=task, evaluations=[dict(target='delivered',
                errors=[dict(type='wrong_classical_meaning',certainty='suspected')],
                faithfulness={'claims':[dict(layer='classical_interpretation',supportLabel='supported',sourceIds=['ref-only'])]})])))
    write('analysis/reviews.json', {'results': rs})
    write('test/rows.json', [dict(caseId='x',arm=a,finalGenerationEvidence=[]) for a in ('B','C')])
    result=analyze_reviews(tmp_path)
    pair=next(p for p in result['pairs'] if p['dimension']=='taskSuccess')
    assert pair['clusterCI']['n']==0 and pair['unknownOrNAScores']==1
    b=next(g for g in result['groups'] if g['arm']=='B')
    assert b['unknownTask']==1 and b['taskSuccessKnown']['n']==0
    assert b['errorAnswerCountsByCertainty']=={'suspected':{'wrong_classical_meaning':1}}
    assert b['claimLabelsByScopeAndLayer']=={'reference_only/classical_interpretation':{'supported':1}}


def test_rag_development_and_independent_scores_are_not_pooled(tmp_path):
    def write(name, value):
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))
    cases=[dict(caseId=split,groupId=split,category='knowledge',split=split) for split in ('development','independent_test')]
    write('source/evaluation/formal/data.json',{'cases':cases})
    write('review-manifest.json',{'reviews':[dict(scores='a.json',role='primary')]})
    write('analysis/cells.json',[])
    reviews=[]
    for case in cases:
        for arm in ('classic','full'):
            reviews.append(dict(target='delivered',stage='rag',caseId=case['caseId'],arm=arm,source=str(tmp_path/'a.json'),
                reviewer='test-ai',mechanicalComplete=True,scores={d:2 for d in DIMENSIONS},
                original=dict(taskSuccess=True,evaluations=[dict(target='delivered',errors=[],faithfulness={})])))
    write('analysis/reviews.json',{'results':reviews})
    result=analyze_reviews(tmp_path)
    assert len(result['groups'])==4
    assert all(group['n']==1 for group in result['groups'])
    assert {group['split'] for group in result['groups']}=={'development','independent_test'}
    assert all(pair['pairedPlanned']==1 for pair in result['pairs'])
