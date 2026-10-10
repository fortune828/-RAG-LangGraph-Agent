"""Offline formal metrics and anonymous reading packs; never runs a model.

Raw inputs, rows, traces and scores are immutable inputs. Derived artifacts live
under analysis/; human packs never contain strategy/model identities or scores.
"""
import argparse
import csv
import html
import itertools
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import ValidationError

from backend.citations import locate_original_quote
from backend.knowledge import normalize
from backend.models import Answer, Citation
from evaluation.semantic_reports import (
    PRICE_PATH, call_records, count_value, digest, field_usage, read_json,
    sum_complete, summarize_group, write_json,
)

BASE = Path(__file__).resolve().parent / 'reports/formal-20261010'
STAGES = ('smoke', 'development', 'test', 'rag', 'continuous')
DIMENSIONS = ('understanding', 'knowledge', 'integration', 'conversation', 'helpfulness')
RUBRIC_PATH = Path(__file__).resolve().parent / 'formal/rubric.json'


def records(value):
    return value['cases'] if isinstance(value, dict) else value


def wilson(successes, n, z=1.959963984540054):
    if not n:
        return dict(successes=successes, n=0, rate=None, low=None, high=None)
    p = successes / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return dict(successes=successes, n=n, rate=p, low=max(0., center - radius), high=min(1., center + radius))


def percentile(values, p):
    """Linear interpolation at (n-1)*p; retain n alongside every percentile."""
    values = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0)
    if not values:
        return None
    location = (len(values) - 1) * p
    lo, hi = math.floor(location), math.ceil(location)
    return values[lo] + (values[hi] - values[lo]) * (location - lo)


def cluster_bootstrap(observations, *, seed=1729, iterations=2000):
    """Resample whole groupId clusters; observations are (groupId, signed delta)."""
    if iterations < 1:
        raise ValueError('BOOTSTRAP_ITERATIONS_MUST_BE_POSITIVE')
    groups = defaultdict(list)
    for group_id, value in observations:
        if value is not None:
            groups[group_id].append(value)
    keys = sorted(groups, key=str)
    values = [value for group_id in keys for value in groups[group_id]]
    result = dict(n=len(values), groups=len(groups), estimate=statistics.mean(values) if values else None,
                  low=None, high=None, seed=seed, iterations=iterations,
                  method='Paired right-minus-left deltas; resample groupId with replacement, retain all observations per selected group.')
    if len(keys) < 2:
        return result | dict(limitation='Fewer than two independent groups; no interval estimated.')
    rng = random.Random(seed)
    samples = sorted(statistics.mean([value for _ in keys for value in groups[rng.choice(keys)]])
                     for _ in range(iterations))
    # Signed differences legitimately include negatives; percentile() is for latencies.
    def quantile(p):
        position = (len(samples) - 1) * p
        lo, hi = math.floor(position), math.ceil(position)
        return samples[lo] + (samples[hi] - samples[lo]) * (position - lo)
    return result | dict(low=quantile(.025), high=quantile(.975))


def schema_valid(value):
    try:
        Answer.model_validate(value)
        return True
    except ValidationError:
        return False


def quote_stats(value, evidence):
    """ID/text matching only; a supported-looking claim is not semantically scored."""
    counts = Counter(total=0, direct=0, resolved=0, invalid=0, unknown=0)
    if not isinstance(value, dict):
        return dict(counts)
    available = {item['id']: item for item in evidence} if evidence is not None else None
    paragraphs = value.get('paragraphs')
    for paragraph in paragraphs if isinstance(paragraphs, list) else []:
        if not isinstance(paragraph, dict):
            continue
        citations = paragraph.get('citations')
        for citation in citations if isinstance(citations, list) else []:
            counts['total'] += 1
            try:
                citation = Citation.model_validate(citation).model_dump()
            except ValidationError:
                counts['invalid'] += 1
                continue
            if available is None:
                counts['unknown'] += 1
                continue
            item = available.get(citation['evidenceId'])
            if item is None:
                counts['invalid'] += 1
                continue
            quoted = normalize(citation['quote'])
            if len(quoted) >= 2 and quoted in normalize(item['text']):
                counts['direct'] += 1
                continue
            located = locate_original_quote(item['text'], citation['quote'])
            try:
                Citation.model_validate(dict(citation, quote=located['quote']) if located else None)
            except ValidationError:
                counts['invalid'] += 1
            else:
                counts['resolved'] += 1
    return dict(counts)


def candidate_from_result(event):
    try:
        return json.loads(((event or {}).get('response') or {}).get('message', {}).get('content') or '')
    except (TypeError, ValueError):
        return None


def load_items(base, prices):
    items = []
    for stage in STAGES:
        directory = base / stage
        if not (directory / 'rows.json').exists():
            continue
        inputs = records(read_json(directory / 'inputs.json'))
        input_map = {case['caseId']: case for case in inputs}
        if len(inputs) != len(input_map):
            raise ValueError('DUPLICATE_CASE:' + stage)
        rows = read_json(directory / 'rows.json')
        manifest = read_json(directory / 'manifest.json') if (directory / 'manifest.json').exists() else {}
        traces = {}
        for path in sorted(directory.glob('*.jsonl')):
            events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if not events:
                continue
            identity = (events[0]['caseId'], events[0]['variant'])
            if identity in traces:
                raise ValueError('DUPLICATE_TRACE:' + str(identity))
            if any((e.get('caseId'), e.get('variant')) != identity for e in events):
                raise ValueError('TRACE_IDENTITY_CHANGED:' + str(path))
            traces[identity] = (path, events)
        seen = set()
        for row in rows:
            identity = (row['caseId'], row['arm'])
            if identity in seen:
                raise ValueError('DUPLICATE_CELL:' + str(identity))
            seen.add(identity)
            case_id, arm = identity
            case = row.get('input') or input_map[case_id]
            path, events = traces.get(identity, (None, []))
            trace = str(path.relative_to(base)) if path else None
            calls = call_records(stage, arm, trace, events, prices)
            # No legacy stage heuristics apply to these formally recorded runs.
            if any(c['effectiveStage'] not in ('route', 'generate', 'repair') for c in calls):
                raise ValueError('MISSING_MODEL_PHASE:' + str(identity))
            split = row.get('split', case.get('split', stage))
            if row.get('split') and case.get('split') and row['split'] != case['split']:
                raise ValueError('SPLIT_MISMATCH:' + str(identity))
            config = row.get('config', manifest.get('config'))
            items.append(dict(stage=stage, split=split, caseId=case_id, arm=arm,
                groupId=row.get('groupId', case.get('groupId', case_id)),
                scenario=row.get('scenario', case.get('scenario', row.get('category', case.get('category', 'unspecified')))),
                config=config, configKey=digest(config), graphVersion=row.get('graphVersion', manifest.get('graphVersion')),
                promptVersion=row.get('promptVersion', manifest.get('promptVersion')),
                inputHash=row.get('inputHash', row.get('inputSha256', digest(dict(
                    question=case['question'], mode=case.get('mode'), session=case['session'])))),
                input=case, row=row, events=events, calls=calls, trace=trace,
                traceSha256=digest(path.read_bytes()) if path else None, rowPresent=True))
        if set(traces) - seen:
            raise ValueError('TRACE_WITHOUT_ROW:' + stage)
    return items


