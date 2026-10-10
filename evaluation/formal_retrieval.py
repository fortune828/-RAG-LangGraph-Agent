"""Offline frozen-input retrieval comparison; references are used only for scoring.

R1/R2 remove business metadata filters, never server authorization. R3 applies
the existing production target parser through existing search-filter fields.
No model, production mutation, gold-guided query or synthetic success fallback.
"""
import argparse
import hashlib
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from backend.config import ROOT
from backend.domain import calculate_hexagram
from backend.graph import classify, evidence_scope
from backend.knowledge import (
    CURRENT_RELEASE, allowed, canonical_tool_args, execute_tool, lexical_index,
    load_release, normalize_search, rank_lexical, retrieval_result, terms,
)
from backend.validation import matches_target, requested_targets
from evaluation.workflow import source_query


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_frozen(folder):
    freeze = json.loads((folder / 'freeze.json').read_text())
    for name, expected in freeze['files'].items():
        if digest(folder / name) != expected:
            raise ValueError('FROZEN_DATA_CHANGED:' + name)
    data = json.loads((folder / 'data.json').read_text())
    labels = json.loads((folder / 'labels.json').read_text())
    _, manifest = load_release(data['knowledgeRelease'])
    if manifest['evidenceSha256'] != labels['evidenceSha256']:
        raise ValueError('FROZEN_CORPUS_CHANGED')
    return data, {label['caseId']: label for label in labels['cases']}, freeze


def query_context(case, release=CURRENT_RELEASE):
    """Only input text/cast/mode enter this function, never the label file."""
    question = case.get('currentQuestion', case['question'])
    cast = calculate_hexagram(case['lineValues'])
    intent = classify(question, case.get('mode', 'initial'))
    scope = evidence_scope(question, cast, intent)
    targets = requested_targets(question, cast)
    if scope is None:
        targets = [dict(target, hexagramId=None) for target in targets]
    query = source_query(question)
    corpus, _ = load_release(release)
    scopes, exact = [], []
    for target in targets:
        # OR across requested objects; AND within each object's filters. This
        # avoids inventing a production lineIndices field for lexical search.
        filters = dict(layers=[target['layer']])
        if target.get('hexagramId') is not None:
            filters['hexagramId'] = target['hexagramId']
        for field, key in [('authorId', 'authorIds'), ('workId', 'workIds')]:
            if target.get(field):
                filters[key] = [target[field]]
        if target.get('section'):
            anchors = sorted({anchor for e in corpus if matches_target(e, target)
                              for anchor in e.get('anchorIds', [])})
            if anchors:
                filters['anchorIds'] = anchors
        args = canonical_tool_args('search_knowledge', dict(query=query, topK=5, **filters))
        if args not in scopes:
            scopes.append(args)
        section, line = target.get('section'), target.get('line')
        if target.get('hexagramId') is None or not section:
            continue
        if section in ('line', 'line_xiang', 'special') and line is None:
            continue
        args = dict(hexagramId=target['hexagramId'], sections=[section], layers=[target['layer']])
        if line is not None:
            args['lineIndices'] = [line]
        for field, key in [('authorId', 'authorIds'), ('workId', 'workIds')]:
            if target.get(field):
                args[key] = [target[field]]
        args = canonical_tool_args('get_passages', args)
        if args not in exact:
            exact.append(args)
    return dict(question=question, query=query, normalizedQuery=normalize_search(query),
                intent=intent, scopeIds=scope, parsedTargets=targets,
                businessScopes=scopes, exactRequests=exact)


def rank_overlap(query, scope, release):
    query = normalize_search(query)
    if not query:
        return []
    docs = lexical_index(release)[0]
    query_terms = set(terms(query))
    scores = [(e, float(len(query_terms.intersection(docs[e['id']][1]))) +
               (12.0 if query in docs[e['id']][0] else 0.0)) for e in scope]
    return sorted((row for row in scores if row[1] > 0), key=lambda row: (-row[1], row[0]['id']))


