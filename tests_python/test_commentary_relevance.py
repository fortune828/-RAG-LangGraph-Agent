"""Commentary preparation follows cast/target anchors, not generic question words."""
import copy

import pytest

from backend.domain import calculate_hexagram
from backend.knowledge import CURRENT_RELEASE, initial_evidence, load_release


def comments(evidence):
    return [e for e in evidence if e['layer'] == 'commentary']


@pytest.mark.parametrize('question', [
    '我不能再把责任推给搭档，怎么说明我的数据给晚了？',
    '这件事可以怎么处理，才能让彼此顺利一点？',
    '本来想坚持做下去，现在感觉并不适合，我该怎么办？',
])
def test_static_cast_does_not_select_arbitrary_lines_from_modern_vocabulary(question):
    cast = calculate_hexagram([8, 7, 7, 7, 8, 8])
    evidence = initial_evidence(cast, question)
    assert not comments(evidence)
    assert {e['id'] for e in evidence} == {'zy-32-judgment', 'zy-32-tuan', 'zy-32-xiang'}


def test_moving_line_keeps_classics_and_three_author_limit():
    cast = calculate_hexagram([7, 9, 7, 7, 7, 7])
    evidence = initial_evidence(cast)
    selected = comments(evidence)
    assert len(selected) == 3
    assert {a for e in selected for a in e['authorIds']} == {'wang-bi', 'cheng-yi', 'zhu-xi'}
    assert all(e['hexagramId'] == 1 and e['line'] == 2 for e in selected)
    assert {'zy-1-line-2', 'zy-1-line_xiang-2', 'zy-13-judgment', 'zy-13-xiang'} <= {
        e['id'] for e in evidence}
    assert sum(len(e['text']) for e in selected) <= 1800


def test_explicit_line_enables_only_corresponding_original_hexagram_commentary():
    cast = calculate_hexagram([8, 7, 8, 8, 7, 8])
    evidence = initial_evidence(cast, '这个意思是什么？', commentary_lines=[2])
    selected = comments(evidence)
    assert {e['id'] for e in selected} == {
        'wang-bi@2404949:h29:line2:75b15f7dc9', 'zhu-xi@528287:h29:line2:9d356a7715'}
    assert all(e['hexagramId'] == 29 and e['line'] == 2 for e in selected)
    # Explicit comments do not silently expand the mandatory classic baseline;
    # exact missing classic targets still belong to the shared tool path.
    assert 'zy-29-line-2' not in {e['id'] for e in evidence}


def test_moving_and_explicit_lines_form_a_bounded_union():
    cast = calculate_hexagram([7, 9, 7, 7, 7, 7])
    selected = comments(initial_evidence(cast, '潜龙勿用', commentary_lines=[1]))
    assert selected and all(e['line'] in {1, 2} and e['hexagramId'] == 1 for e in selected)
    authors = [tuple(e['authorIds']) for e in selected]
    assert len(authors) == len(set(authors)) <= 3
    assert sum(len(e['text']) for e in selected) <= 1800


def test_release_and_original_records_are_not_changed():
    corpus, manifest = load_release(CURRENT_RELEASE)
    before = copy.deepcopy((corpus, manifest))
    by_id = {e['id']: e for e in corpus}
    evidence = initial_evidence(calculate_hexagram([8, 7, 8, 6, 7, 8]),
                                '薄礼诚心', CURRENT_RELEASE, commentary_lines=[2])
    assert all(e == by_id[e['id']] and e['releaseId'] == CURRENT_RELEASE for e in evidence)
    assert (corpus, manifest) == before


@pytest.mark.parametrize('explicit', [[0], [8], [True], ['2']])
def test_invalid_internal_line_targets_are_not_silently_reinterpreted(explicit):
    with pytest.raises(ValueError, match='INVALID_COMMENTARY_LINES'):
        initial_evidence(calculate_hexagram([7] * 6), commentary_lines=explicit)