def cell_metrics(item):
    row, events = item['row'], item['events']
    validations = [event for event in events if event['kind'] == 'validation']
    responses = [event for event in events if event['kind'] == 'model_result'
                 and event.get('stage') in ('generate', 'repair') and event.get('budgetCounted') is not False]
    first = next((e for e in responses if e['stage'] == 'generate'), None)
    last = responses[-1] if responses else None
    first_candidate, final_candidate = candidate_from_result(first), candidate_from_result(last)
    first_validation = next((e for e in validations if e.get('repairNumber', 0) == 0), None)
    final_validation = validations[-1] if validations else None
    if first_validation is not None:
        first_candidate = first_validation.get('candidate')
    if final_validation is not None:
        final_candidate = final_validation.get('candidate')
    if last is None and row.get('finalCandidate') is not None:
        final_candidate = row['finalCandidate']
    repair_calls = [c for c in item['calls'] if c['effectiveStage'] == 'repair' and c['budgetCounted'] is True]
    valid_output = row.get('answer') is not None and schema_valid(row['answer'])
    complete = row.get('status') == 'completed' and valid_output and bool(events) and not (
        final_validation and final_validation.get('errors'))
    first_evidence = first_validation.get('evidence') if first_validation is not None else None
    final_evidence = final_validation.get('evidence') if final_validation is not None else row.get('finalGenerationEvidence')
    preliminary = summarize_group([item])
    first_visible = isinstance(((first or {}).get('response') or {}).get('message'), dict)
    final_visible = isinstance(((last or {}).get('response') or {}).get('message'), dict)
    candidates = dict(firstSchemaValid=schema_valid(first_candidate) if first_visible or first_validation else None,
        finalSchemaValid=schema_valid(final_candidate) if final_visible or final_validation else None,
        firstResponseComplete=first.get('status') == 'ok' if first else None,
        firstValidationPassed=not first_validation.get('errors') if first_validation is not None else None,
        finalValidationPassed=not final_validation.get('errors') if final_validation is not None else None,
        firstQuotes=quote_stats(first_candidate, first_evidence), finalQuotes=quote_stats(final_candidate, final_evidence))
    if candidates['firstSchemaValid'] is not None:
        candidates['firstDeliverableSchema'] = candidates['firstSchemaValid'] and candidates['firstResponseComplete'] is True
    else:
        candidates['firstDeliverableSchema'] = None
    return {k: item[k] for k in ('stage', 'split', 'caseId', 'arm', 'groupId', 'scenario', 'config', 'configKey',
                                 'graphVersion', 'promptVersion', 'inputHash', 'trace', 'traceSha256')} | dict(
        status=row.get('status', 'unknown'), mechanicalComplete=complete, finalAnswerSchemaValid=valid_output,
        repairTriggered=bool(repair_calls), repairSuccessful=bool(repair_calls) and complete,
        durationMs=row.get('durationMs'), error=row.get('error'), errors=(final_validation or {}).get('errors', row.get('errors')),
        integrityIssues=['completed row has missing trace, invalid answer or failing validation'] if row.get('status') == 'completed' and not complete else [],
        **{k: preliminary[k] for k in ('calls', 'modelRequests', 'unresolvedRequests', 'reportedTokens', 'totalTokens',
            'unknownUsageCalls', 'estimatedUSD', 'reportedEstimateUSD', 'unknownCostCalls', 'toolRequests',
            'toolAttempts', 'toolOk', 'toolEmpty', 'toolRejected', 'repairs', 'repairRequests', 'quoteResolutions')}, **candidates)


def usage_summary(calls):
    relevant = [call for call in calls if call['budgetCounted'] is not False]
    usage = {field: field_usage(relevant, field) for field in ('total_tokens', 'prompt_tokens', 'completion_tokens',
        'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')}
    reasoning = []
    for call in relevant:
        reported = call.get('usage') or {}
        detail = reported.get('completion_tokens_details') or {}
        reasoning.append(count_value(detail.get('reasoning_tokens', reported.get('reasoning_tokens'))))
    usage['reasoning_tokens'] = dict(reportedSum=sum(n for n in reasoning if n is not None),
        unknownCalls=sum(n is None for n in reasoning), total=sum_complete(reasoning))
    return usage


