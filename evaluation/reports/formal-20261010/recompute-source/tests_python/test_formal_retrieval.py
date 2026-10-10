"""Offline ablations preserve authorization and do not turn unknown labels into zero."""
import copy
import json

import pytest

from backend.knowledge import CURRENT_RELEASE, load_release, rank_lexical
from evaluation.formal_retrieval import (
    digest, lexical_search, query_context, rank_overlap, read_frozen, score_hits, summarize,
)


def case(question='请给出本卦第二爻的原文。'):
    return dict(question='核对原文。', currentQuestion=question, mode='followup', lineValues=[7]*6)


def reference():
    return dict(evidenceGroups=[['A', 'B'], ['C']], relevantEvidenceIds=['A', 'B', 'C'],
                annotationCompleteness={'semanticRelevanceExhaustive': False})


def test_runtime_query_does_not_read_gold_or_label_metadata():
    raw = case()
    clean = query_context(raw)
    polluted = dict(raw, gold=['hidden'], semanticReference='secret', scopeIds=None,
                    referenceExactArgs=[{'hexagramId': 29}], referenceSearchFilters={'authorIds': ['jiao-xun']})
    assert query_context(polluted) == clean
    assert clean['scopeIds'] == [1]
    assert clean['exactRequests'] == [dict(hexagramId=1, sections=['line'], layers=['classic'], lineIndices=[2])]
    assert 'secret' not in json.dumps(clean)


def test_all_lexical_arms_preserve_hard_authorization_even_with_inconsistent_filter():
    context = query_context(case())
    context.update(query='坎有险求小得', businessScopes=[dict(hexagramId=29, layers=['classic'])])
    by_id = {e['id']: e for e in load_release()[0]}
    for arm in ['R1', 'R2', 'R3']:
        result = lexical_search(context, arm)
        assert all(by_id[e]['hexagramId'] == 1 for e in result['resultEvidenceIds'])
    assert lexical_search(context, 'R3')['filteredCandidateCount'] == 0


def test_r2_reuses_existing_bm25_with_same_authorized_corpus():
    context = query_context(case('本卦九二的原文是什么？'))
    corpus = [e for e in load_release()[0] if e['hexagramId'] in context['scopeIds']]
    expected = rank_lexical(context['query'], corpus, CURRENT_RELEASE)[:5]
    actual = lexical_search(context, 'R2')
    assert actual['resultEvidenceIds'] == [e['id'] for e, _ in expected]
    assert [hit['score'] for hit in actual['hits']] == [score for _, score in expected]


def test_r1_has_shared_normalization_phrase_boost_and_stable_ties():
    corpus = [e for e in load_release()[0] if e['id'] in ['zy-1-judgment', 'zy-2-judgment']]
    result = rank_overlap('元，亨', corpus, CURRENT_RELEASE)
    assert result and all(score >= 13 for _, score in result)
    assert [(e['id'], score) for e, score in result] == [(e['id'], score) for e, score in rank_overlap('元亨', corpus, CURRENT_RELEASE)]


def test_alternative_groups_do_not_inflate_recall_denominator_and_unknown_is_not_negative():
    result = score_hits(['A', 'unjudged', 'C'], reference(), 5)
    assert result['groupCoverage'] == {'numerator': 2, 'denominator': 2, 'value': 1.0}
    assert result['hit']['value'] == 1 and result['allGroupsCovered']
    assert result['recall']['value'] is None
    assert result['precisionAtK']['value'] is None
    assert result['unknownRelevance'] == 1
    assert result['precisionBounds'] == {'lower': 0.4, 'upper': 0.6}


def test_complete_relevance_explicitly_enables_strict_recall():
    label = reference()
    label['annotationCompleteness']['semanticRelevanceExhaustive'] = True
    result = score_hits(['A', 'known-negative'], label, 3)
    assert result['recall']['value'] == pytest.approx(1/3)
    assert result['precisionAtK']['value'] == pytest.approx(1/3)
    assert result['unknownRelevance'] == 0


def test_missing_sources_have_no_recall_or_hit_denominator():
    result = score_hits(['unrelated'], {'evidenceGroups': [], 'availability': 'not_ingested'}, 5)
    assert not result['eligible']
    assert result['hit']['denominator'] is None and result['groupCoverage']['value'] is None
    assert result['precisionAtK']['value'] is None


def test_separate_pool_judgments_preserve_conflict_as_unknown():
    label = reference()
    before = copy.deepcopy(label)
    result = score_hits(['A', 'not-related'], label, 5, {'A': 'relevant', 'not-related': 'irrelevant'})
    assert result['precisionAtK']['value'] == 0.2
    assert label == before
    disagreement = score_hits(['A'], label, 5, {'A': 'irrelevant'})
    assert disagreement['precisionAtK']['value'] is None
    assert disagreement['unknownRelevance'] == 1
    assert disagreement['referenceConflicts'] == ['A']
    assert disagreement['groupCoverage']['numerator'] == 1
    assert label == before


def test_requested_target_recall_is_separate_from_open_semantic_recall():
    label = reference()
    label['annotationCompleteness']['requestedMetadataExhaustive'] = True
    result = score_hits(['A', 'C'], label, 3)
    assert result['requestedTargetRecall']['value'] == pytest.approx(2/3)
    assert result['groupCoverage']['value'] == 1
    assert result['recall']['value'] is None


def test_independent_unknown_is_not_replaced_by_authored_positive_reference():
    result = score_hits(['A'], reference(), 1, {'A': 'unknown'})
    assert result['precisionAtK']['value'] is None
    assert result['unknownRelevance'] == 1


def test_existing_unknown_line_parser_failure_is_not_corrected_using_gold():
    context = query_context(case('我记得王弼有一段解释，究竟是哪一爻？请核对出处。'))
    assert context['scopeIds'] == [1]
    assert any(t.get('line') == 1 for t in context['parsedTargets'])
    assert all(request['hexagramId'] == 1 for request in context['exactRequests'])


def test_summary_reports_unknown_precision_and_recall_counts():
    row = dict(split='independent_test', arm='R2', durationMs=1.0,
               scores=[score_hits(['A', 'unjudged'], reference(), 5)])
    result = summarize([row])[0]
    assert result['strictRecall'] == {'knownCases': 0, 'unknownCases': 1, 'mean': None}
    assert result['precisionAtK']['unknownCases'] == 1
    assert result['precisionAtK']['meanAmongKnown'] is None


def test_changed_frozen_input_is_rejected_before_any_retrieval(tmp_path):
    data = tmp_path/'data.json'
    data.write_text('{}')
    (tmp_path/'freeze.json').write_text(json.dumps({'files': {'data.json': digest(data)}}))
    data.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='FROZEN_DATA_CHANGED:data.json'):
        read_frozen(tmp_path)