def lexical_search(context, arm, release=CURRENT_RELEASE):
    corpus, _ = load_release(release)
    start = time.perf_counter()
    authorized = [e for e in corpus if context['scopeIds'] is None or e['hexagramId'] in context['scopeIds']]
    scope = authorized
    if arm == 'R3' and context['businessScopes']:
        scope = [e for e in authorized if any(allowed(e, args) for args in context['businessScopes'])]
    if arm not in ('R1', 'R2', 'R3'):
        raise ValueError('UNKNOWN_RETRIEVAL_ARM')
    ranked = (rank_overlap if arm == 'R1' else rank_lexical)(context['query'], scope, release)
    result = retrieval_result({}, scope, ranked, release, 'lexical', 5)
    if arm == 'R1':
        for hit in result['hits']:
            hit['matchReason'] = 'Chinese unique bigram overlap + phrase boost'
    duration = (time.perf_counter() - start) * 1000
    return dict(arm=arm, hits=result['hits'], durationMs=duration,
                authorizedCandidateCount=len(authorized), filteredCandidateCount=len(scope),
                filters=context['businessScopes'] if arm == 'R3' else [],
                resultEvidenceIds=[e['id'] for e in result['evidence']],
                noHitReason=result.get('noHitReason'), truncated=result['truncated'])


def fraction(numerator, denominator, reason=None):
    return dict(numerator=numerator, denominator=denominator,
                value=numerator / denominator if numerator is not None and denominator else None,
                **({'reason': reason} if reason else {}))


def score_hits(ids, label, k, judgments=None):
    ids = list(dict.fromkeys(ids))[:k]
    groups = label.get('evidenceGroups', [])
    positives = set(label.get('relevantEvidenceIds', [])) | {e for group in groups for e in group}
    judged = dict(judgments or {})
    conflicts = sorted(eid for eid in positives if judged.get(eid) == 'irrelevant')
    if judgments is None:
        judged.update({eid: 'relevant' for eid in positives})
    for eid in conflicts:
        judged[eid] = 'unknown'  # Preserve both AI views; neither is expert truth.
    known = sum(judged.get(eid) == 'relevant' for eid in ids)
    unknown = sum(judged.get(eid, 'unknown') not in ('relevant', 'irrelevant') for eid in ids)
    complete = label.get('annotationCompleteness', {}).get('semanticRelevanceExhaustive', False)
    if complete and judgments is None:
        unknown = 0  # Outside an explicitly exhaustive relevance set is negative.
    if not groups:
        reason = 'No nonempty evidence reference; absence cases are excluded from recall/hit denominators.'
        return dict(k=k, returned=len(ids), eligible=False, hit=fraction(None, None, reason),
                    groupCoverage=fraction(None, None, reason), allGroupsCovered=None,
                    recall=fraction(None, None, reason), precisionAtK=fraction(None, k, reason),
                    requestedTargetRecall=fraction(None, None, reason),
                    precisionBounds=None, knownRelevant=known, unknownRelevance=unknown,
                    referenceConflicts=conflicts)
    covered = sum(bool(set(group) & set(ids)) for group in groups)
    return dict(k=k, returned=len(ids), eligible=True, hit=fraction(int(covered > 0), 1),
                groupCoverage=fraction(covered, len(groups)), allGroupsCovered=covered == len(groups),
                recall=fraction(len(set(ids) & positives), len(positives)) if complete else
                fraction(None, None, 'Semantic relevance set is not exhaustive; use labelled group coverage.'),
                requestedTargetRecall=fraction(len(set(ids) & positives), len(positives)) if
                label.get('annotationCompleteness', {}).get('requestedMetadataExhaustive', False) else
                fraction(None, None, 'Fuzzy or non-exhaustive requested-object reference.'),
                precisionAtK=fraction(known, k) if not unknown else
                fraction(None, k, 'Unjudged retrieved records remain unknown, not irrelevant.'),
                precisionBounds=dict(lower=known / k, upper=(known + unknown) / k),
                knownRelevant=known, unknownRelevance=unknown, referenceConflicts=conflicts,
                precisionSource='independent_pool_with_conflicts_unknown' if judgments is not None else 'positive_reference_only')


