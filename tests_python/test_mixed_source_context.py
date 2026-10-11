"""Acquired classics must survive generation filtering in mixed source requests."""
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.domain import calculate_hexagram
from backend.graph import build_graph, generation_evidence, initial_state, target_availability
from backend.knowledge import CURRENT_RELEASE, load_release
from backend.validation import requested_targets, validate_answer
from tests_python.test_migration import response, session, tool_call


@pytest.mark.parametrize('question', [
    '“见金夫，不有躬”出自哪里？我想先看原文，再看朱熹如何解释“金夫”。',
    '请给蒙卦六三爻辞原文以及朱熹的解释。',
    '先看朱熹对蒙卦六三的解释，再看爻辞原文。',
    '本卦九二原文与王弼注解。',
])
def test_separate_classic_request_retains_both_layers(question):
    targets = requested_targets(question, calculate_hexagram([8, 7, 8, 8, 8, 7]))
    assert {target['layer'] for target in targets} == {'classic', 'commentary'}
    assert all('authorId' not in t and 'workId' not in t for t in targets if t['layer'] == 'classic')


@pytest.mark.parametrize('question', [
    '请给朱熹关于蒙卦六三的注解原文。',
    '朱熹对蒙卦六三原文的解释是什么？',
    '请给朱熹对蒙卦六三的注解，不要经文。',
    '王弼和朱熹分别如何解释本卦九二？',
])
def test_commentary_text_is_not_misidentified_as_a_classic_request(question):
    targets = requested_targets(question, calculate_hexagram([7] * 6))
    assert targets and all(target['layer'] == 'commentary' for target in targets)


def test_frozen_context_filter_failure_retains_acquired_classic_without_unrelated_sources():
    frozen = json.loads((Path(__file__).parents[1] /
        'evaluation/reports/formal-20261010/analysis/context-filter-case.json').read_text())
    case = frozen['input']
    targets = [dict(t, hexagramId=None) for t in requested_targets(case['question'], case['session']['cast'])]
    corpus = {e['id']: e for e in load_release()[0]}
    record = next(r for r in frozen['records'] if r['arm'] == 'H')
    state = initial_state(case['session'], case['question'], case['mode']) | dict(
        intent='source_request', targets=targets, scope_ids=None, trace=record['trace'],
        evidence=[corpus[eid] for eid in record['retrievedEvidenceIds']])
    visible = generation_evidence(state)
    visible_ids = {e['id'] for e in visible}
    assert 'zy-4-line-3' in visible_ids
    assert 'zhu-xi@528287:h4:line3:5e2050a509' in visible_ids
    assert not visible_ids.intersection(record['initialEvidenceIds'])
    # The old answer cites only Zhu Xi and claims the classic was unavailable.
    # It may no longer pass as a completed mixed-source request.
    assert any(error.startswith('REQUIRED_TARGET_NOT_CITED:')
               for error in validate_answer(record['answer'], visible, targets)[1])
    # Retrieving only the author's note must not let the initial cast's judgment
    # pretend the unknown classic has already been acquired.
    commentary_only = dict(state, trace=[dict(step='tool', evidenceIds=[
        'zhu-xi@528287:h4:line3:5e2050a509'])])
    availability = target_availability(commentary_only)
    assert any(t['layer'] == 'classic' for t in availability['retrievable'])
    assert not any(t['layer'] == 'classic' for t in availability['available'])


async def test_mixed_unknown_source_flows_from_real_tools_to_generation_and_validation():
    question = '“见金夫，不有躬”出自哪里？我想先看原文，再看朱熹如何解释“金夫”。'
    s = session()
    captured = []

    async def model(messages, tools=None):
        if tools:
            if any(message['role'] == 'tool' for message in messages):
                return response({'ready': True})
            return response(calls=[
                tool_call(args=dict(hexagramId=4, sections=['line'], lineIndices=[3], layers=['classic'])),
                tool_call(args=dict(hexagramId=4, sections=['line'], lineIndices=[3],
                                    layers=['commentary'], authorIds=['zhu-xi']))])
        # Compatible with either JSON-packed or native-role conversational history.
        contexts = []
        for message in messages:
            try:
                value = json.loads(message.get('content') or '')
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and '参考原文' in value:
                contexts.append(value)
        evidence = contexts[-1]['参考原文']
        captured.extend(e['id'] for e in evidence)
        classic = next(e for e in evidence if e['id'] == 'zy-4-line-3')
        commentary = next(e for e in evidence if e['id'].startswith('zhu-xi'))
        text = '蒙卦六三的经文和朱熹注解均已取得。'
        return response(dict(status='answered', paragraphs=[dict(text=text, citations=[
            dict(evidenceId=e['id'], quote=e['text'][:8], claim=text)
            for e in (classic, commentary)])], limitations=[]))

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, question, 'followup'), {'configurable': {'thread_id': 'mixed-source-context'}})
    assert result['scope_ids'] is None
    assert result['attempts'] == 2 and result['repairs'] == 0 and not result['errors']
    assert 'zy-4-line-3' in captured
    assert 'zy-1-judgment' not in captured
    assert result['answer']['status'] == 'answered'
    assert result['session']['cast'] == s['cast']
