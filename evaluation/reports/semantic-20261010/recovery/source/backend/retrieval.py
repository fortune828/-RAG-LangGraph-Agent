"""Plan unambiguous missing passages; execution and authorization stay in the graph."""
import json
import re

from .knowledge import canonical_tool_args, load_release, normalize_search
from .validation import WORKS, matches_target


def exact_target_request(state):
    """Return bounded get_passages requests, or leave the decision to the Agent.

    Only an entirely precise, available target set is handled here. No tool is
    executed, and neither state nor evidence is changed. Requests combine only
    complete section/line rectangles, so batching never adds an unasked object.
    """
    scope = state.get('scope_ids')
    attempts = state.get('attempts', 0)
    targets = state.get('targets', [])
    if scope is None or attempts >= 3 or not targets or state.get('unresolvedBooks'):
        return None

    corpus = load_release(state['release'])[0]
    if state.get('intent', 'source_request') == 'source_request':
        known = {normalize_search(e.get('workTitle', '周易')) for e in corpus}
        known.update(normalize_search(name) for names in WORKS.values() for name in names)
        books = re.findall(r'《([^》]{1,80})》', state.get('question', ''))
        if any(normalize_search(book) not in known for book in books):
            return None

    def matches(evidence, target):
        # A commentary target may otherwise match subcommentary, while the
        # exact tool's layers filter would not return it.
        return evidence.get('layer', 'classic') == target['layer'] and matches_target(evidence, target)

    groups = {}
    for target in targets:
        if set(target) - {'hexagramId', 'layer', 'section', 'line', 'authorId', 'workId'}:
            return None  # Do not silently drop future target constraints.
        h, layer, section, line = (target.get(key) for key in ('hexagramId', 'layer', 'section', 'line'))
        if h not in scope or not section or not layer:
            return None
        if section in ('line', 'line_xiang'):
            if type(line) is not int or not 1 <= line <= 6:
                return None
        elif section == 'special':
            if type(line) is not int or line != 7:
                return None
        elif section not in ('judgment', 'tuan', 'xiang') or line is not None:
            return None

        args = dict(hexagramId=h, sections=[section], layers=[layer])
        if line is not None:
            args['lineIndices'] = [line]
        for field, key in [('authorId', 'authorIds'), ('workId', 'workIds')]:
            if target.get(field):
                args[key] = [target[field]]
        try:
            canonical_tool_args('get_passages', args)
        except (ValueError, TypeError):
            return None
        if not any(matches(e, target) for e in corpus):
            return None
        if any(matches(e, target) for e in state.get('evidence', [])):
            continue
        key = (h, layer, target.get('authorId') or '', target.get('workId') or '')
        groups.setdefault(key, {}).setdefault(line, set()).add(section)

    calls = []
    for (h, layer, author, work), by_line in sorted(groups.items()):
        rectangles = {}
        for line, sections in by_line.items():
            rectangles.setdefault(tuple(sorted(sections)), []).append(line)
        for sections, lines in sorted(rectangles.items()):
            args = dict(hexagramId=h, sections=list(sections), layers=[layer])
            if lines != [None]:
                args['lineIndices'] = sorted(lines)
            if author:
                args['authorIds'] = [author]
            if work:
                args['workIds'] = [work]
            args = canonical_tool_args('get_passages', args)
            signature = 'get_passages' + json.dumps(args, sort_keys=True)
            if signature in state.get('seen', []):
                continue
            calls.append(dict(id=f'exact-target-{attempts + len(calls) + 1}', type='function',
                              function=dict(name='get_passages', arguments=json.dumps(args, ensure_ascii=False))))
            if len(calls) == 3 - attempts:
                return dict(role='assistant', content=None, tool_calls=calls)
    return dict(role='assistant', content=None, tool_calls=calls) if calls else None