def group_metrics(items, cells):
    result = summarize_group(items)
    latencies = [c['durationMs'] for c in cells if isinstance(c['durationMs'], (int, float)) and c['durationMs'] >= 0]
    terminal = [c for c in cells if c['status'] not in ('pending', 'not_run', 'running', 'unknown')]
    first_n = sum(c['firstSchemaValid'] is not None for c in cells)
    final_n = sum(c['finalSchemaValid'] is not None for c in cells)
    triggered = sum(c['repairTriggered'] for c in cells)
    result['reportedCompleted'] = result.pop('mechanicalCompleted')
    result['failed'] = sum(not c['mechanicalComplete'] for c in terminal)
    result['unfinished'] = len(cells) - len(terminal)
    result['budgetStopped'] = sum(c['status'] == 'budget_stopped' for c in cells)
    result.update(independentGroups=len({c['groupId'] for c in cells}), statuses=dict(Counter(c['status'] for c in cells)),
        mechanicalComplete=wilson(sum(c['mechanicalComplete'] for c in cells), len(cells)),
        mechanicalAmongTerminal=wilson(sum(c['mechanicalComplete'] for c in terminal), len(terminal)),
        firstSchema=wilson(sum(c['firstSchemaValid'] is True for c in cells), first_n),
        firstDeliverableSchema=wilson(sum(c['firstDeliverableSchema'] is True for c in cells), first_n),
        finalSchema=wilson(sum(c['finalSchemaValid'] is True for c in cells), final_n),
        repair=dict(triggered=triggered, successful=sum(c['repairSuccessful'] for c in cells),
                    success=wilson(sum(c['repairSuccessful'] for c in cells), triggered)),
        p50Ms=percentile(latencies, .5), p95Ms=percentile(latencies, .95), latencyN=len(latencies),
        meanMs=statistics.mean(latencies) if latencies else None,
        minMs=min(latencies) if latencies else None, maxMs=max(latencies) if latencies else None,
        missingLatency=len(cells) - len(latencies),
        usage=usage_summary([call for item in items for call in item['calls']]))
    for field in ('firstQuotes', 'finalQuotes'):
        totals = {key: sum(c[field][key] for c in cells) for key in ('total', 'direct', 'resolved', 'invalid', 'unknown')}
        result[field] = totals | dict(matched=totals['direct'] + totals['resolved'],
            rate=(totals['direct'] + totals['resolved']) / (totals['total'] - totals['unknown']) if totals['total'] > totals['unknown'] else None)
    return result


def paired_results(cells):
    """Descriptive within-case comparisons. No independent-round significance claim."""
    groups = defaultdict(list)
    for cell in cells:
        if cell['stage'] != 'continuous':
            groups[(cell['stage'], cell['split'], cell['configKey'], cell['graphVersion'], cell['promptVersion'])].append(cell)
    results = []
    for key, group in sorted(groups.items(), key=lambda pair: str(pair[0])):
        arms = sorted({cell['arm'] for cell in group})
        for left, right in itertools.combinations(arms, 2):
            lefts = {c['caseId']: c for c in group if c['arm'] == left}
            rights = {c['caseId']: c for c in group if c['arm'] == right}
            shared = lefts.keys() & rights.keys()
            incompatible = sorted(c for c in shared if lefts[c]['inputHash'] != rights[c]['inputHash'])
            pending = sorted(c for c in shared if lefts[c]['status'] in ('pending', 'not_run', 'running', 'unknown') or rights[c]['status'] in ('pending', 'not_run', 'running', 'unknown'))
            matched = sorted(shared - set(incompatible) - set(pending))
            differences = {field: [rights[c][field] - lefts[c][field] for c in matched
                if rights[c][field] is not None and lefts[c][field] is not None]
                for field in ('calls', 'totalTokens', 'durationMs', 'estimatedUSD')}
            wins = sum(rights[c]['mechanicalComplete'] and not lefts[c]['mechanicalComplete'] for c in matched)
            losses = sum(lefts[c]['mechanicalComplete'] and not rights[c]['mechanicalComplete'] for c in matched)
            results.append(dict(stage=key[0], split=key[1], configKey=key[2], graphVersion=key[3], promptVersion=key[4],
                left=left, right=right, n=len(matched), independentGroups=len({lefts[c]['groupId'] for c in matched}),
                rightWins=wins, leftWins=losses, ties=len(matched)-wins-losses,
                mechanicalRateDifference=(wins-losses)/len(matched) if matched else None,
                mechanicalClusterCI=cluster_bootstrap([(lefts[c]['groupId'],
                    int(rights[c]['mechanicalComplete']) - int(lefts[c]['mechanicalComplete'])) for c in matched]),
                differences={field: dict(n=len(values), mean=statistics.mean(values) if values else None,
                    median=statistics.median(values) if values else None) for field, values in differences.items()},
                missingLeft=sorted(rights.keys()-lefts.keys()), missingRight=sorted(lefts.keys()-rights.keys()),
                incompatibleInputs=incompatible, pendingCases=pending))
    return results


def flatten(value, prefix=''):
    result = {}
    for key, item in value.items():
        name = prefix + str(key)
        if isinstance(item, dict):
            result.update(flatten(item, name + '.'))
        elif isinstance(item, list):
            result[name] = json.dumps(item, ensure_ascii=False)
        else:
            result[name] = item
    return result


def write_csv(path, rows):
    rows = [flatten(row) for row in rows]
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ['n']
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def review_pairs(reviews):
    groups = defaultdict(list)
    for review in reviews:
        if review['stage'] != 'continuous':
            for dimension, score in review['scores'].items():
                groups[(review['stage'], review['split'], review['configKey'], review['graphVersion'], review['promptVersion'],
                        review['reviewerType'], digest(review['reviewer']), review['target'], dimension)].append((review, score))
    pairs = []
    for key, group in sorted(groups.items(), key=lambda pair: str(pair[0])):
        arms = sorted({r['arm'] for r, _ in group})
        for left, right in itertools.combinations(arms, 2):
            l = {r['caseId']: (r, score) for r, score in group if r['arm'] == left}
            r = {r['caseId']: (r, score) for r, score in group if r['arm'] == right}
            shared = l.keys() & r.keys()
            matched = [case for case in sorted(shared) if l[case][1] is not None and r[case][1] is not None
                       and l[case][0]['inputHash'] == r[case][0]['inputHash']]
            deltas = [(l[case][0]['groupId'], r[case][1] - l[case][1]) for case in matched]
            pairs.append(dict(stage=key[0], split=key[1], configKey=key[2], graphVersion=key[3],
                promptVersion=key[4], reviewerType=key[5], reviewer=group[0][0]['reviewer'], target=key[7], dimension=key[8],
                left=left, right=right, n=len(matched), rightWins=sum(value > 0 for _, value in deltas),
                leftWins=sum(value < 0 for _, value in deltas), ties=sum(value == 0 for _, value in deltas),
                clusterCI=cluster_bootstrap(deltas), incompleteOrIncompatible=sorted(shared-set(matched)),
                missingLeft=sorted(r.keys()-l.keys()), missingRight=sorted(l.keys()-r.keys())))
    return pairs


