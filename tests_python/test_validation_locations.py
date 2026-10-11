from backend.knowledge import retrieve_exact
from backend.validation import validate_answer


def test_locations_identify_uncited_author_paragraph_without_relaxing_old_errors():
    e=retrieve_exact(dict(hexagramId=1,sections=['line'],lineIndices=[2],layers=['commentary'],authorIds=['wang-bi']))['evidence'][0]
    answer=dict(status='answered',paragraphs=[dict(text='王弼解释见龙在田。',citations=[dict(evidenceId=e['id'],quote=e['text'][:10],claim='王弼解释见龙在田。')]),
        dict(text='王弼主张应当马上行动。',citations=[])])
    issues=[]
    assert validate_answer(answer,[e],issues=issues)==validate_answer(answer,[e])
    assert len(issues)==1
    assert issues[0]['code']=='AUTHOR_ATTRIBUTION_MISMATCH:wang-bi'
    assert issues[0]['path']==['paragraphs',1,'citations']
    assert '本段 citations' in issues[0]['message']


def test_same_error_code_keeps_all_distinct_locations():
    e=retrieve_exact(dict(hexagramId=1,sections=['judgment']))['evidence'][0]
    paragraph=dict(text='解释。',citations=[dict(evidenceId=e['id'],quote='伪造原文',claim='错误定位')])
    issues=[]
    _,errors=validate_answer(dict(status='answered',paragraphs=[paragraph,paragraph]),[e],issues=issues)
    assert len(errors)==2 and len(issues)==4
    assert [issue['path'][1] for issue in issues]==[0,0,1,1]
    assert any(i['path'][-1]=='quote' for i in issues)


def test_schema_error_locations_are_safe_and_do_not_repeat_input_values():
    issues=[]
    answer,errors=validate_answer({'paragraphs':[{'text':'secret-synthetic-marker'}]},[],issues=issues)
    assert answer is None and errors and issues
    assert 'secret-synthetic-marker' not in str(issues)


def test_formal_r03_single_character_quote_failure_is_already_located_for_repair():
    """Do not misreport a model ignoring repair as a missing schema constraint."""
    import json
    from pathlib import Path

    records = [json.loads(line) for line in (Path(__file__).parents[1] /
        'evaluation/reports/formal-20261010/test/test-07-H.jsonl').read_text().splitlines()]
    validations = [record for record in records if record['kind'] == 'validation']
    assert len(validations) == 2
    for record in validations:
        issues = []
        answer, errors = validate_answer(record['candidate'], record['evidence'], issues=issues)
        assert answer is None and any('at least 2 characters' in error for error in errors)
        assert any(issue['path'] == ['paragraphs', 0, 'citations', 0, 'quote'] for issue in issues)
    repair = next(record for record in records if record['kind'] == 'model_request' and record['stage'] == 'repair')
    instructions = json.loads(repair['payload']['messages'][-1]['content'])
    assert instructions['errors'][0]['path'] == ['paragraphs', 0, 'citations', 0, 'quote']
