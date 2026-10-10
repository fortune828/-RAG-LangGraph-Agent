"""Offline release audit of formal artifacts; no model requests."""
import csv
import json
from pathlib import Path
from urllib.parse import unquote
import re

from evaluation.formal_run import assert_frozen
from evaluation.formal_stats import BASE


def read(path):
    return json.loads(path.read_text())


def main():
    assert_frozen()
    metrics=read(BASE/'analysis/metrics.json')
    quality=read(BASE/'analysis/quality.json')
    evidence=read(BASE/'analysis/evidence.json')
    assert metrics['ledger']['consistent'] and not metrics['integrityIssues']
    expected={'smoke':4,'development':160,'test':160,'rag':36,'continuous':12}
    for stage,n in expected.items():
        rows=read(BASE/stage/'rows.json')
        assert len(rows)==n and all(r['status'] in ('completed','validation_failed','failed','budget_stopped') for r in rows),stage
    assert len(evidence['controls'])==80
    for row in evidence['controls']:
        assert all(row[k] is True for k in ('identicalInitialEvidence','identicalScope','castUnchanged')),row
    assert quality['primaryRecords']==248 and quality['secondaryRecords']==16 and quality['humanRecords']==0
    reviewed=0;occurrences=0
    for spec in read(BASE/'review-manifest.json')['reviews']:
        path=BASE/spec['scores'];materials=read(path.parent/'materials.json');byid={m['answerId']:m for m in materials}
        results=read(path)['results']
        assert {r['answerId'] for r in results}==set(byid) and len(results)==len(byid),path
        for r in results:
            m=byid[r['answerId']]
            for evaluation in r['evaluations']:
                target=evaluation['target']
                claims=[c for c in m['citationClaims'] if c['target']==target]
                coverage=(evaluation.get('faithfulness') or {}).get('coverage',{})
                assert coverage.get('citationClaimOccurrencesTotal',0)==len(claims),(path,r['answerId'],target,'total')
                assert coverage.get('citationClaimOccurrencesReviewed',0)==len(claims),(path,r['answerId'],target,'reviewed')
                occurrences+=len(claims)
            reviewed+=1
    assert reviewed==264
    human=0
    for stage,n in expected.items():
        if stage=='smoke':continue
        with (BASE/'human'/stage/'scores.csv').open(newline='') as f:
            rows=list(csv.DictReader(f))
        assert len(rows)==n
        assert all(all(not value for key,value in row.items() if key!='answerId') for row in rows),stage
        human+=len(rows)
    assert human==368
    root=Path(__file__).resolve().parents[1]
    docs=[root/'evaluation/final-evaluation-20261010.md',root/'evaluation/project-showcase-20261010.md',BASE/'README.md',BASE/'human/README.md']
    for path in docs:
        text=path.read_text()
        assert '<!-- FINAL_' not in text,path
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if '://' in target or target.startswith('#'):continue
            assert (path.parent/unquote(target.split('#')[0])).exists(),(path,target)
    private_hits=[]
    env=root/'.env.local'
    if env.exists():
        from dotenv import dotenv_values
        secrets=[str(v).encode() for k,v in dotenv_values(env).items() if v and any(w in k.upper() for w in ('KEY','SECRET','TOKEN','PASSWORD')) and len(str(v))>8]
        for path in BASE.rglob('*'):
            if path.is_file() and any(secret in path.read_bytes() for secret in secrets):private_hits.append(str(path))
    assert not private_hits,'PRIVATE_VALUE_IN_ARTIFACT'
    result=dict(productionAndDatasetFrozen=True,ledgerConsistent=True,initialEvidenceScopeAndCastMatched=80,
                completedPlannedCells=sum(expected.values()),primaryAIRecords=248,secondaryAIRecords=16,
                reviewedCitationOccurrencesIncludingCandidatesAndRepeats=occurrences,humanBlankRows=368,
                reportLinksExist=True,secretScanPassed=True,productionCommit=read(BASE/'freeze.json')['commit'])
    (BASE/'verification/delivery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