def merge_reviews(specifications, cells, base):
    """Accept formal rubric records or explicit score maps; retain original records."""
    cell_map = {(c['stage'], c['caseId'], c['arm']): c for c in cells}
    merged, seen = [], set()
    for specification in specifications:
        specification = dict(specification) if isinstance(specification, dict) else {'scores': str(specification)}
        path = Path(specification['scores'])
        if not path.is_absolute():
            path = base / path
        if path.suffix == '.csv':
            with path.open(newline='') as stream:
                payload = dict(results=list(csv.DictReader(stream)))
        else:
            payload = read_json(path)
            if isinstance(payload, list):
                payload = {'results': payload}
            elif 'answerId' in payload:
                payload = {'results': [payload]}
        key_file = specification.get('key', payload.get('keyPath'))
        keys, key_hash = {}, None
        if key_file:
            key_path = Path(key_file)
            if not key_path.is_absolute():
                key_path = base / key_path
            keys = {entry['answerId']: entry for entry in read_json(key_path)}
            key_hash = digest(key_path.read_bytes())
        for raw in payload['results']:
            # Blank placeholders in the human sheet are not submitted human reviews.
            if path.suffix == '.csv' and not any(value for key, value in raw.items() if key != 'answerId'):
                continue
            identity = keys.get(raw.get('answerId'), raw)
            cell = cell_map.get(tuple(identity.get(k) for k in ('stage', 'caseId', 'arm')))
            if cell is None:
                raise ValueError('REVIEW_CELL_NOT_FOUND:' + repr(identity))
            reviewer = raw.get('reviewer') or raw.get('reviewerId') or payload.get('reviewer') or specification.get('reviewer') or 'unspecified'
            inferred_kind = reviewer.get('kind', 'unknown').lower() if isinstance(reviewer, dict) else 'unknown'
            kind = specification.get('reviewerType', payload.get('reviewerType', inferred_kind))
            evaluations = raw.get('evaluations')
            if evaluations is None:
                evaluations = [dict(target=raw.get('target') or 'delivered',
                    scores=raw.get('scores', {k: raw.get(k) for k in DIMENSIONS if k in raw}))]
            for evaluation in evaluations:
                target = evaluation.get('target', 'delivered')
                score_map = evaluation.get('scores', evaluation.get('dimensions', {}))
                if not isinstance(score_map, dict):
                    raise ValueError('REVIEW_SCORES_MUST_BE_MAPPING:' + str(path))
                numeric = {}
                for dimension, score in score_map.items():
                    score = score.get('value') if isinstance(score, dict) else score
                    if score is None or score == '' or score == 'N/A':
                        numeric[dimension] = None
                    elif type(score) in (int, float) and score in (0, 1, 2):
                        numeric[dimension] = int(score)
                    elif path.suffix == '.csv' and score in ('0', '1', '2'):
                        numeric[dimension] = int(score)
                    else:
                        raise ValueError('INVALID_REVIEW_SCORE:' + dimension)
                identity = (cell['stage'], cell['caseId'], cell['arm'], kind, digest(reviewer), target)
                if identity in seen:
                    raise ValueError('DUPLICATE_REVIEW:' + repr(identity))
                seen.add(identity)
                merged.append({key: cell[key] for key in ('stage', 'split', 'caseId', 'arm', 'groupId',
                    'configKey', 'graphVersion', 'promptVersion', 'inputHash')} | dict(target=target, reviewerType=kind, reviewer=reviewer,
                    source=str(path), sourceSha256=digest(path.read_bytes()), keySha256=key_hash,
                    scores=numeric, original=raw, mechanicalComplete=cell['mechanicalComplete']))
    aggregates = defaultdict(list)
    for review in merged:
        for dimension, value in review['scores'].items():
            aggregates[(review['stage'], review['split'], review['arm'], review['configKey'], review['graphVersion'],
                        review['promptVersion'], review['reviewerType'], json.dumps(review['reviewer'], ensure_ascii=False, sort_keys=True),
                        review['target'], dimension)].append(value)
    summaries = [dict(stage=key[0], split=key[1], arm=key[2], configKey=key[3], graphVersion=key[4], promptVersion=key[5],
        reviewerType=key[6], reviewer=json.loads(key[7]), target=key[8], dimension=key[9],
        scored=sum(v is not None for v in values), unknown=sum(v is None for v in values),
        mean=statistics.mean([v for v in values if v is not None]) if any(v is not None for v in values) else None)
        for key, values in sorted(aggregates.items(), key=lambda pair: str(pair[0]))]
    return dict(results=merged, summaries=summaries, pairs=review_pairs(merged),
        limitation='AI, human, candidate and delivered scores remain separate; original errors/faithfulness/taskSuccess retained without automatic composite success.')


