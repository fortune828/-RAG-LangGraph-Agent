"""Offline attribution-only counterfactual; never changes original outcomes."""
import hashlib
import json
import sys
import types
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
from backend.knowledge import load_release  # noqa: E402


def module(name):
    path = HERE / 'source' / ('validation-' + name + '.py')
    mod = types.ModuleType('backend.attribution_diagnostic_' + name)
    mod.__package__ = 'backend'
    exec(compile(path.read_text(), str(path), 'exec'), mod.__dict__)
    return mod


def source_ids(value):
    if isinstance(value, list):
        for item in value:
            yield from source_ids(item)
    if isinstance(value, dict):
        if 'id' in value and 'text' in value:
            yield value['id']
        for key in ('原典', '历代注解'):
            yield from source_ids(value.get(key, []))


def visible_ids(messages):
    ids = set()
    for message in messages:
        try:
            content = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(content, dict):
            continue
        if '本轮参考资料' in content:
            content = content['本轮参考资料']
        ids.update(source_ids(content.get('参考原文', [])))
        if message['role'] == 'tool':
            ids.update(source_ids(content.get('evidence', [])))
    return ids


def main():
    before, after = module('before'), module('after')
    corpus = {e['id']: e for e in load_release()[0]}
    summaries, changes, raw_hashes = {}, [], {}
    for batch in ('autonomous-20261010', 'semantic-20261010'):
        stats = Counter()
        for path in sorted((ROOT / 'evaluation/reports' / batch).rglob('*.jsonl')):
            if 'source' in path.parts:
                continue
            raw = path.read_bytes()
            trace = str(path.relative_to(ROOT))
            raw_hashes[trace] = hashlib.sha256(raw).hexdigest()
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            stats['traceFiles'] += 1
            requests = {e['callId']: e for e in events if e['kind'] == 'model_request'}
            for index, event in enumerate(events):
                if event['kind'] != 'model_result' or event.get('stage') == 'route':
                    continue
                stats['generationOrRepairResponses'] += 1
                response = event.get('response') or {}
                message = response.get('message') or {}
                try:
                    candidate = json.loads(message.get('content') or '')
                except (ValueError, TypeError):
                    stats['unparseableResponses'] += 1
                    continue
                if not isinstance(candidate, dict):
                    stats['nonObjectResponses'] += 1
                    continue
                stats['parsedCandidates'] += 1
                paragraphs = candidate.get('paragraphs') or []
                stats['citations'] += sum(len(p.get('citations') or []) for p in paragraphs if isinstance(p, dict))
                validation = None
                for following in events[index + 1:]:
                    if following['kind'] == 'model_request':
                        break
                    if following['kind'] == 'validation':
                        validation = following
                        break
                if validation is not None:
                    evidence = validation['evidence']
                else:
                    ids = visible_ids(requests[event['callId']]['payload']['messages'])
                    evidence = [corpus[eid] for eid in sorted(ids) if eid in corpus]
                old_answer, old_errors = before.validate_answer(candidate, evidence, targets=(), require_citations=False)
                new_answer, new_errors = after.validate_answer(candidate, evidence, targets=(), require_citations=False)
                stats['beforeLocallyValid'] += old_answer is not None
                stats['afterLocallyValid'] += new_answer is not None
                name_changes = []
                for paragraph in paragraphs:
                    if not isinstance(paragraph, dict):
                        continue
                    text = paragraph.get('text', '')
                    old_names = [before.asserted_names(text, before.AUTHORS), before.asserted_names(text, before.WORKS)]
                    new_names = [after.asserted_names(text, after.AUTHORS), after.asserted_names(text, after.WORKS)]
                    if old_names != new_names:
                        name_changes.append(dict(text=text, beforeAuthors=old_names[0], afterAuthors=new_names[0],
                                                 beforeWorks=old_names[1], afterWorks=new_names[1]))
                if old_errors != new_errors or name_changes:
                    stats['changedCandidates'] += 1
                    changes.append(dict(batch=batch, trace=trace, callId=event['callId'], eventId=event.get('eventId'),
                        rawStage=event.get('stage'), originalModelStatus=event.get('status'),
                        originalValidationErrors=(validation or {}).get('errors'),
                        beforeErrors=old_errors, afterErrors=new_errors,
                        removedErrors=[e for e in old_errors if e not in new_errors],
                        addedErrors=[e for e in new_errors if e not in old_errors], paragraphs=name_changes))
        summaries[batch] = dict(stats)
    report = dict(
        purpose='Offline diagnosis of the noun/verb boundary only. Original failed runs remain failed; this is not a successful real regeneration.',
        method='Same candidates and visible evidence, same current validator dependencies, before/after attribution regex. targets=() and require_citations=False isolate the changed attribution rule; no full scenario or semantic success is inferred.',
        summaries=summaries, changes=changes,
        validatorHashes={name: hashlib.sha256((HERE / 'source' / ('validation-' + name + '.py')).read_bytes()).hexdigest()
                         for name in ('before', 'after')},
        rawTraceHashes=raw_hashes)
    (HERE / 'diff.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(summaries=summaries, changes=len(changes)), ensure_ascii=False))


if __name__ == '__main__':
    main()
