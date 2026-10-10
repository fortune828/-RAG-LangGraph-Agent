"""Experiments remain separate from scores and preserve old retrieval coverage."""
import json
import pytest
from evaluation.natural_dialogue import inputs, CASES
from evaluation.paired import load_cases
from evaluation.scoring import make_review_pack


def test_core_comparison_uses_saved_contexts_without_retrieval_quiz_cases():
    cases = inputs()
    assert {c['caseId'] for c in cases} == set(CASES)
    assert not {'fuzzy-author', 'source-two-lines'} & set(CASES)
    assert {'fuzzy-author', 'source-two-lines'} <= {c['caseId'] for c in load_cases()['cases']}
    for case in cases:
        context = json.loads(case['messages'][1]['content'])
        assert context['参考原文'] and context['卦象']
        assert '近期对话' in context and context['当前问题']


def test_paused_review_cannot_be_overwritten(tmp_path):
    review = tmp_path / 'review'; review.mkdir()
    (review / 'PAUSED.md').write_text('paused')
    (review / 'scores.csv').write_text('original scores')
    with pytest.raises(ValueError, match='REVIEW_PAUSED'):
        make_review_pack(tmp_path)
    assert (review / 'scores.csv').read_text() == 'original scores'