def recompute(base=BASE, price_path=PRICE_PATH, *, review_paths=()):
    base, price_path = Path(base), Path(price_path)
    prices = read_json(price_path)
    items = load_items(base, prices)
    cells = [cell_metrics(item) for item in items]
    grouped = defaultdict(list)
    for index, item in enumerate(items):
        grouped[(item['stage'], item['split'], item['arm'], item['configKey'], item['graphVersion'], item['promptVersion'])].append(index)
    groups = [dict(stage=key[0], split=key[1], arm=key[2], configKey=key[3], graphVersion=key[4], promptVersion=key[5],
        **group_metrics([items[i] for i in indexes], [cells[i] for i in indexes]))
        for key, indexes in sorted(grouped.items(), key=lambda pair: str(pair[0]))]
    calls = [call for item in items for call in item['calls']]
    dispatched = [call for call in calls if call['budgetCounted'] is True]
    unknown_dispatch = sum(call['budgetCounted'] is None for call in calls)
    usage = usage_summary(calls)
    estimates = [call['estimate']['usd'] for call in dispatched]
    accounted = sum_complete([count_value((call['usage'] or {}).get('total_tokens'))
        if count_value((call['usage'] or {}).get('total_tokens')) is not None else call['unknownReservation'] for call in dispatched])
    ledger = read_json(base / 'ledger.json') if (base / 'ledger.json').exists() else None
    ledger_values = dict(calls=len(dispatched), reportedTokens=usage['total_tokens']['reportedSum'],
        missingUsageCalls=field_usage(dispatched, 'total_tokens')['unknownCalls'], accountedTokens=accounted)
    comparisons = {key: dict(recorded=ledger.get(key), recomputed=value,
        match=ledger.get(key) == value if value is not None else None) for key, value in ledger_values.items()} if ledger else {}
    metrics = dict(protocol='formal-offline-stats-v1', groups=groups, pairs=paired_results(cells),
        accounting=dict(calls=len(dispatched), requests=len(calls), unknownDispatch=unknown_dispatch,
            reportedTokens=usage['total_tokens']['reportedSum'], unknownUsageCalls=usage['total_tokens']['unknownCalls'], usage=usage),
        ledger=dict(recorded=ledger, comparisons=comparisons,
            consistent=all(item['match'] is True for item in comparisons.values()) if comparisons else None),
        cost=dict(currency=prices['currency'], estimatedUSD=None if unknown_dispatch else sum_complete(estimates),
            reportedEstimateUSD=sum(v for v in estimates if v is not None),
            unknownCalls=unknown_dispatch + sum(v is None for v in estimates), priceTable=str(price_path),
            priceTableSha256=digest(price_path.read_bytes()), observedDate=prices['observedDate'],
            limitation='Same-day weekend list-price estimate, not a bill. Other dates or missing cache components remain unknown. Reasoning is included in completion; never charged twice.'),
        integrityIssues=[dict(stage=c['stage'], caseId=c['caseId'], arm=c['arm'], issues=c['integrityIssues']) for c in cells if c['integrityIssues']],
        definitions=dict(mechanical='Completed row with schema-valid answer, trace and no final validation errors. Pending cells remain in planned denominator.',
            schema='Pydantic structure only; parseable length-truncated JSON can be schema-valid but not deliverable. No-response cells are N/A, separately retained as incomplete.',
            quotes='Known ID and source text match (including logged deterministic source-span restoration), not semantic entailment.',
            repair='Actually budget-counted repair calls; successful means final mechanical completion, not semantic correctness.',
            latency='End-to-end recorded cell duration; all recorded outcomes, including failures. P50/P95 linear interpolation at (n-1)*p.',
            intervals='95% Wilson describes input rounds only. Paired deltas additionally use whole-groupId cluster bootstrap (seed 1729, 2000 resamples); fewer than two groups has no interval. Neither quotes nor related turns are treated as independent groups.',
            pairing='Same stage/split/config/version/case/input hash; terminal outcomes only. Right minus left. Continuous self-generated histories excluded.',
            scopes='No pooled production success rate across stages or arms. Accounting total alone spans all recorded experiments.'))
    destination = base / 'analysis'
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'metrics.json', metrics)
    write_json(destination / 'usage.json', dict(calls=calls, usage=usage))
    write_json(destination / 'cells.json', cells)
    write_json(destination / 'pairs.json', metrics['pairs'])
    write_json(destination / 'reviews.json', merge_reviews(review_paths, cells, base))
    write_csv(destination / 'cells.csv', cells)
    write_csv(destination / 'groups.csv', groups)
    write_csv(destination / 'pairs.csv', metrics['pairs'])
    return metrics


def safe_name(value):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('INVALID_EXPORT_NAME')
    return value


def clean_answer(answer):
    if not isinstance(answer, dict):
        return answer
    return {key: answer[key] for key in ('status', 'summary', 'paragraphs', 'limitations') if key in answer}


def citation_occurrences(answer, target):
    occurrences = []
    if not isinstance(answer, dict):
        return occurrences
    for p, paragraph in enumerate(answer.get('paragraphs', [])):
        if not isinstance(paragraph, dict):
            continue
        for c, citation in enumerate(paragraph.get('citations', [])):
            if isinstance(citation, dict):
                occurrences.append(dict(occurrenceId=f'{target}.p{p}.c{c}', target=target,
                    location=f'paragraphs[{p}].citations[{c}]', paragraphText=paragraph.get('text'),
                    **{key: citation.get(key) for key in ('evidenceId', 'quote', 'claim')}))
    return occurrences


def review_material(row, case, answer_id, references=()):
    """Keep delivery and internal candidates distinct; never imply hidden evidence was seen."""
    case = row.get('input') or case
    session = case['session']
    delivered = clean_answer(row.get('visibleAnswer', row.get('answer') if row.get('status') == 'completed' else None))
    candidate = clean_answer(row.get('finalCandidate')) if row.get('status') != 'completed' else None
    fallback = clean_answer(row.get('answer')) if row.get('status') != 'completed' and row.get('answer') != delivered else None
    actual = row.get('finalGenerationEvidence', row.get('evidence', [])) or []
    actual_ids = {e['id'] for e in actual}
    # Keep only source records, never expectation text or retrieval gold labels.
    evidence_fields = ('id', 'text', 'textOriginal', 'hexagramId', 'name', 'section', 'line', 'layer',
                       'workId', 'workTitle', 'authorIds', 'authorNames', 'editionId', 'editionLabel',
                       'sourceId', 'source', 'locator', 'version', 'releaseId', 'anchorIds',
                       'reviewStatus', 'review', 'commentsOn')
    extras = [{key: e[key] for key in evidence_fields if key in e} | dict(referenceOnly=True, availableToAnswer=False)
              for e in references if e['id'] not in actual_ids]
    visible_kind = row.get('visibleKind') or ('insufficient_or_refusal' if delivered and delivered.get('status') == 'insufficient'
                    else 'substantive_answer' if delivered else 'none')
    history = [dict(question=turn.get('question'), answer=clean_answer(turn.get('answer')),
                    evidence=turn.get('evidence', [])) for turn in session.get('turns', [])]
    return dict(answerId=answer_id, scenario=case.get('scenario', case.get('category', 'unspecified')),
        question=case['question'], coreQuestion=session['question'], history=history,
        cast={k:v for k,v in session['cast'].items() if k not in ('id', 'createdAt')},
        answer=delivered, candidate=candidate, candidateText=row.get('finalCandidateText') if candidate is None else None,
        internalFallback=fallback, internalFallbackScope='Graph-internal diagnostic only; no evidence of API delivery.' if fallback else None,
        evidence=actual, status=row.get('status'),
        delivery=dict(mechanicalStatus=row.get('status') if row.get('status') in ('completed', 'failed', 'validation_failed') else 'failed',
            candidateOnly=delivered is None and candidate is not None, visibleKind=visible_kind,
            technicalFailure=row.get('status') != 'completed',
            providedFailureNote='未完成可交付回答；内部候选不是实际交付。' if delivered is None else None),
        citationClaims=citation_occurrences(delivered, 'delivered') + citation_occurrences(candidate, 'candidate'),
        referenceOnlyEvidence=extras,
        evidenceScope='evidence 是该回答实际可见资料；referenceOnlyEvidence 仅授权评阅查证，未提供给被测生成，不得视为该回答已检索或已引用。')


