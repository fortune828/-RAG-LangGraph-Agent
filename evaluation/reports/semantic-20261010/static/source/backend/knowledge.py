"""Versioned exact lookup and Chinese bigram BM25 over existing releases."""
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache

from .config import ROOT
from .models import PassageArgs, SearchArgs

DATA = ROOT / 'data'
LEGACY_RELEASE = json.loads((DATA / 'manifest.json').read_text())['version']
CURRENT_RELEASE = json.loads((DATA / 'knowledge/active.json').read_text())['releaseId']
CHAR_MAP = {}
for row in (DATA / 'knowledge/raw/opencc-TSCharacters.txt').read_text().splitlines():
    if '\t' in row:
        key, value = row.split('\t', 1)
        CHAR_MAP[key] = value.split(' ')[0]


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and unicodedata.category(c)[0] not in 'PS')


def normalize_search(text):
    return ''.join(CHAR_MAP.get(c, c) for c in normalize(text)).replace('恆', '恒')


@lru_cache(maxsize=16)
def load_release(release=CURRENT_RELEASE):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,90}', release):
        raise ValueError('RELEASE_UNAVAILABLE')
    folder = DATA if release == LEGACY_RELEASE else DATA / 'knowledge/releases' / release
    if not (folder / 'manifest.json').exists():
        raise ValueError('RELEASE_UNAVAILABLE')
    manifest = json.loads((folder / 'manifest.json').read_text())
    raw = (folder / 'evidence.json').read_bytes()
    if manifest.get('evidenceSha256') and hashlib.sha256(raw).hexdigest() != manifest['evidenceSha256']:
        raise ValueError('CORPUS_HASH_MISMATCH')
    corpus = [dict(e, layer=e.get('layer', 'classic'), releaseId=release) for e in json.loads(raw)]
    return corpus, manifest


def coverage(release=CURRENT_RELEASE):
    corpus, manifest = load_release(release)
    return dict(releaseId=release, **manifest.get('coverage', {
        'classic': {'records': len(corpus), 'hexagramIds': list(range(1, 65)),
                    'missingTuan': manifest.get('missingTuan', [])},
        'authors': {}, 'layers': ['classic'], 'humanReviewedRecords': 0}))


def allowed(e, args):
    layers = args.get('layers') or (None if args.get('authorIds') or args.get('workIds') else ['classic'])
    return (not args.get('hexagramId') or e['hexagramId'] == args['hexagramId']) and (
        not layers or e['layer'] in layers) and all(
        not args.get(key) or bool(set(args[key]) & set(values)) for key, values in (
            ('authorIds', e.get('authorIds', [])), ('workIds', [e.get('workId', 'zhouyi')]),
            ('editionIds', [e.get('editionId', 'open-iching-v2')]), ('anchorIds', e.get('anchorIds', []))))


def terms(text):
    return [text[i:i + 2] for i in range(len(text) - 1)] if len(text) >= 2 else list(text)


@lru_cache(maxsize=16)
def lexical_index(release):
    docs, frequency = {}, Counter()
    for e in load_release(release)[0]:
        text = normalize_search(' '.join([e['text'], e['name'], e['locator'], e.get('workTitle', ''),
                                         ' '.join(e.get('authorNames', []))]))
        counts = Counter(terms(text))
        docs[e['id']] = (text, counts, sum(counts.values()))
        frequency.update(counts.keys())
    return docs, frequency, sum(v[2] for v in docs.values()) / max(len(docs), 1)


def rank_lexical(query, scope, release):
    query = normalize_search(query)
    if not query:
        return []
    docs, frequency, average = lexical_index(release)
    ranked = []
    for e in scope:
        text, counts, length = docs[e['id']]
        score = 12.0 if query in text else 0.0
        for term in set(terms(query)):
            tf = counts[term]
            if tf:
                df = frequency[term]
                score += math.log(1 + (len(docs) - df + .5) / (df + .5)) * tf * 2.2 / (
                    tf + 1.2 * (.25 + .75 * length / average))
        if score > 0:
            ranked.append((e, score))
    return sorted(ranked, key=lambda row: (-row[1], row[0]['id']))


def retrieval_result(args, scope, ranked, release, channel, limit):
    selected, chars = [], 0
    for e, score in ranked:
        if len(selected) < limit and chars + len(e['text']) <= 12000:
            selected.append((e, score))
            chars += len(e['text'])
    missing = []
    for section in args.get('sections', []):
        lines = (args.get('lineIndices') or ([7] if section == 'special' else list(range(1, 7)))) if section in ('line', 'line_xiang', 'special') else [None]
        for line in lines:
            for author in args.get('authorIds') or [None]:
                if not any(e['section'] == section and e['line'] == line and
                           (not author or author in e.get('authorIds', [])) for e in scope):
                    missing.append(f"{args.get('hexagramId')}:{section}:{line}|author:{author}")
    for key, field in [('authorIds', 'authorIds'), ('workIds', 'workId'), ('anchorIds', 'anchorIds')]:
        for value in args.get(key) or []:
            if not any(value in (e.get(field, []) if isinstance(e.get(field, []), list) else [e.get(field)]) for e in scope):
                missing.append(f'{key}:{value}')
    return dict(evidence=[e for e, _ in selected],
                hits=[dict(passageId=e['id'], channel=channel, score=score,
                           matchReason='metadata exact match' if channel == 'exact' else 'Chinese bigram BM25 + phrase boost') for e, score in selected],
                releaseId=release, coverageStatus='not_ingested' if not scope else 'partial' if missing else 'covered',
                missingTargets=missing, truncated=len(selected) < len(ranked),
                **({'noHitReason': 'no_match' if scope else 'filtered_out'} if not ranked else {}))