def summarize(rows, by_query_type=False):
    buckets = defaultdict(list)
    for row in rows:
        for score in row['scores']:
            buckets[row['split'], row['arm'], score['k'], row.get('queryType', 'unknown') if by_query_type else 'all'].append((row, score))
    summary = []
    for (split, arm, k, query_type), values in sorted(buckets.items()):
        eligible = [score for _, score in values if score['eligible']]
        precision = [s['precisionAtK']['value'] for s in eligible if s['precisionAtK']['value'] is not None]
        recalls = [s['recall']['value'] for s in eligible if s['recall']['value'] is not None]
        requested = [s['requestedTargetRecall'] for s in eligible if s['requestedTargetRecall']['value'] is not None]
        durations = sorted(row['durationMs'] for row, _ in values)
        summary.append(dict(split=split, arm=arm, k=k, queryType=query_type, cases=len(values), eligibleCases=len(eligible),
            hit=fraction(sum(s['hit']['numerator'] for s in eligible), len(eligible)),
            groupCoverage=fraction(sum(s['groupCoverage']['numerator'] for s in eligible),
                                   sum(s['groupCoverage']['denominator'] for s in eligible)),
            allGroupsCovered=fraction(sum(s['allGroupsCovered'] for s in eligible), len(eligible)),
            strictRecall=dict(knownCases=len(recalls), unknownCases=len(eligible)-len(recalls),
                              mean=statistics.mean(recalls) if recalls else None),
            requestedTargetRecall=dict(eligibleCases=len(requested),
                                       micro=fraction(sum(s['numerator'] for s in requested), sum(s['denominator'] for s in requested)),
                                       macro=statistics.mean(s['value'] for s in requested) if requested else None),
            precisionAtK=dict(knownCases=len(precision), unknownCases=len(eligible)-len(precision),
                              meanAmongKnown=statistics.mean(precision) if precision else None),
            precisionBounds=dict(meanLower=statistics.mean(s['precisionBounds']['lower'] for s in eligible) if eligible else None,
                                 meanUpper=statistics.mean(s['precisionBounds']['upper'] for s in eligible) if eligible else None),
            referenceConflictIds=sorted({eid for s in eligible for eid in s['referenceConflicts']}),
            durationMs=dict(p50=statistics.median(durations), p95=durations[max(0, (95*len(durations)+99)//100-1)],
                            samples=durations)))
    return summary


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_judgments(path):
    if path is None:
        return {}
    review = json.loads(path.read_text())
    if isinstance(review.get('cases'), dict):
        return review['cases']  # Explicit case/evidence mapping supported for replay.
    key = json.loads((path.parent/'pool-key.json').read_text())
    mapping = {(item['queryId'], item['documentId']): item for item in key['documents']}
    result = defaultdict(dict)
    for judgment in review['judgments']:
        item = mapping[judgment['queryId'], judgment['documentId']]
        value = judgment['judgment']
        if value not in ('relevant', 'irrelevant', 'unknown'):
            raise ValueError('INVALID_RELEVANCE_JUDGMENT')
        if item['evidenceId'] in result[item['caseId']]:
            raise ValueError('DUPLICATE_RELEVANCE_JUDGMENT')
        result[item['caseId']][item['evidenceId']] = value
    return dict(result)


def blind_pool(folder, source):
    data, _, _ = read_frozen(folder)
    cases = {c['caseId']: c for c in data['cases']}
    corpus = {e['id']: e for e in load_release(data['knowledgeRelease'])[0]}
    rows = json.loads((source/'rows.json').read_text())
    pairs = defaultdict(set)
    for row in rows:
        pairs[row['caseId']].update(row['resultEvidenceIds'])
    rng = random.Random(4871)
    query_ids = sorted(pairs)
    rng.shuffle(query_ids)
    queries, mappings = [], []
    for index, cid in enumerate(query_ids, 1):
        case = cases[cid]
        cast = calculate_hexagram(case['lineValues'])
        eids = sorted(pairs[cid])
        rng.shuffle(eids)
        docs, qid = [], f'Q{index:03d}'
        for position, eid in enumerate(eids, 1):
            did = f'D{position:03d}'
            evidence = corpus[eid]
            docs.append(dict(documentId=did, **{k: evidence.get(k) for k in (
                'text', 'source', 'locator', 'layer', 'hexagramId', 'section', 'line', 'authorNames', 'workTitle')}))
            mappings.append(dict(queryId=qid, documentId=did, caseId=cid, evidenceId=eid))
        queries.append(dict(queryId=qid, question=case['currentQuestion'], coreQuestion=case['question'],
                            history=case['history'], cast=cast,
                            requestedTask=case['currentQuestion'], documents=docs))
    write(source/'blind-pool.json', dict(schemaVersion=1,
        task='Independently label each query/document pair relevant, irrelevant or unknown with a short reason. Judge support for the actual requested source/meaning, including meaningful contextual support. A real quotation alone does not establish relevance. Exact-author/object constraints matter; uncertain judgments remain unknown.',
        boundaries='No strategy, rank, score, original evidence label or expected answer is shown. This is AI relevance review, not expert Gold. Return every pair, including uncertainty.',
        outputFormat={'reviewer': 'AI identity', 'judgments': [{'queryId': 'Q001', 'documentId': 'D001', 'judgment': 'relevant|irrelevant|unknown', 'reason': 'short evidence-based explanation'}]},
        queryCount=len(queries), pairCount=len(mappings), queries=queries))
    write(source/'pool-key.json', dict(schemaVersion=1, sourceRowsSha256=digest(source/'rows.json'),
                                     shuffleSeed=4871, documents=mappings))
    return dict(queries=len(queries), pairs=len(mappings), uniqueDocuments=len({m['evidenceId'] for m in mappings}))


def rescore(folder, source, output, relevance_path=None):
    data, labels, freeze = read_frozen(folder)
    rows = json.loads((source/'rows.json').read_text())
    exact = json.loads((source/'exact.json').read_text())
    judgments = load_judgments(relevance_path)
    output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        label = labels[row['caseId']]
        row['queryType'] = 'explicit_metadata' if label.get('annotationCompleteness', {}).get('requestedMetadataExhaustive') else 'fuzzy_or_missing'
        review = judgments.get(row['caseId'], {}) if relevance_path else None
        row['scores'] = [score_hits(row['resultEvidenceIds'], label, k, review) for k in (1, 3, 5)]
    for row in exact:
        review = judgments.get(row['caseId'], {}) if relevance_path else None
        row['score'] = score_hits(row['resultEvidenceIds'], labels[row['caseId']], max(1, len(row['resultEvidenceIds'])), review)
    write(output/'rows.json', rows)
    write(output/'exact.json', exact)
    write(output/'summary.json', summarize(rows))
    write(output/'summary-by-query-type.json', summarize(rows, by_query_type=True))
    conflicts = [dict(caseId=cid, evidenceId=eid, reference='relevant', independent='irrelevant', resolution='unknown')
                 for cid, values in judgments.items() for eid, value in values.items()
                 if value == 'irrelevant' and eid in set(labels[cid].get('relevantEvidenceIds', []))]
    write(output/'conflicts.json', conflicts)
    write(output/'manifest.json', dict(sourceRows=str(source/'rows.json'), sourceRowsSha256=digest(source/'rows.json'),
        sourceExactSha256=digest(source/'exact.json'), runnerSha256=digest(__file__), datasetHashes=freeze['files'],
        relevanceFile=str(relevance_path) if relevance_path else None,
        relevanceSha256=digest(relevance_path) if relevance_path else None,
        changed='Scoring only. Original retrieval IDs, scores, queries, scope and timings preserved.',
        requestedTargetRecall='Explicit requested-object AI reference set only. Each alternative is a relevant object; all-alternative recall is distinct from sufficient any-of evidence-group coverage.',
        semanticRecall='Open semantic relevance remains non-exhaustive. Do not call requested-target recall general semantic recall.',
        precisionAtK='Denominator is K, including unfilled slots. Unjudged or conflicting records yield unknown and bounds, not false negatives.',
        conflicts='Authored positive vs independent irrelevant is preserved in conflicts.json and scored unknown. No forced agreement.'))
    print(json.dumps(dict(output=str(output), rows=len(rows), conflicts=len(conflicts)), ensure_ascii=False))


def run(folder, output, relevance_path=None):
    data, labels, freeze = read_frozen(folder)
    release = data['knowledgeRelease']
    corpus, manifest = load_release(release)
    lexical_index(release)  # All timings use an already-loaded corpus/index.
    by_id = {e['id']: e for e in corpus}
    judgments = load_judgments(relevance_path)
    cases = [case for case in data['cases'] if case['category'] == 'knowledge']
    output.mkdir(parents=True, exist_ok=False)
    rows, exact, pool = [], [], []
    for i, case in enumerate(cases):
        context = query_context(case, release)
        label = labels[case['caseId']]
        # Label access occurs only after the query/authorization is built.
        common = dict(caseId=case['caseId'], groupId=case['groupId'], split=case['split'], context=context,
                      queryType='explicit_metadata' if label.get('annotationCompleteness', {}).get('requestedMetadataExhaustive') else 'fuzzy_or_missing')
        arms = ['R1', 'R2', 'R3']
        for arm in arms[i % 3:] + arms[:i % 3]:
            row = common | lexical_search(context, arm, release)
            review = judgments.get(case['caseId'], {}) if relevance_path else None
            row['scores'] = [score_hits(row['resultEvidenceIds'], label, k, review) for k in (1, 3, 5)]
            rows.append(row)
        records, ids = [], []
        for args in context['exactRequests']:
            started = time.perf_counter()
            try:
                result = execute_tool('get_passages', args, release, context['scopeIds'])
                records.append(dict(args=args, status='ok' if result['evidence'] else 'empty', result=result,
                                    durationMs=(time.perf_counter()-started)*1000))
                ids.extend(e['id'] for e in result['evidence'])
            except ValueError as exc:
                records.append(dict(args=args, status='rejected', error=str(exc),
                                    durationMs=(time.perf_counter()-started)*1000))
        unique = list(dict.fromkeys(ids))
        exact.append(common | dict(eligible=bool(context['exactRequests']), requests=records,
                     resultEvidenceIds=unique, score=score_hits(unique, label, max(1, len(unique))),
                     metricScope='Unranked complete exact results, not BM25 or Recall@5.'))
        pooled = sorted({eid for row in rows if row['caseId'] == case['caseId'] for eid in row['resultEvidenceIds']})
        positives = set(label.get('relevantEvidenceIds', []))
        pool.append(dict(caseId=case['caseId'], question=case['currentQuestion'],
                         records=[dict(evidenceId=eid, text=by_id[eid]['text'], source=by_id[eid]['source'],
                                       locator=by_id[eid]['locator'], existingLabel='relevant' if eid in positives else 'unknown',
                                       reviewer='', judgment='', reason='') for eid in pooled]))
    write(output/'rows.json', rows)
    write(output/'exact.json', exact)
    write(output/'summary.json', summarize(rows))
    write(output/'relevance-pool.json', dict(annotationOrigin='Unscored pooling material; no new relevance judgments performed.', cases=pool))
    write(output/'manifest.json', dict(releaseId=release, evidenceSha256=manifest['evidenceSha256'],
        datasetHashes=freeze['files'], runnerSha256=digest(__file__), cases=len(cases),
        relevanceFile=str(relevance_path) if relevance_path else None,
        relevanceSha256=digest(relevance_path) if relevance_path else None,
        methods=dict(R1='Unique query/document bigram overlap + 12 point full phrase boost',
                     R2='Existing BM25 + same phrase boost',
                     R3='R2 + OR of production-parser requested-object metadata scopes'),
        authorization='All arms retain production evidence_scope derived from original text/mode/cast. No label expands scope.',
        retrieval='R1/R2 scan all layers within authorization; R3 uses existing author/work/layer/hexagram/anchor filters. They are offline retrieval ablations, not production tool defaults.',
        parserLimitation='Existing parser mistakes are retained, including unknown 哪一爻 being read as first line. No gold correction.',
        timing='Local warm corpus/index; authorization/metadata filtering, ranking and result selection; excludes query preparation, API and generation. Arm order rotates.',
        metricLimits='Non-exhaustive AI evidence groups support coverage/Hit. Strict Recall is unknown; unjudged retrieved records do not count as irrelevant. Precision bounds include unknowns.'))
    print(json.dumps(dict(output=str(output), cases=len(cases), rows=len(rows), exactEligible=sum(e['eligible'] for e in exact)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT/'evaluation/formal')
    parser.add_argument('--output', type=Path, default=ROOT/'evaluation/reports/formal-20261010/retrieval')
    parser.add_argument('--relevance', type=Path, help='Optional separately versioned pooled judgments; never used for retrieval.')
    parser.add_argument('--rescore', type=Path, help='Recompute metrics from existing raw retrieval rows, without rerunning retrieval.')
    parser.add_argument('--blind-pool', type=Path, help='Create shuffled independent review material from existing rows.')
    args = parser.parse_args()
    if args.blind_pool:
        print(json.dumps(blind_pool(args.data, args.blind_pool)))
    elif args.rescore:
        rescore(args.data, args.rescore, args.output, args.relevance)
    else:
        run(args.data, args.output, args.relevance)