def material_markdown(materials):
    lines = ['# 匿名完整回答材料', '', '原始输入、完整答复及原文保留。机械状态不代表语义质量；交付与候选分别评阅。', '']
    for material in materials:
        lines += ['## ' + material['answerId'] + ' · ' + material['scenario'], '',
            '**核心问题：** ' + material['coreQuestion'], '', '**当前问题：** ' + material['question'], '']
        for label, value in [('历史', material['history']), ('程序卦象', material['cast']), ('交付状态', material['delivery'])]:
            lines += ['### ' + label, '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```', '']
        for label, key in [('实际交付正文与引用', 'answer'), ('未交付候选', 'candidate'),
                           ('截断或无法解析的原始候选文本', 'candidateText'), ('内部 fallback（不证明实际交付）', 'internalFallback'),
                           ('全部引用 claim 位置', 'citationClaims'), ('生成实际可见原文', 'evidence'),
                           ('仅供评阅查证的补充原文（生成未见）', 'referenceOnlyEvidence')]:
            value = material[key]
            if value is not None:
                lines += ['### ' + label, '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```', '']
        lines += [material['evidenceScope'], '']
    return '\n'.join(lines)


def batch_sizes(n, preferred=10, *, allow_small=False):
    if not 8 <= preferred <= 12:
        raise ValueError('BATCH_SIZE_MUST_BE_8_TO_12')
    if n < 8 and allow_small:
        return [n] if n else []
    minimum, maximum = math.ceil(n / 12), n // 8
    if not n or minimum > maximum:
        raise ValueError('CANNOT_PARTITION_INTO_8_TO_12:' + str(n))
    batches = min(maximum, max(minimum, round(n / preferred)))
    quotient, remainder = divmod(n, batches)
    return [quotient + (index < remainder) for index in range(batches)]


def compact_review_guide(rubric):
    lines = ['# 匿名独立评阅要求', '',
        '使用本包 materials.json 或 materials.md。只评所给匿名输出，不查策略或生产代码，不调用被测 API，不读其他评阅分数。',
        '本说明缩减输出冗余，不改变随包 rubric.json 的冻结锚点、N/A、taskSuccess 和引用支持口径；完整口径优先。', '',
        '输出一个 JSON 对象：reviewer 为身份对象（reviewerId、kind=AI、expertStatus=non_expert、blind=true、independent=true），results 为逐 answerId 记录数组。',
        '每条保留 answerId、rubricVersion、taskSuccess、taskSuccessReason、evaluations；存在未交付候选时另写 candidateTaskSuccess/Reason。未交付的实际 taskSuccess=false，不以候选成功替代。',
        'evaluations 按 target=delivered/candidate 分开。每维 dimensions.<name> 写 {value:0/1/2/null,rationale:具体理由}，null 另写 naReason；理由引用原句或位置。五维不求总分。', '', '## 五维', '']
    for key, dimension in rubric['scoring']['dimensions'].items():
        lines += ['- ' + key + '：' + dimension['question'],
                  '  0：' + dimension['anchors']['0'] + ' 1：' + dimension['anchors']['1'] + ' 2：' + dimension['anchors']['2']]
    lines += ['', '## 错误和证据支持', '',
        '每个 evaluation 的 errors 保留实际错误的 type、quote（答复原句）、location、reason、certainty；有条件时注明 severity 与 dimensions。只标实际问题，不为填表造错；未知标签可保留。',
        'faithfulness.claims 覆盖材料 citationClaims 中每个 occurrence，并补明显未引用的传统断言。混合 claim 拆成传统/现代命题；同义重复可合并但列出全部 occurrenceIds。',
        '每条写 claimId、occurrenceIds（未引为空）、assertion、quote（答复原句）、location、layer、origin、sourceIds、supportLabel、rationale、contradiction、quotationMatch；有原文支持时附 evidenceQuote。',
        'supportLabel 使用 supported/partial/unsupported/unknown。纯现代类比等不进入传统分母时才用 null，并写 excludedFromTraditionalDenominator=true 及 exclusionReason。真实引文不自动证明 claim 含义。',
        'faithfulness.coverage 保留 citationClaimOccurrencesTotal/Reviewed、traditionalAssertionsIdentified/Reviewed、uncitedTraditionalAssertionsIdentified/Reviewed、uncitedDiscoveryExhaustive=false 和 notes。不能把未检查的 claim 算已核对。',
        'evidence 是生成实际看见的资料。末尾 referenceOnlyEvidence 可辅助独立查证，但生成未见：不能把它算作已检索、已引用，不能据此把诚实未取得材料说成虚构缺失。引用支持若只来自补充参考，注明 supportEvidenceScope=reference_only。',
        '无实质交付时对应五维 N/A 有原因；候选可另外评分。内部 fallback 仅是引擎诊断，不证明用户看到了它。', '',
        '不使用字数、段落数、引用数或固定句式作为自然度/帮助评分指标。AI 评阅不是人工或专家 Gold。', '',
        '错误类型：' + ', '.join(rubric['errors']['types']), '']
    return '\n'.join(lines)


