import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { cleanWiki, templates } from './parse';
const root=new URL('../../data/knowledge/',import.meta.url);
const read=(file:string)=>readFileSync(new URL(file,root),'utf8');
const sha=(s:string)=>createHash('sha256').update(s).digest('hex');
const sources=JSON.parse(read('sources.json'));
const active=process.argv.find(arg => arg.startsWith('--release='))?.slice('--release='.length)
  || JSON.parse(read('active.json')).releaseId;
assert.match(active, /^[a-zA-Z0-9_-]{1,90}$/);
const manifest=JSON.parse(read(`releases/${active}/manifest.json`));
const serialized=read(`releases/${active}/evidence.json`), evidence=JSON.parse(serialized);
assert.equal(sha(serialized),manifest.evidenceSha256);
for(const s of sources)assert.equal(sha(read(s.rawFile)),s.sha256,s.sourceId);
assert.equal(new Set(evidence.map((e:any)=>e.id)).size,evidence.length);
let sourceEvidence = evidence;
let correctionCount = 0;
if (manifest.correctionSet) {
  assert.match(manifest.parentReleaseId, /^[a-zA-Z0-9_-]{1,90}$/);
  const parentRaw = read(`releases/${manifest.parentReleaseId}/evidence.json`);
  assert.equal(sha(parentRaw), manifest.parentEvidenceSha256);
  const parentManifest = JSON.parse(read(`releases/${manifest.parentReleaseId}/manifest.json`));
  assert.equal(sha(parentRaw), parentManifest.evidenceSha256);
  sourceEvidence = JSON.parse(parentRaw);
  assert.equal(sourceEvidence.length, evidence.length);
  const correctionsRaw = read(`releases/${active}/corrections.json`);
  assert.equal(sha(correctionsRaw), manifest.correctionSet.sha256);
  assert.equal(sha(read(manifest.correctionSet.auditFile)), manifest.correctionSet.auditSha256);
  const fixes = JSON.parse(correctionsRaw);
  const proposed = JSON.parse(read(manifest.correctionSet.auditFile));
  assert.equal(fixes.length, manifest.correctionSet.records);
  assert.equal(new Set(fixes.map((f:any)=>f.evidenceId)).size, fixes.length);
  for (const fix of fixes) {
    const original = sourceEvidence.find((e:any)=>e.id===fix.evidenceId);
    const updated = evidence.find((e:any)=>e.id===fix.evidenceId);
    const proposal = proposed.find((f:any)=>f.evidenceId===fix.evidenceId);
    assert.ok(original && updated && proposal);
    assert.equal(fix.originalText, original.text);
    assert.equal(fix.correctedText, proposal.correctedText);
    assert.equal(fix.before, proposal.before); assert.equal(fix.after, proposal.after);
    assert.equal(original.text.split(fix.before).length, 2);
    assert.equal(original.text.replace(fix.before,fix.after), updated.text);
    assert.equal(updated.text, fix.correctedText);
    assert.equal(updated.textOriginal, original.textOriginal || original.text);
    assert.equal(updated.reviewStatus, 'machine_corrected');
    assert.equal(fix.applied, true); assert.equal(fix.appliedReleaseId, active);
    assert.equal(fix.humanReviewed, false);
    assert.deepEqual(updated.correctionHistory, [{auditId:'crosscheck-20260926',
      parentReleaseId:manifest.parentReleaseId,date:'2026-09-26',before:fix.before,after:fix.after,
      reason:fix.reason,sourceUrls:fix.sourceUrls||fix.supportingUrls,reviewMethod:'machine_assisted',humanReviewed:false}]);
  }
  // Only the audited text/annotation fields may change; all anchors and original provenance remain intact.
  for (const old of sourceEvidence) {
    const updated = evidence.find((e:any)=>e.id===old.id); assert.ok(updated);
    const reverted = {...updated, releaseId:old.releaseId};
    if (fixes.some((f:any)=>f.evidenceId===old.id)) {
      for (const key of ['text','textOriginal','reviewStatus','review','correctionHistory']) {
        if (key in old) reverted[key]=old[key]; else delete reverted[key];
      }
    }
    assert.deepEqual(reverted,old,`${old.id}: unexpected metadata or text mutation`);
    assert.equal(updated.releaseId,active);
  }
  correctionCount=fixes.length;
}
for(const legacy of JSON.parse(readFileSync(new URL('../../data/evidence.json',import.meta.url),'utf8'))){
  const migrated=sourceEvidence.find((e:any)=>e.id===legacy.id);assert.ok(migrated,legacy.id);
  for(const key of ['text','source','locator','version'])assert.equal(migrated[key],legacy[key],`${legacy.id}:${key}`);
}
const reviews=read('review/records.jsonl').trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));
for(const row of reviews){
  const source=sources.find((s:any)=>s.sourceId===row.sourceId),raw=read(source.rawFile),e=sourceEvidence.find((e:any)=>e.id===row.passageId);
  assert.ok(e);assert.equal(e.reviewStatus,'machine_parsed');assert.equal(row.humanReviewer,null);
  const excerpt=raw.slice(row.rawStart,row.rawEnd);
  const body=excerpt.startsWith('（')?excerpt.slice(1,-1):templates(excerpt)[0]?.body;
  assert.equal(cleanWiki(body),e.text,`${e.id}: raw span roundtrip`);
  assert.equal(e.rawLocator.startLine,raw.slice(0,row.rawStart).split('\n').length);
  assert.equal(e.rawLocator.endLine,raw.slice(0,row.rawEnd).split('\n').length);
  assert.deepEqual(e.commentsOn,e.anchorIds);
  assert.ok(e.line>=1&&e.line<=6);assert.equal(e.layer,'commentary');
}
for(let id=1;id<=64;id++){
  assert.ok(evidence.some((e:any)=>e.hexagramId===id&&e.section==='judgment'&&e.layer==='classic'));
  assert.ok(evidence.some((e:any)=>e.hexagramId===id&&e.section==='tuan'&&e.layer==='classic'));
  for(let line=1;line<=6;line++)assert.ok(evidence.some((e:any)=>e.hexagramId===id&&e.section==='line'&&e.line===line&&e.layer==='classic'));
}
assert.equal(reviews.length,evidence.filter((e:any)=>e.layer==='commentary').length);
console.log(JSON.stringify({status:'passed',releaseId:active,sources:sources.length,records:evidence.length,corrections:correctionCount,
  commentaryRoundtrips:reviews.length,humanReviewed:0,scope:'Structural/source consistency only, not expert textual or answer quality certification'},null,2));
