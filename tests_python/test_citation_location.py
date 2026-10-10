import copy

import pytest

from backend.citations import locate_original_quote
from backend.validation import validate_answer


def test_observed_script_difference_returns_exact_source_and_offsets():
    source = '以陽居隂久非其位故為此象占者田无所獲而凡事亦不得其所求也'
    found = locate_original_quote(source, '占者田无所获而凡事亦不得其所求也')
    assert found is not None
    assert found['quote'] == '占者田无所獲而凡事亦不得其所求也'
    assert source[found['start']:found['end']] == found['quote']


def test_punctuation_and_whitespace_return_original_internal_characters():
    source = '九二“悔亡”，\n能久中也。'
    found = locate_original_quote(source, '九二「悔亡」能久中也')
    assert found == dict(quote='九二“悔亡”，\n能久中也', start=0, end=len(source) - 1)


@pytest.mark.parametrize('source,quote', [
    ('後悔；后悔。', '后悔'),  # two original locations collapse under the same map
    ('甲，乙。甲乙。', '甲乙'),  # punctuation cannot distinguish locations
    ('恒恒恒', '恒恒'),  # overlapping matches are also ambiguous
    ('占者田无所獲', '占者田无所獲而吉'),  # invented additional words
    ('占者田无所獲', '占者有所獲'),  # changed negation
    ('凡事亦不得其所求也', '凡事不得其所求也'),  # omitted internal word
    ('田无禽', '狩猎没有收获'),  # semantic paraphrase is not literal evidence
    ('九二悔亡', '九三悔亡'),  # changed line number
    ('九二悔亡', '悔亡九二'),  # changed order
    ('甲+乙', '甲乙'),  # symbols are meaningful, unlike punctuation
    ('第２爻', '第2爻'),  # no additional compatibility/numeric conversion
    ('吉。', '吉'),  # too short after normalization
    ('吉。', '，。\n'),
])
def test_ambiguous_or_nonliteral_quotes_are_rejected(source, quote):
    assert locate_original_quote(source, quote) is None


def fixture(source='以陽居隂久非其位故為此象占者田无所獲而凡事亦不得其所求也', quote='占者田无所获而凡事亦不得其所求也'):
    evidence = dict(id='example', text=source, authorIds=['zhu-xi'], workId='zhouyi-benyi',
        hexagramId=32, section='line', line=4, layer='commentary')
    answer = dict(status='answered', paragraphs=[dict(text='朱熹说“' + quote + '”。',
        citations=[dict(evidenceId='example', quote=quote, claim='朱熹说“' + quote + '”')])])
    return answer, [evidence]


def test_validation_restores_source_quote_without_changing_input_or_claim():
    value, evidence = fixture()
    original, original_evidence = copy.deepcopy(value), copy.deepcopy(evidence)
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert not errors and answer is not None
    assert value == original and evidence == original_evidence
    citation = answer['paragraphs'][0]['citations'][0]
    assert citation['quote'] == '占者田无所獲而凡事亦不得其所求也'
    assert citation['claim'] == original['paragraphs'][0]['citations'][0]['claim']
    assert resolutions == [dict(method='unique_source_span', path=['paragraphs', 0, 'citations', 0, 'quote'],
        evidenceId='example', requestedQuote=original['paragraphs'][0]['citations'][0]['quote'],
        sourceQuote=citation['quote'], start=12, end=28)]
    # The original positional/default call contract remains valid.
    assert validate_answer(value, evidence) == (answer, errors)


@pytest.mark.parametrize('source,quote', [
    ('以陽居隂久非其位故為此象占者田无所獲而凡事亦不得其所求也', '占者田无所获而凡事亦不得其所求也大吉'),
    ('以陽居隂久非其位故為此象占者田无所獲而凡事亦不得其所求也', '占者田有所获而凡事亦不得其所求也'),
    ('後悔，後悔。', '后悔'),
    ('有所獲', '一定会成功'),
])
def test_validation_keeps_fabricated_and_ambiguous_quotes_invalid(source, quote):
    value, evidence = fixture(source, quote)
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert answer is None and 'QUOTE_MISMATCH:example' in errors and resolutions == []


def test_source_resolution_does_not_bypass_claim_or_author_checks():
    value, evidence = fixture()
    value['paragraphs'][0]['text'] = '王弼认为该处另有含义。'
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert answer is None and resolutions
    assert 'CLAIM_NOT_IN_PARAGRAPH:example' in errors
    assert 'AUTHOR_ATTRIBUTION_MISMATCH:wang-bi' in errors


def test_unknown_id_is_never_resolved_using_another_record():
    value, evidence = fixture()
    value['paragraphs'][0]['citations'][0]['evidenceId'] = 'unknown'
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert answer is None and 'UNKNOWN_EVIDENCE:unknown' in errors and resolutions == []


def test_resolved_span_must_still_fit_quote_schema():
    value, evidence = fixture('獲' + '，' * 600 + '利', '获利')
    assert locate_original_quote(evidence[0]['text'], '获利') is not None
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert answer is None and 'QUOTE_MISMATCH:example' in errors and resolutions == []


def test_existing_literal_match_does_not_need_or_record_resolution():
    value, evidence = fixture('悔亡，悔亡。', '悔亡')
    resolutions = []
    answer, errors = validate_answer(value, evidence, quote_resolutions=resolutions)
    assert answer is not None and not errors and resolutions == []
