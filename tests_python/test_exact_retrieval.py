"""Exact planning uses real corpus metadata, never cases or expected answers."""
import copy
import json

import pytest

from backend.knowledge import CURRENT_RELEASE, canonical_tool_args, execute_tool, load_release
from backend.retrieval import exact_target_request


def target(section='line', line=2, h=29, layer='classic', **filters):
    return dict(hexagramId=h, section=section, line=line, layer=layer, **filters)


def state(*targets, **overrides):
    return dict(release=CURRENT_RELEASE, scope_ids=[1, 2, 29], targets=list(targets),
                evidence=[], question='请给出本卦第二爻原文。', intent='source_request',
                attempts=0, seen=[]) | overrides


def arguments(message):
    assert message['role'] == 'assistant' and message['content'] is None
    assert all(c['type'] == 'function' and c['function']['name'] == 'get_passages'
               for c in message['tool_calls'])
    return [json.loads(c['function']['arguments']) for c in message['tool_calls']]


def returned_ids(message, scope):
    return {e['id'] for args in arguments(message)
            for e in execute_tool('get_passages', args, CURRENT_RELEASE, scope)['evidence']}


def test_same_line_sections_merge_without_mutating_or_executing_state():
    original = state(target(), target('line_xiang'))
    before = copy.deepcopy(original)
    message = exact_target_request(original)
    assert arguments(message) == [dict(hexagramId=29, sections=['line', 'line_xiang'],
                                      layers=['classic'], lineIndices=[2])]
    assert original == before
    assert returned_ids(message, original['scope_ids']) == {'zy-29-line-2', 'zy-29-line_xiang-2'}


def test_complete_rectangles_combine_multiple_lines_and_sections():
    value = state(*(target(section, line) for line in (3, 2) for section in ('line_xiang', 'line')))
    message = exact_target_request(value)
    assert len(arguments(message)) == 1
    assert returned_ids(message, value['scope_ids']) == {
        'zy-29-line-2', 'zy-29-line-3', 'zy-29-line_xiang-2', 'zy-29-line_xiang-3'}


def test_incomplete_rectangle_does_not_fetch_unrequested_cross_objects():
    value = state(target('line', 2), target('line_xiang', 2), target('line', 3))
    message = exact_target_request(value)
    assert len(arguments(message)) == 2
    assert returned_ids(message, value['scope_ids']) == {
        'zy-29-line-2', 'zy-29-line_xiang-2', 'zy-29-line-3'}


def test_author_work_and_hexagram_filters_never_cross_merge():
    value = state(target(h=1, layer='commentary', authorId='wang-bi', workId='zhouyi-zhengyi'),
                  target(h=1, layer='commentary', authorId='zhu-xi', workId='zhouyi-benyi'),
                  target(h=2, layer='commentary', authorId='wang-bi', workId='zhouyi-zhengyi'))
    message = exact_target_request(value)
    assert len(arguments(message)) == 3
    assert returned_ids(message, value['scope_ids']) == {
        'wang-bi@2676007:h1:line2:156a776063', 'zhu-xi@528287:h1:line2:9c12de10ea',
        'wang-bi@2404938:h2:line2:0157d47dbc'}


def test_classics_and_commentaries_remain_separate():
    value = state(target(), target(layer='commentary', authorId='wang-bi'))
    message = exact_target_request(value)
    assert len(arguments(message)) == 2
    assert returned_ids(message, value['scope_ids']) == {
        'zy-29-line-2', 'wang-bi@2404949:h29:line2:75b15f7dc9'}


def test_non_line_sections_and_special_are_precise_objects():
    value = state(target('judgment', None, h=1), target('tuan', None, h=1), target('special', 7, h=1))
    message = exact_target_request(value)
    assert len(arguments(message)) == 2
    assert returned_ids(message, value['scope_ids']) == {'zy-1-judgment', 'zy-1-tuan', 'zy-1-special-7'}


def test_already_present_targets_are_not_requested_again():
    existing = next(e for e in load_release()[0] if e['id'] == 'zy-29-line-2')
    assert exact_target_request(state(target(), evidence=[existing])) is None
    message = exact_target_request(state(target(), target('line_xiang'), evidence=[existing]))
    assert arguments(message)[0]['sections'] == ['line_xiang']


def test_seen_requests_and_remaining_budget_are_respected():
    value = state(target(), target('line_xiang'))
    first = exact_target_request(value)
    args = canonical_tool_args('get_passages', arguments(first)[0])
    value['seen'] = ['get_passages' + json.dumps(args, sort_keys=True)]
    assert exact_target_request(value) is None
    limited = state(target(h=1), target(h=2), target(h=29), attempts=2)
    message = exact_target_request(limited)
    assert len(message['tool_calls']) == 1
    assert message['tool_calls'][0]['id'] == 'exact-target-3'
    assert exact_target_request(limited | {'attempts': 3}) is None


@pytest.mark.parametrize('value', [
    state(),
    state(target(), scope_ids=None),
    state(target(), scope_ids=[1]),
    state(target(), unresolvedBooks=['尚未识别']),
    state(target(), question='请查《未识别的书》对本卦的原文。'),
    state(dict(hexagramId=29, layer='commentary', authorId='wang-bi')),
    state(target(line=None)),
    state(target('line_xiang', None)),
    state(target(line=7)),
    state(target('special', None, h=1)),
    state(target('special', 6, h=1)),
    state(target('judgment', 2)),
    state(target(section='unknown')),
    state(target(layer='commentary', authorId='jiao-xun')),
    state(target(), target(layer='commentary', authorId='jiao-xun')),
    state(target(editionId='future-target-filter')),
])
def test_ambiguous_missing_or_unauthorized_decisions_stay_with_agent(value):
    assert exact_target_request(value) is None


def test_work_name_requests_conservatively_keep_model_routing():
    value = state(target(h=1, layer='commentary', authorId='zhu-xi', workId='zhouyi-benyi'),
                  question='请查《周易本义》的九二注解。')
    assert exact_target_request(value) is None


@pytest.mark.parametrize('question', [
    '请给出本卦第二爻原文。',
    '请查本卦九二爻辞和小象。',
    '本卦第三爻和第四爻原文是什么？',
    '请提供当前卦九二的爻辞原文与小象原文。',
    '请给出本卦第二爻原文，并解释含义。',
    '请查本卦第二爻原文，它是什么意思？',
    ' 请给出 本卦 第二爻 原文。 ',
])
def test_only_complete_plain_source_requests_enable_automatic_planning(question):
    assert exact_target_request(state(target(), question=question)) is not None


@pytest.mark.parametrize('question', [
    '我和对象分手了，本卦第二爻对复合这件事有什么启发？',
    '请给出本卦第二爻原文，再谈谈这对我换工作有什么启发。',
    '请给出本卦第二爻原文。我还想讨论孩子转学。',
    '请给出本卦第二爻原文；另外有一个问题。',
    '请给出本卦第二爻原文，暂时不要按这个来回答。',
    '朱熹怎样解释本卦第二爻？',
    '本卦第二爻的“见龙在田”是什么意思？',
])
def test_unknown_remaining_language_preserves_router_judgment(question):
    assert exact_target_request(state(target(), question=question)) is None


def test_multiple_hexagrams_and_lines_do_not_assume_correct_pairing():
    value = state(*(target(h=h, line=line) for h in (1, 2) for line in (2, 3)))
    assert exact_target_request(value) is None
    assert exact_target_request(state(target(h=1, line=2), target(h=1, line=3))) is not None
    assert exact_target_request(state(target(h=1, line=2), target(h=2, line=2))) is not None