def export_blind(stage, *, base=BASE, name=None, arms=None, case_ids=None, batch_size=10,
                 seed=1729, references=None, secondary=False):
    """Select only a completed requested stage; never opens other stage inputs."""
    base, stage = Path(base), safe_name(stage)
    name = safe_name(name or (stage + '-secondary' if secondary else stage))
    destination, key_path = base / 'blind' / name, base / 'analysis' / (name + '-key.json')
    if destination.exists() or key_path.exists():
        raise FileExistsError('EXISTING_BLIND_PACK:' + name)
    cases = {case['caseId']: case for case in records(read_json(base / stage / 'inputs.json'))}
    selected = set(case_ids) if case_ids is not None else set(cases)
    if selected - cases.keys():
        raise ValueError('UNKNOWN_REVIEW_CASES:' + repr(sorted(selected - cases.keys())))
    rows = [row for row in read_json(base / stage / 'rows.json') if row['caseId'] in selected and (arms is None or row['arm'] in arms)]
    if any(row.get('status') in ('pending', 'not_run', 'running') for row in rows):
        raise ValueError('REVIEW_STAGE_NOT_FINISHED')
    sizes = batch_sizes(len(rows), batch_size, allow_small=stage == 'smoke')
    if isinstance(references, (str, Path)):
        references = read_json(Path(references))
    if isinstance(references, dict) and isinstance(references.get('cases'), list):
        references = {case['caseId']: case.get('sourceEvidence', []) for case in references['cases'] if case['caseId'] in selected}
    references = references or {}
    rubric = read_json(RUBRIC_PATH)
    # Deterministic shuffle, opaque IDs and an independent namespace for re-review.
    rows.sort(key=lambda row: (row['caseId'], row['arm']))
    random.Random(seed).shuffle(rows)
    materials, keys = [], []
    for row in rows:
        answer_id = 'Q' + digest(['formal-blind-v1', name, seed, row['caseId'], row['arm']])[:12]
        material = review_material(row, cases[row['caseId']], answer_id, references.get(row['caseId'], []))
        if material['candidate'] is None and row.get('status') != 'completed':
            trace_path = base / stage / (row['caseId'] + '-' + row['arm'] + '.jsonl')
            if trace_path.exists():
                events = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
                responses = [e for e in events if e['kind'] == 'model_result' and e.get('stage') in ('generate', 'repair')]
                if responses:
                    material['candidateText'] = ((responses[-1].get('response') or {}).get('message') or {}).get('content')
        materials.append(material)
        keys.append(dict(answerId=answer_id, stage=stage, caseId=row['caseId'], arm=row['arm']))
    destination.mkdir(parents=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    offset, packages = 0, []
    for index, size in enumerate(sizes, 1):
        batch_name = f'batch-{index:03}'
        directory = destination / batch_name
        directory.mkdir()
        batch = materials[offset:offset + size]
        for key in keys[offset:offset + size]:
            key['batch'] = batch_name
        offset += size
        write_json(directory / 'materials.json', batch)
        (directory / 'materials.md').write_text(material_markdown(batch))
        write_json(directory / 'rubric.json', rubric)
        (directory / 'review-guide.md').write_text(compact_review_guide(rubric))
        manifest = dict(batchId=batch_name, n=len(batch), answerIds=[m['answerId'] for m in batch],
            role='independent_secondary' if secondary else 'primary', rubricVersion=rubric['version'],
            rubricSha256=digest(rubric), materialsSha256=digest(batch), scoresPresent=False)
        write_json(directory / 'manifest.json', manifest)
        packages.append(dict(batch=batch_name, n=len(batch), materials=str(directory / 'materials.json'),
            guide=str(directory / 'review-guide.md')))
    write_json(key_path, keys)
    write_json(destination / 'index.json', dict(n=len(materials), batches=[dict(batch=p['batch'], n=p['n']) for p in packages],
        role='independent_secondary' if secondary else 'primary', scoresPresent=False))
    return dict(n=len(materials), packages=packages, key=str(key_path))


def export_secondary(stage, case_ids, *, base=BASE, name=None, **options):
    return export_blind(stage, base=base, case_ids=case_ids, name=name or stage + '-secondary',
                        secondary=True, **options)


def export_human(stage, *, base=BASE, name=None, case_ids=None, references=None):
    """Full unabridged reading material, ordered by scenario and blinded within it."""
    base, stage = Path(base), safe_name(stage)
    name = safe_name(name or stage)
    destination, key_path = base / 'human' / name, base / 'analysis' / (name + '-key.json')
    if destination.exists() or key_path.exists():
        raise FileExistsError('EXISTING_REVIEW_PACK:' + name)
    cases = {case['caseId']: case for case in records(read_json(base / stage / 'inputs.json'))}
    if isinstance(references, (str, Path)):
        references = read_json(Path(references))
    if isinstance(references, dict) and isinstance(references.get('cases'), list):
        references = {case['caseId']: case.get('sourceEvidence', []) for case in references['cases']}
    references = references or {}
    rubric = read_json(RUBRIC_PATH)
    rows = read_json(base / stage / 'rows.json')
    if case_ids is not None:
        missing = set(case_ids) - set(cases)
        if missing:
            raise ValueError('UNKNOWN_REVIEW_CASES:' + repr(sorted(missing)))
        rows = [row for row in rows if row['caseId'] in case_ids]
    rows.sort(key=lambda row: (cases[row['caseId']].get('scenario', cases[row['caseId']].get('category', 'unspecified')),
        digest(['formal-human-v1', stage, row['caseId'], row['arm']])))
    materials, keys = [], []
    for index, row in enumerate(rows):
        answer_id = f'A{index+1:03}'
        materials.append(review_material(row, cases[row['caseId']], answer_id, references.get(row['caseId'], [])))
        keys.append(dict(answerId=answer_id, stage=stage, caseId=row['caseId'], arm=row['arm']))
    destination.mkdir(parents=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'materials.json', materials)
    write_json(destination / 'rubric.json', rubric)
    write_json(key_path, keys)
    introduction = '每份按场景排列，策略、模型和既有评分已隐藏。阅读完整问题、历史与原文后独立评分；机械完成不代表语义正确。失败候选若有仅供诊断，不是已交付回答。'
    markdown = ['# 匿名完整回答材料', '', introduction, '']
    articles = []
    for material in materials:
        title = material['answerId'] + ' · ' + material['scenario']
        markdown += ['## ' + title, '', '**核心问题：** ' + material['coreQuestion'], '', '**当前问题：** ' + material['question'], '']
        sections = [('历史上下文', material['history']), ('程序卦象', material['cast']), ('实际交付状态', material['delivery'])]
        for label, value in sections:
            markdown += ['### ' + label, '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```', '']
        answer = material['answer']
        texts = [paragraph['text'] for paragraph in (answer or {}).get('paragraphs', [])]
        markdown += ['### 本轮正文', ''] + [text + '\n' for text in texts]
        if not answer:
            markdown += ['未完成可交付回答。', '']
        supplements = [('完整回答与引用', answer), ('未交付候选（若有）', material['candidate']),
            ('内部 fallback（不证明实际交付）', material['internalFallback']), ('生成实际可见原文', material['evidence']),
            ('仅供查证的补充原文（生成未见）', material['referenceOnlyEvidence'])]
        for label, value in supplements:
            if value is not None:
                markdown += ['### ' + label, '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```', '']
        details = ''.join('<details><summary>' + html.escape(label) + '</summary><pre>' +
            html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'
            for label, value in sections + supplements if value is not None)
        articles.append('<article><h2>' + html.escape(title) + '</h2><p><b>核心问题：</b>' + html.escape(material['coreQuestion']) +
            '</p><p><b>当前问题：</b>' + html.escape(material['question']) + '</p><section>' +
            (''.join('<p>' + html.escape(text).replace('\n', '<br>') + '</p>' for text in texts) if answer else '<p>未完成可交付回答。</p>') + '</section>' + details + '</article>')
    (destination / 'materials.md').write_text('\n'.join(markdown))
    (destination / 'materials.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>匿名完整回答材料</title>'
        '<style>body{max-width:960px;margin:2rem auto;padding:0 1rem;font:17px/1.8 sans-serif}article{border-top:1px solid #ccc;margin-top:2rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}summary{cursor:pointer}section{background:#f6f7f8;padding:1rem}</style>'
        '<h1>匿名完整回答材料</h1><p>' + html.escape(introduction) + '</p>' + ''.join(articles) + '</html>')
    fields = rubric['humanCsv']['columns']
    with (destination / 'scores.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows({'answerId': item['answerId']} for item in materials)
    instructions = '''# 人工试评说明

先读 materials.md 或 materials.html；以当前问题、完整历史、卦象和提供原文为依据，填写 scores.csv。不要读取分析目录中的解盲键或既有 AI 分数。

五维均为 0（明显不满足）、1（部分满足）、2（明确满足）：

- understanding：理解本轮问题与用户更正，区分已知事实与推测。
- knowledge：传统解释忠于提供原文及作者归属；真实引用不自动证明解释正确。
- integration：卦意参与分析且联系自然，不牵强映射或只作装饰。
- conversation：自然交流，篇幅和深度适合实际问题，追问承接上文。
- helpfulness：回应真实矛盾，分析条件、取舍与行动的实际帮助。

表中只有 answerId 为匿名占位，未代表已有人工评分。每次实际评阅填写 reviewerId、target（delivered 或 candidate）和 rubricVersion，五维分别填 0/1/2；不适用填 N/A，并在该维 NAReason/Reason 说明。候选可单独增一行评分，不能混作已交付体验。段数、字数、引用条数不作为评分标准。无可见回答时分数不伪填 0，taskSuccess 为 false；未知与未提交保持区别。

完整冻结口径见同目录 rubric.json。errorsJson 使用其 errors.types 中的标签，附原句、位置与原因；保留有证据的新标签，不因不在目录就丢弃。当前标签：

''' + ', '.join(rubric['errors']['types']) + '\n\n这是人工表，不会自动填入 AI 评分。AI、人工和机械指标分别汇总。\n'
    (destination / 'rubric.md').write_text(instructions)
    return dict(n=len(materials), materials=str(destination / 'materials.json'), markdown=str(destination / 'materials.md'),
        html=str(destination / 'materials.html'), scores=str(destination / 'scores.csv'), key=str(key_path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=('summarize', 'export', 'blind', 'secondary'))
    parser.add_argument('stage', nargs='?', choices=STAGES)
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--prices', type=Path, default=PRICE_PATH)
    parser.add_argument('--name')
    parser.add_argument('--arms', nargs='+')
    parser.add_argument('--case-ids', nargs='+')
    parser.add_argument('--references', type=Path)
    parser.add_argument('--batch-size', type=int, default=10)
    parser.add_argument('--seed', type=int, default=1729)
    parser.add_argument('--reviews', type=Path, help='JSON list of explicit score/key/reviewerType import specifications')
    args = parser.parse_args()
    if args.action in ('export', 'blind', 'secondary'):
        if not args.stage:
            parser.error('export needs stage')
        if args.action == 'export':
            result = export_human(args.stage, base=args.base, name=args.name, case_ids=args.case_ids, references=args.references)
        else:
            if args.action == 'secondary' and not args.case_ids:
                parser.error('secondary needs --case-ids')
            result = export_blind(args.stage, base=args.base, name=args.name, arms=args.arms,
                case_ids=args.case_ids, references=args.references, seed=args.seed,
                batch_size=args.batch_size, secondary=args.action == 'secondary')
        print(json.dumps(result, ensure_ascii=False))
    else:
        result = recompute(args.base, args.prices, review_paths=read_json(args.reviews) if args.reviews else ())
        print(json.dumps(dict(accounting=result['accounting'], groups=len(result['groups']), integrityIssues=result['integrityIssues']), ensure_ascii=False))
