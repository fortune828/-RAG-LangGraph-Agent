"""Offline recomputation and blind export of the semantic development experiments.

Reads visible traces only. Never imports model clients, runs models, revises
scores, or rewrites raw rows/traces. Run again after a stage finishes writing.
"""
import argparse
import csv
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evaluation/reports/semantic-20261010'
PRICE_PATH = ROOT / 'evaluation/reports/autonomous-20261010/capabilities/price_table.json'
LABEL_FIELDS = ('semanticReference', 'expectedEvidenceGroups', 'reviewPoints', 'retrievalNecessity',
                'sourceFixture', 'initialEvidenceAudit', 'expectedBehavior', 'gold', 'reviewScores')


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def count_value(value):
    return value if type(value) is int and value >= 0 else None


def sum_complete(values):
    return sum(values) if all(value is not None for value in values) else None


def field_usage(calls, field):
    values = [count_value((call.get('usage') or {}).get(field)) for call in calls]
    return dict(reportedSum=sum(value for value in values if value is not None),
                unknownCalls=sum(value is None for value in values), total=sum_complete(values))


def estimate_cost(request, usage, prices):
    """Use the frozen same-day weekend rates; other dates require new prices."""
    try:
        instant = datetime.fromisoformat(request['timestamp'].replace('Z', '+00:00'))
        if instant.tzinfo is None:
            raise ValueError('timestamp has no offset')
        instant = instant.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return dict(usd=None, reason='unknown_request_time')
    if instant.date().isoformat() != prices['observedDate'] or instant.weekday() < 5:
        return dict(usd=None, reason='frozen_weekend_price_not_applicable')
    model = request.get('payload', {}).get('model')
    rate = prices.get('models', {}).get(model, {}).get('off_peak')
    if rate is None:
        return dict(usd=None, reason='unknown_model_price')
    usage = usage or {}
    hit, miss, output = [count_value(usage.get(field)) for field in
                         ('prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'completion_tokens')]
    result = dict(usd=None, currency=prices['currency'], rate='off_peak',
                  requestDateUTC=instant.date().isoformat(), reason='Saturday/Sunday UTC; frozen same-day price table')
    if None in (hit, miss, output):
        return result | dict(reason='missing_usage_components')
    if count_value(usage.get('prompt_tokens')) is not None and usage['prompt_tokens'] != hit + miss:
        return result | dict(reason='inconsistent_cache_usage')
    result['usd'] = (hit * rate['input_cache_hit'] + miss * rate['input_cache_miss'] + output * rate['output']) / 1_000_000
    return result


def effective_stage(stage, arm, request, request_count):
    raw = request.get('stage')
    if stage == 'context' and arm in ('roles', 'grouped', 'core') and request_count == 1 and raw == 'repair':
        return 'generate', 'context single-generation experiment: legacy recorder inferred repair from messages length > 2'
    return raw, None


def call_records(stage, arm, trace, events, prices):
    requests = {event['callId']: event for event in events if event['kind'] == 'model_request'}
    results = {event['callId']: event for event in events if event['kind'] == 'model_result'}
    if len(requests) != sum(e['kind'] == 'model_request' for e in events):
        raise ValueError('DUPLICATE_REQUEST_ID:' + trace)
    if len(results) != sum(e['kind'] == 'model_result' for e in events):
        raise ValueError('DUPLICATE_RESULT_ID:' + trace)
    if set(results) - set(requests):
        raise ValueError('RESULT_WITHOUT_REQUEST:' + trace)
    calls = []
    for call_id, request in requests.items():
        result = results.get(call_id)
        response = (result or {}).get('response') or {}
        phase, erratum = effective_stage(stage, arm, request, len(requests))
        payload = request.get('payload', {})
        counted = result.get('budgetCounted') if result is not None else None
        usage = response.get('usage')
        options = {k: v for k, v in payload.items() if k not in ('messages', 'tools')}
        cost = estimate_cost(request, usage, prices) if counted is True else dict(usd=None, reason='not_dispatched' if counted is False else 'dispatch_unknown')
        reservation = None
        if counted is True and count_value((usage or {}).get('total_tokens')) is None:
            # Reasoning omission hides its byte count. Do not invent the original
            # reservation in that case; the recorded token ledger stays separate.
            if count_value(payload.get('max_tokens')) is not None and 'reasoningOmitted' not in json.dumps(payload):
                reservation = len(json.dumps(dict(messages=payload.get('messages'), tools=payload.get('tools')),
                                             ensure_ascii=False).encode()) + payload['max_tokens'] + 1024
        calls.append(dict(trace=trace, callId=call_id, stage=stage, arm=arm,
            rawStage=request.get('stage'), rawResultStage=(result or {}).get('stage'), effectiveStage=phase,
            erratum=erratum, requestTimestamp=request.get('timestamp'), status=(result or {}).get('status', 'unresolved'),
            budgetCounted=counted, error=(result or {}).get('error'), requestOptions=options,
            actualModel=response.get('model'), usage=usage, unknownReservation=reservation,
            durationMs=(result or {}).get('durationMs'), estimate=cost,
            requestEventId=request.get('eventId'), resultEventId=(result or {}).get('eventId')))
    return calls


def label_audit(base, items):
    """Check forbidden keys and distinct long label spans, not conceptual leakage."""
    labels = {}
    for path in (base / 'fixtures').glob('*.json'):
        fixture = read_json(path)
        if isinstance(fixture, dict):
            for case in fixture.get('cases', []):
                labels[case['caseId']] = case
    violations, checked = [], 0

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)

    for item in items:
        fixture = labels.get(item['caseId'], {})
        permitted = json.dumps(item.get('input', {}), ensure_ascii=False) + ''.join(
            evidence.get('text', '') for evidence in item['row'].get('evidence', []))
        # Evidence IDs, intent names and other technical values legitimately
        # occur in both labels and model data. Only distinctive prose from
        # semantic labels is an additional exact-span leakage signal.
        spans = {text for field in ('semanticReference', 'reviewPoints', 'expectedBehavior')
                 for text in strings(fixture.get(field))
                 if len(text) >= 20 and (re.search(r'[\u3400-\u9fff]', text) or ' ' in text) and text not in permitted}
        for event in item['events']:
            if event['kind'] != 'model_request':
                continue
            checked += 1
            for message in event.get('payload', {}).get('messages', []):
                content = message.get('content') or ''
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                keys = [field for field in LABEL_FIELDS if re.search(r'"' + re.escape(field) + r'"\s*:', content)]
                matches = sorted(text for text in spans if text in content)
                if keys or matches:
                    violations.append(dict(trace=item['trace'], callId=event['callId'], role=message.get('role'),
                                           labelFields=keys, exactLabelSpans=matches))
    return dict(requestsChecked=checked, passed=not violations, violations=violations,
        limitation='Checks evaluation-only field names and distinctive exact label spans. This is not a proof against conceptual leakage or coincidental matching.')


def load_items(base, prices):
    items = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.name in ('source', 'blind', 'fixtures'):
            continue
        paths = sorted(directory.glob('*.jsonl'))
        if not paths:
            continue
        rows = read_json(directory / 'rows.json') if (directory / 'rows.json').exists() else []
        row_map = {(row['caseId'], row['arm']): row for row in rows}
        inputs = read_json(directory / 'inputs.json') if (directory / 'inputs.json').exists() else []
        input_map = {case['caseId']: case for case in inputs}
        manifest = read_json(directory / 'manifest.json') if (directory / 'manifest.json').exists() else {}
        seen = set()
        for path in paths:
            events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if not events:
                continue
            identity = (events[0]['caseId'], events[0]['variant'])
            if identity in seen:
                raise ValueError('DUPLICATE_CELL:' + str(path))
            seen.add(identity)
            case_id, arm = identity
            row = row_map.get(identity, dict(caseId=case_id, arm=arm, status='running', durationMs=None))
            trace = str(path.relative_to(base))
            calls = call_records(directory.name, arm, trace, events, prices)
            item = dict(stage=directory.name, caseId=case_id, arm=arm, row=row, input=input_map.get(case_id),
                trace=trace, traceSha256=digest(path.read_bytes()), events=events, calls=calls,
                config=row.get('config'), graphVersion=row.get('graphVersion', manifest.get('graphVersion')),
                rowPresent=identity in row_map)
            items.append(item)
        missing = set(row_map) - seen
        if missing:
            raise ValueError('ROW_WITHOUT_TRACE:' + str(directory) + ':' + repr(sorted(missing)))
    return items


def summarize_group(items):
    calls = [call for item in items for call in item['calls']]
    dispatched = [call for call in calls if call['budgetCounted'] is True]
    unresolved = sum(call['budgetCounted'] is None for call in calls)
    tools = [event for item in items for event in item['events'] if event['kind'] == 'tool_result']
    statuses = Counter(item['row'].get('status', 'unknown') for item in items)
    latencies = [item['row'].get('durationMs') for item in items if item['row'].get('durationMs') is not None]
    usage = field_usage(dispatched, 'total_tokens')
    resolutions = 0
    for item in items:
        validations = [event for event in item['events'] if event['kind'] == 'validation']
        resolutions += sum(len(event.get('quoteResolutions', [])) for event in validations) if validations else len(item['row'].get('quoteResolutions', []))
    estimates = [call['estimate']['usd'] for call in dispatched]
    return dict(n=len(items), mechanicalCompleted=statuses['completed'],
        failed=statuses['failed'] + statuses['validation_failed'], technicalFailures=statuses['failed'],
        validationFailed=statuses['validation_failed'], unfinished=sum(n for status, n in statuses.items()
            if status not in ('completed', 'failed', 'validation_failed')),
        calls=len(dispatched), modelRequests=len(calls), unresolvedRequests=sum(call['budgetCounted'] is None for call in calls),
        budgetRejectedRequests=sum(call['status'] == 'budget_stopped' for call in calls),
        reportedTokens=usage['reportedSum'], unknownUsageCalls=usage['unknownCalls'], totalTokens=None if unresolved else usage['total'],
        medianMs=statistics.median(latencies) if latencies else None, latencyN=len(latencies),
        toolRequests=sum(event['kind'] == 'tool_request' for item in items for event in item['events']),
        toolAttempts=sum(event.get('budgetCounted') is True for event in tools),
        toolOk=sum(event.get('executionStatus') == 'ok' for event in tools),
        toolEmpty=sum(event.get('executionStatus') == 'empty' for event in tools),
        toolRejected=sum(event.get('executionStatus') == 'rejected' for event in tools),
        repairs=sum(call['effectiveStage'] == 'repair' for call in dispatched),
        repairRequests=sum(call['effectiveStage'] == 'repair' for call in calls), quoteResolutions=resolutions,
        estimatedUSD=None if unresolved else sum_complete(estimates), reportedEstimateUSD=sum(value for value in estimates if value is not None),
        unknownCostCalls=sum(value is None for value in estimates) + unresolved,
        configurations=[json.loads(value) for value in sorted({json.dumps(call['requestOptions'], sort_keys=True) for call in calls})])


def reveal_reviews(base):
    reviews, summaries = [], []
    for path in sorted((base / 'blind').glob('*/scores.json')):
        name = path.parent.name
        primary = base / name / 'review-key.json'
        key_path = primary if primary.exists() else base / (name + '-review-key.json')
        if not key_path.exists():
            raise ValueError('REVIEW_KEY_MISSING:' + str(path))
        keys = {entry['answerId']: entry for entry in read_json(key_path)}
        data = read_json(path)
        for result in data['results']:
            identity = keys[result['answerId']]
            reviews.append(dict(review=name, reviewer=data.get('reviewer'), scoreSource=str(path.relative_to(base)),
                keySource=str(key_path.relative_to(base)), stage=identity.get('stage', name),
                caseId=identity['caseId'], arm=identity['arm'], result=result))
        summaries.append(dict(review=name, reviewer=data.get('reviewer'), originalSummary=data.get('summary'),
                              scoreSha256=digest(path.read_bytes()), keySha256=digest(key_path.read_bytes())))
    return dict(scoreType='Existing independent AI subjective reviews; not human or expert scores. Original scores unchanged.',
                reviews=summaries, results=reviews)


def recompute(base=BASE, price_path=PRICE_PATH):
    base, price_path = Path(base), Path(price_path)
    prices = read_json(price_path)
    items = load_items(base, prices)
    groups = defaultdict(list)
    for item in items:
        groups[(item['stage'], item['arm'], item['config'], item['graphVersion'])].append(item)
    tables = [dict(stage=stage, arm=arm, config=config, graphVersion=version, **summarize_group(group))
              for (stage, arm, config, version), group in sorted(groups.items(), key=lambda pair: str(pair[0]))]
    calls = [call for item in items for call in item['calls']]
    dispatched = [call for call in calls if call['budgetCounted'] is True]
    unresolved = [call for call in calls if call['budgetCounted'] is None]
    usage = {field: field_usage(dispatched + unresolved, field) for field in ('total_tokens', 'prompt_tokens', 'completion_tokens',
             'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')}
    accounted = sum_complete([count_value((call['usage'] or {}).get('total_tokens'))
        if count_value((call['usage'] or {}).get('total_tokens')) is not None else call['unknownReservation'] for call in dispatched])
    recomputed = dict(calls=len(dispatched), reportedTokens=usage['total_tokens']['reportedSum'],
        missingUsageCalls=field_usage(dispatched, 'total_tokens')['unknownCalls'], accountedTokens=accounted)
    ledger = read_json(base / 'ledger.json') if (base / 'ledger.json').exists() else None
    comparisons = {key: dict(recorded=ledger.get(key), recomputed=value, match=ledger.get(key) == value if value is not None else None)
                   for key, value in recomputed.items()} if ledger is not None else {}
    totals = dict(scope='All experimental stages: accounting only; no pooled production success rate.',
        groups=tables, accounting=recomputed, usage=usage,
        cost=dict(currency=prices['currency'], estimatedUSD=None if unresolved else sum_complete([call['estimate']['usd'] for call in dispatched]),
                  reportedEstimateUSD=sum(call['estimate']['usd'] for call in dispatched if call['estimate']['usd'] is not None),
                  unknownCalls=sum(call['estimate']['usd'] is None for call in dispatched) + len(unresolved),
                  priceTable=str(price_path.relative_to(ROOT)) if price_path.is_relative_to(ROOT) else str(price_path),
                  priceTableSha256=digest(price_path.read_bytes()),
                  rule=prices['peakRule'], limitation='Usage-based same-day off-peak list-price estimate, not an account bill; failure usage included; reasoning is already part of completion tokens.'),
        ledger=dict(recorded=ledger, comparisons=comparisons,
                    consistent=all(value['match'] is True for value in comparisons.values()) if comparisons else None),
        labelAudit=label_audit(base, items),
        errata=[{key: call[key] for key in ('trace', 'callId', 'rawStage', 'effectiveStage', 'erratum')} for call in calls if call['erratum']],
        unresolvedRequests=sum(call['budgetCounted'] is None for call in calls),
        rowsNotYetSaved=[item['trace'] for item in items if not item['rowPresent']],
        rawTraces={item['trace']: item['traceSha256'] for item in items},
        caveats=['Mechanical completion does not establish semantic quality.',
                 'Stages, arms, configurations and graph versions remain separate.',
                 'Unknown usage totals and costs remain null; reported sums are lower bounds when unknown calls exist.',
                 'Ledger mismatches can occur while an experiment is still writing; recompute after completion.',
                 'Unknown dispatch is reported separately and is never counted as zero-cost success.'])
    write_json(base / 'usage.json', dict(calls=calls, usage=usage))
    write_json(base / 'totals.json', totals)
    write_json(base / 'review-revealed.json', reveal_reviews(base))
    fields = [key for key in tables[0] if key != 'configurations'] if tables else ['stage', 'arm', 'n']
    with (base / 'tables.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(tables)
    return totals


def export(stage, name=None, *, base=BASE):
    """Create one immutable anonymous pack. Existing packs/keys are never replaced."""
    base = Path(base)
    name = name or stage
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in (stage, name)):
        raise ValueError('INVALID_EXPORT_NAME')
    source = base / stage
    destination = base / 'blind' / name
    key_path = source / 'review-key.json' if name == stage else base / (name + '-review-key.json')
    if destination.exists() or key_path.exists():
        raise FileExistsError('Existing blind materials/key will not be overwritten: ' + name)
    rows = read_json(source / 'rows.json')
    inputs = {case['caseId']: case for case in read_json(source / 'inputs.json')}
    materials, keys = [], []
    for index, row in enumerate(sorted(rows, key=lambda row: digest(['semantic-blind-v1', stage, row['caseId'], row['arm']]))):
        case = inputs[row['caseId']]
        answer_id = f'A{index + 1:02}'
        materials.append(dict(answerId=answer_id, question=case['question'], coreQuestion=case['session']['question'],
            history=case['session']['turns'], cast=case['session']['cast'], answer=row.get('answer'),
            evidence=row.get('evidence', []), status=row['status']))
        keys.append(dict(answerId=answer_id, stage=stage, caseId=row['caseId'], arm=row['arm']))
    destination.mkdir(parents=True)
    write_json(destination / 'materials.json', materials)
    write_json(key_path, keys)
    lines = ['# 匿名回答材料', '', '技术失败保留；机械状态不代表语义质量。策略解盲键另存，评分前勿读取。', '']
    for material in materials:
        lines += ['## ' + material['answerId'], '', '**核心问题：** ' + material['coreQuestion'], '',
            '**当前问题：** ' + material['question'], '', '**历史：** ' + json.dumps(material['history'], ensure_ascii=False), '',
            '**卦象：** ' + json.dumps(material['cast'], ensure_ascii=False), '', '**状态：** ' + material['status'], '']
        lines += [p['text'] + '\n' for p in (material['answer'] or {}).get('paragraphs', [])]
        lines += ['**结构化回答及引文：**', '', '```json', json.dumps(material['answer'], ensure_ascii=False, indent=2), '```', '',
                  '**本轮资料：**', '', '```json', json.dumps(material['evidence'], ensure_ascii=False, indent=2), '```', '']
    (destination / 'materials.md').write_text('\n'.join(lines))
    return dict(materials=str(destination / 'materials.json'), key=str(key_path), n=len(materials))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=('summarize', 'export'))
    parser.add_argument('stage', nargs='?')
    parser.add_argument('--name')
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--prices', type=Path, default=PRICE_PATH)
    args = parser.parse_args()
    if args.action == 'export':
        if not args.stage:
            parser.error('export requires stage')
        print(json.dumps(export(args.stage, args.name, base=args.base), ensure_ascii=False))
    else:
        result = recompute(args.base, args.prices)
        print(json.dumps(dict(accounting=result['accounting'], ledgerConsistent=result['ledger']['consistent'],
                              labelAuditPassed=result['labelAudit']['passed'], groups=len(result['groups'])), ensure_ascii=False))
