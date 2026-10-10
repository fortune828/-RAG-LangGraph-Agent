"""C/W1: deterministic retrieval decisions over the same production state.

No cases, expected answers, labels or alternative corpus are available here.
Actual tool execution, permissions and retry budgets belong to the shared graph.
"""
import json
import re

from backend.graph import missing_targets, target_availability
from backend.knowledge import canonical_tool_args
from backend.validation import AUTHORS

POLICY_VERSION = 'rules-c-v1'


def source_query(question):
    quoted = re.findall(r'[“「『"]([^”」』"]+)[”」』"]', question)
    if quoted:
        return quoted[0][:160]
    fuzzy = re.search(r'(?:大意是|意思是|大概是)(.+)', question)
    text = fuzzy[1] if fuzzy else question
    text = re.sub(r'《[^》]+》|请(?:问|给出|查出|提供)?|原文出处|出自哪里|是不是说过|是否说过', '', text)
    for names in AUTHORS.values():
        for name in names:
            text = text.replace(name, '')
    return text.strip(' ，。？！?；：:')[:160] or question[:160]


def rule_route(state):
    def request(name, args):
        canonical = canonical_tool_args(name, args)
        signature = name + json.dumps(canonical, sort_keys=True)
        if signature in state['seen'] or state['attempts'] >= 3:
            return None
        return dict(role='assistant', content=None, tool_calls=[dict(
            id=f"rule-{state['attempts'] + 1}", type='function',
            function=dict(name=name, arguments=json.dumps(args, ensure_ascii=False)))])

    gaps = missing_targets(state)
    for target in gaps:
        if target.get('hexagramId') is None:
            continue
        args = dict(hexagramId=target['hexagramId'],
                    sections=[target.get('section') or 'line'], layers=[target['layer']])
        if target.get('line'):
            args['lineIndices'] = [target['line']]
        for source, key in [('authorId', 'authorIds'), ('workId', 'workIds')]:
            if target.get(source):
                args[key] = [target[source]]
        decision = request('get_passages', args)
        if decision:
            return decision

    # One lexical lookup for an unknown source/title. An empty result is not
    # permission to pretend the entire corpus lacks the requested passage.
    availability = target_availability(state)
    needs_search = state['intent'] == 'source_request' and (
        state['scope_ids'] is None or availability['unresolvedBooks'])
    searched = any(e['step'] == 'tool' and e.get('tool') == 'search_knowledge' for e in state['trace'])
    if needs_search and not searched:
        args = dict(query=source_query(state['question']), topK=4)
        authors = sorted({t['authorId'] for t in state['targets'] if t.get('authorId')})
        works = sorted({t['workId'] for t in state['targets'] if t.get('workId')})
        if authors or works:
            args['layers'] = ['commentary']
        if authors:
            args['authorIds'] = authors
        if works:
            args['workIds'] = works
        decision = request('search_knowledge', args)
        if decision:
            return decision
    return dict(role='assistant', content='{"ready":true}')
