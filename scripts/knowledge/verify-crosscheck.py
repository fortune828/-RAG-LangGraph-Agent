"""Verify audit coverage, source integrity and reversible proposed amendments.

Offline only. Does not apply corrections or alter a published knowledge release.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/knowledge/review/crosscheck-20260926'
RELEASE = 'knowledge-v3-20260926'
EXPECTED_SHA = 'c9375046355eac73ef120aa9275877c9eafbbf225f1d5d2d9116a3a4ba3b2b05'
def read(p):
    return json.loads(p.read_text())
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
corpus_file = ROOT / f'data/knowledge/releases/{RELEASE}/evidence.json'
assert digest(corpus_file) == EXPECTED_SHA, 'Audited release was changed'
# Historical audit is pinned to RELEASE, independent of today's active pointer.
corpus = {r['id']: r for r in read(corpus_file)}
index = []
for area, filename, id_key, status_key, text_key in [
    ('classic', 'adjudicated.jsonl', 'evidence_id', 'adjudication_status', 'current_text'),
    ('wang-bi', 'records.jsonl', 'evidenceId', 'status', 'localText'),
    ('song', 'records.jsonl', 'evidenceId', 'status', 'currentText'),
]:
    for line, text in enumerate((OUT / area / filename).read_text().splitlines(), 1):
        r = json.loads(text)
        eid = r[id_key]
        assert corpus[eid]['text'] == r[text_key], f'{eid}: wrong audited text'
        index.append(dict(evidence_id=eid, area=area, status=r[status_key],
            record_file=f'{area}/{filename}', record_line=line, human_reviewed=False))
assert len(index) == len({r['evidence_id'] for r in index}) == len(corpus) == 1076
assert {r['evidence_id'] for r in index} == set(corpus)
snapshot_count = 0
for area, file, pathkey in [
    ('classic','sources.json','raw_file'), ('classic','additional-sources.json','raw_file'),
    ('wang-bi','snapshots.json','file'), ('song','sources.json','localFile'),
]:
    for s in read(OUT / area / file):
        assert digest(OUT / area / s[pathkey]) == s['sha256'], f'Changed snapshot: {s[pathkey]}'
        snapshot_count += 1
corrections = []
for area in ['classic', 'wang-bi']:
    for fix in read(OUT / area / 'corrections.json'):
        eid = fix['evidenceId']
        original = corpus[eid]['text']
        assert original == fix['originalText']
        assert original.count(fix['before']) == 1
        assert original.replace(fix['before'],fix['after']) == fix['correctedText']
        assert fix['applied'] is False and fix['humanReviewed'] is False
        corrections.append(dict(fix, audit_area=area))
assert len(corrections) == len({r['evidenceId'] for r in corrections}) == 12
conditional_id = 'cheng-yi@1313743:h1:line3:10ea52416d'
assert '三雖人位' in corpus[conditional_id]['text']
summary = dict(release_id=RELEASE, corpus_sha256=EXPECTED_SHA, coverage=len(index),
    areas=dict(Counter(r['area'] for r in index)), verified_snapshot_count=snapshot_count,
    proposed_correction_count=len(corrections), conditional_edition_issue_ids=[conditional_id],
    production_changes_applied=False, human_reviewed_count=0,
    caution='Cross-transcription coverage and agreement are not a corpus accuracy rate or expert certification.')
(OUT/'index.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in index))
(OUT/'corrections.json').write_text(json.dumps(corrections,ensure_ascii=False,indent=2)+'\n')
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