def retrieve_exact(raw, release=CURRENT_RELEASE, scope_ids=None):
    args = PassageArgs.model_validate(raw).model_dump(exclude_none=True)
    if scope_ids is not None and args['hexagramId'] not in scope_ids:
        raise ValueError('EVIDENCE_SCOPE_DENIED')
    found = [e for e in load_release(release)[0] if allowed(e, args) and e['section'] in args['sections']
             and (not args.get('lineIndices') or e['line'] is None or e['line'] in args['lineIndices'])]
    return retrieval_result(args, found, [(e, None) for e in found], release, 'exact', 18)


def retrieve_lexical(raw, release=CURRENT_RELEASE, scope_ids=None):
    args = SearchArgs.model_validate(raw).model_dump(exclude_none=True)
    if scope_ids is not None and args.get('hexagramId') and args['hexagramId'] not in scope_ids:
        raise ValueError('EVIDENCE_SCOPE_DENIED')
    scope = [e for e in load_release(release)[0] if allowed(e, args)
             and (scope_ids is None or e['hexagramId'] in scope_ids)]
    return retrieval_result(args, scope, rank_lexical(args['query'], scope, release), release, 'lexical', args['topK'])


def initial_evidence(cast, question='', release=CURRENT_RELEASE, *, commentary_lines=()):
    """Prepare classics plus comments anchored to moving or explicitly asked lines.

    The caller supplies explicit line targets for the original hexagram. Modern
    question vocabulary alone is not evidence that an arbitrary line is relevant.
    """
    original = cast['original']['number']
    def get(h, sections, **kw):
        return retrieve_exact(dict(hexagramId=h, sections=sections, **kw), release)['evidence']
    records = get(original, ['judgment', 'xiang', 'tuan'])
    moving = [i + 1 for i in cast['changingIndices']]
    explicit = tuple(commentary_lines)
    if any(type(line) is not int or not 1 <= line <= 7 for line in explicit):
        raise ValueError('INVALID_COMMENTARY_LINES')
    relevant_lines = set(moving) | set(explicit)
    if moving:
        records += get(original, ['line', 'line_xiang'], lineIndices=moving)
    if cast['changed']['number'] != original:
        records += get(cast['changed']['number'], ['judgment', 'xiang'])
    pool = [e for e in load_release(release)[0] if e['layer'] == 'commentary' and e['hexagramId'] == original
            and 20 < len(e['text']) <= 1200 and e['line'] in relevant_lines]
    ranked = rank_lexical(question[:160], pool, release) if question else []
    candidates = [e for e, _ in ranked] if ranked else pool
    authors, budget = set(), 1800
    for e in candidates:
        author = tuple(e.get('authorIds', []))
        if len(authors) < 3 and author not in authors and len(e['text']) <= budget:
            records.append(e)
            authors.add(author)
            budget -= len(e['text'])
    return list({e['id']: e for e in records}.values())


def canonical_tool_args(name, raw):
    schemas = {'get_passages': PassageArgs, 'search_knowledge': SearchArgs}
    if name not in schemas:
        raise ValueError('UNKNOWN_TOOL')
    args = schemas[name].model_validate(raw).model_dump(exclude_none=True)
    # Filters are sets; defaults and list ordering must not bypass deduplication.
    for key, value in args.items():
        if isinstance(value, list):
            args[key] = sorted(set(value))
    if name == 'search_knowledge':
        args['query'] = normalize_search(args['query'])
    return args


def execute_tool(name, args, release, scope_ids):
    functions = {'get_passages': retrieve_exact, 'search_knowledge': retrieve_lexical}
    if name not in functions:
        raise ValueError('UNKNOWN_TOOL')
    return functions[name](args, release, scope_ids)


def tool_schema(model):
    schema = model.model_json_schema()
    definitions = schema.pop('$defs', {})
    def expand(value):
        if isinstance(value, list):
            return [expand(v) for v in value]
        if not isinstance(value, dict):
            return value
        if '$ref' in value:
            return expand(definitions[value['$ref'].split('/')[-1]])
        return {k: expand(v) for k, v in value.items() if k != 'title'}
    return expand(schema)


TOOLS = [dict(type='function', function=dict(name=name, description=description, parameters=tool_schema(model)))
         for name, model, description in [
             ('get_passages', PassageArgs, '按卦号、章节和爻位读取原文或注解。注解使用 layers=[commentary]，可指定作者和作品。'),
             ('search_knowledge', SearchArgs, '按关键词、短句或主题进行中文词面检索，可用卦号、作者、作品等元数据筛选。')]]
