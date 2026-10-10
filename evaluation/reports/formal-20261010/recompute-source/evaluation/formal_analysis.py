"""Evidence acquisition and semantic review summaries from immutable formal records."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from evaluation.formal_stats import BASE, DIMENSIONS, cluster_bootstrap, wilson, write_csv
from evaluation.paired import write_json


def read(p):
    return json.loads(p.read_text())


def coverage(groups, ids):
    return [bool(set(group) & set(ids)) for group in groups]


def observed_boolean(rows, field):
    values=[r[field] for r in rows if type(r[field]) is bool]
    return wilson(sum(values),len(values)) | dict(unknown=len(rows)-len(values),plannedN=len(rows))


def comparable(values, expected):
    """A counterexample proves inequality; an incomplete equal subset proves nothing."""
    unique={json.dumps(value,sort_keys=True) for value in values.values()}
    return False if len(unique)>1 else True if set(values)==set(expected) else None


def generation_ids(row, events):
    validations=[e for e in events if e['kind']=='validation' and isinstance(e.get('evidence'),list)]
    if validations:
        return [e['id'] for e in validations[-1]['evidence']], 'validation_event'
    if isinstance(row.get('finalGenerationEvidence'),list):
        return [e['id'] for e in row['finalGenerationEvidence']], 'row_final_generation'
    results={e['callId']:e for e in events if e['kind']=='model_result'}
    requests=[e for e in events if e['kind']=='model_request' and e.get('stage') in ('generate','repair')
              and results.get(e['callId'],{}).get('budgetCounted') is True]
    if requests:
        for message in requests[-1].get('payload',{}).get('messages',[]):
            try: context=json.loads(message.get('content') or '')
            except (TypeError,ValueError):continue
            if isinstance(context,dict) and isinstance(context.get('参考原文'),list):
                return [e['id'] for e in context['参考原文']], 'last_counted_generation_request'
    return None, 'unknown'


def evidence_analysis(base=BASE, *, labels=None):
    base=Path(base)
    label_source='explicit synthetic/authorized labels'
    if labels is None:
        frozen=base/'source/evaluation/formal/labels.json'
        path=frozen if frozen.exists() else Path(__file__).parent/'formal/labels.json'
        labels=read(path)
        label_source=str(path)
    labels={r['caseId']:r for r in labels['cases']}
    results=[];controls=[]
    for stage in ('development','test'):
        if not (base/stage/'rows.json').exists():continue
        rows=read(base/stage/'rows.json')
        manifest=read(base/stage/'manifest.json') if (base/stage/'manifest.json').exists() else {}
        expected=manifest.get('arms',['B','C','D','H'])
        prepared_rows=[]
        for r in rows:
            path=base/stage/f"{r['caseId']}-{r['arm']}.jsonl"
            events=[json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
            prepare=next((e for e in events if e['kind']=='prepare'),{})
            initial=r.get('initialEvidenceIds')
            if initial is None and isinstance(prepare.get('evidence'),list):initial=[e['id'] for e in prepare['evidence']]
            scope_known='scopeIds' in r or 'scopeIds' in prepare
            prepared_rows.append(dict(row=r,events=events,initial=initial,
                scope=r.get('scopeIds',prepare.get('scopeIds')),scopeKnown=scope_known))
        for cid in sorted({r['caseId'] for r in rows}):
            paired=[p for p in prepared_rows if p['row']['caseId']==cid]
            initials={p['row']['arm']:p['initial'] for p in paired if p['initial'] is not None}
            scopes={p['row']['arm']:p['scope'] for p in paired if p['scopeKnown']}
            casts={p['row']['arm']:p['row']['sameCast'] for p in paired if type(p['row'].get('sameCast')) is bool}
            controls.append(dict(stage=stage,caseId=cid,expectedArms=expected,observedArms=len(initials),
                missingInitialArms=sorted(set(expected)-initials.keys()),missingScopeArms=sorted(set(expected)-scopes.keys()),
                missingCastArms=sorted(set(expected)-casts.keys()),
                identicalInitialEvidence=comparable(initials,expected),identicalScope=comparable(scopes,expected),
                castUnchanged=False if any(value is False for value in casts.values()) else True if set(casts)==set(expected) else None))
        for p in prepared_rows:
            r,events,initial=p['row'],p['events'],p['initial']
            label=labels[r['caseId']];groups=label['evidenceGroups']
            before=coverage(groups,initial or [])
            # graph_cell only captures the final state on a normal graph return.
            # A default [] on an exception is not evidence that retrieval failed.
            final_known=r['status'] in ('completed','validation_failed') and isinstance(r.get('evidence'),list)
            final=[e['id'] for e in r['evidence']] if final_known else None
            visible,visible_source=generation_ids(r,events)
            tool_events=[e for e in events if e['kind']=='tool_result']
            tools=[t for t in r.get('trace',[]) if t.get('step')=='tool']
            seen=set(initial or []);contributing=0
            for event in tool_events:
                obtained={e['id'] for e in (event.get('returnedResult') or {}).get('evidence',[])} if event.get('executionStatus')=='ok' and event.get('budgetCounted') is True else set()
                contributing+=int(any(not set(g)&seen and set(g)&obtained for g in groups))
                seen|=obtained
            # A captured final state is also positive acquisition evidence.
            seen.update(final or [])
            seen.update(visible or [])
            acquired=coverage(groups,seen)
            initially_missing=(not all(before)) if groups and initial is not None else False if not groups and initial is not None else None
            acquired_all=all(acquired) if groups and (all(acquired) or final_known) else None
            final_all=all(coverage(groups,final)) if groups and final is not None else None
            visible_all=all(coverage(groups,visible)) if groups and visible is not None else None
            def resolved(value):return value if initially_missing is True else None
            model_results={e['callId']:e for e in events if e['kind']=='model_result'}
            route_requests=[e for e in events if e['kind']=='model_request' and e.get('stage')=='route']
            attempts=sum(e.get('budgetCounted') is True for e in tool_events)
            results.append(dict(stage=stage,caseId=r['caseId'],groupId=r['groupId'],arm=r['arm'],category=r['category'],
                status=r['status'],groups=len(groups),initialCovered=sum(before) if initial is not None else None,
                acquiredCoveredLowerBound=sum(acquired),finalCovered=sum(coverage(groups,final)) if final is not None else None,
                finalEvidenceKnown=final_known,gapInitiallyPresent=initially_missing,gapResolved=resolved(acquired_all),
                finalGapResolved=resolved(final_all),allRequiredEvidence=final_all,allRequiredAcquiredEvidence=acquired_all,
                generationEvidenceIds=visible,generationEvidenceSource=visible_source,
                generationCovered=sum(coverage(groups,visible)) if visible is not None else None,
                gapResolvedForGeneration=resolved(visible_all),allRequiredGenerationEvidence=visible_all,
                evidenceAlreadySufficient=bool(groups) and initial is not None and all(before),
                labelNecessity=label['retrievalRequired'],availability=label['availability'],
                initialAuditMatches=set(initial)==set(label['initialEvidenceAudit']['evidenceIds']) if initial is not None else None,
                toolAttempts=attempts,toolRequests=sum(e['kind']=='tool_request' for e in events),
                targetContributingCalls=contributing if groups else None,toolTrace=tools,rawToolEvents=len(tool_events),
                routeModelRequests=len(route_requests),
                routeModelCalls=sum(model_results.get(e['callId'],{}).get('budgetCounted') is True for e in route_requests),
                routeDispatchUnknown=sum(model_results.get(e['callId'],{}).get('budgetCounted') is None for e in route_requests),
                routingEntered=any(e['kind']=='prepare' and e.get('nextStep')=='route' for e in events),
                exactShortcut=any(t.get('code')=='EXACT_TARGET_POLICY' for t in r.get('trace',[])),
                newEvidenceIds=sorted(set(final)-set(initial)) if final is not None and initial is not None else None,
                acquiredEvidenceIds=sorted(seen),answerStatus=(r.get('answer') or {}).get('status'),
                limit='AI-labelled source groups are not semantic success. Acquired means accepted at some point; final retention and generation visibility are separate. Failed executions without negative evidence remain unknown.'))
    grouped=defaultdict(list)
    for r in results:grouped[(r['stage'],r['arm'])].append(r)
    summaries=[]
    for (stage,arm),rs in sorted(grouped.items()):
        gaps=[r for r in rs if r['gapInitiallyPresent'] is True];sufficient=[r for r in rs if r['evidenceAlreadySufficient']]
        summaries.append(dict(stage=stage,arm=arm,n=len(rs),
            gapResolution=observed_boolean(gaps,'gapResolved'),
            finalGapResolution=observed_boolean(gaps,'finalGapResolved'),
            generationGapResolution=observed_boolean(gaps,'gapResolvedForGeneration'),
            retrievedButNotVisible=[r['caseId'] for r in gaps if r['gapResolved'] is True and r['gapResolvedForGeneration'] is False],
            retrievedVisibilityUnknown=[r['caseId'] for r in gaps if r['gapResolved'] is True and r['gapResolvedForGeneration'] is None],
            targetContributingCalls=sum(r['targetContributingCalls'] or 0 for r in rs),
            labelledToolResults=sum(r['rawToolEvents'] for r in rs if r['groups']),
            initialSufficient=len(sufficient),unknownInitial=sum(r['gapInitiallyPresent'] is None for r in rs),
            casesWithCallsWhenSufficient=sum(r['toolAttempts']>0 for r in sufficient),
            toolAttemptsWhenSufficient=sum(r['toolAttempts'] for r in sufficient),
            toolRequestsWhenSufficient=sum(r['toolRequests'] for r in sufficient),
            missingCases=sum(r['availability']=='not_ingested' for r in rs),
            auditMismatch=[r['caseId'] for r in rs if r['initialAuditMatches'] is False and r['status'] not in ('not_run','running')],
            missingRecognized=sum(r['availability']=='not_ingested' and r['answerStatus']=='insufficient' and r['status']=='completed' for r in rs)))
    out=base/'analysis';out.mkdir(exist_ok=True)
    write_json(out/'evidence.json',dict(cases=results,groups=summaries,controls=controls,labelSource=label_source,
        definitions=dict(gapResolution='Required groups acquired in initial/accepted tool/final records, not proof of final retention.',
            generationGapResolution='Required groups present in actual generation context; absent capture is unknown.',
            routeModelCalls='Paired model_result budgetCounted=true attempts, not invoices or all proposed requests.',
            missingRecognized='Mechanical completed+insufficient status only, not semantic missingness judgment.')))
    write_csv(out/'evidence.csv',results)
    return summaries


def semantic_analysis(base=BASE):
    raw=read(base/'analysis/reviews.json')['results']
    groups=defaultdict(list)
    for r in raw:
        if r['target']!='delivered':continue
        category=next((c['scenario'] for c in read(base/'analysis/cells.json') if c['stage']==r['stage'] and c['caseId']==r['caseId'] and c['arm']==r['arm']), 'unknown')
        # Keep independent reviewer identities separate; never average away disagreement.
        groups[(r['stage'],r['arm'],category,json.dumps(r['reviewer'],sort_keys=True,ensure_ascii=False))].append(r)
    summaries=[]
    for (stage,arm,category,reviewer),rs in sorted(groups.items()):
        successful=sum(r['mechanicalComplete'] and r['original'].get('taskSuccess') is True for r in rs)
        scores={d:[r['scores'].get(d) for r in rs if r['scores'].get(d) is not None] for d in DIMENSIONS}
        summaries.append(dict(stage=stage,arm=arm,category=category,reviewer=json.loads(reviewer),n=len(rs),
            semanticCompletion=wilson(successful,len(rs)),
            dimensions={d:dict(n=len(v),mean=mean(v) if v else None,distribution=dict(Counter(v))) for d,v in scores.items()}))
    write_json(base/'analysis/semantic-groups.json',summaries)
    return summaries


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,default=BASE);args=p.parse_args()
    print(json.dumps(evidence_analysis(args.base),ensure_ascii=False,indent=2))
    if (args.base/'analysis/reviews.json').exists():semantic_analysis(args.base)
