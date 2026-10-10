import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import type { Evidence } from '../../shared/types';

const root = new URL('../../data/knowledge/', import.meta.url);
const parentId = 'knowledge-v3-20260926';
const releaseId = 'knowledge-v3-20260926-r1';
const auditFile = 'review/crosscheck-20260926/corrections.json';
const read = (path: string) => readFileSync(new URL(path, root), 'utf8');
const sha = (s: string) => createHash('sha256').update(s).digest('hex');
const serialize = (value: unknown) => JSON.stringify(value, null, 2) + '\n';
const original = read(`releases/${parentId}/evidence.json`);
assert.equal(sha(original), 'c9375046355eac73ef120aa9275877c9eafbbf225f1d5d2d9116a3a4ba3b2b05');
const parent = JSON.parse(read(`releases/${parentId}/manifest.json`));
const auditText = read(auditFile);
const fixes = JSON.parse(auditText);
assert.equal(fixes.length, 12);
assert.equal(new Set(fixes.map((f: any) => f.evidenceId)).size, 12);
const evidence: Evidence[] = JSON.parse(original);
for (const fix of fixes) {
  const e = evidence.find(e => e.id === fix.evidenceId);
  assert.ok(e, fix.evidenceId);
  assert.equal(e.text, fix.originalText, `${e.id}: source changed`);
  assert.equal(e.text.split(fix.before).length, 2, `${e.id}: ambiguous replacement`);
  assert.equal(e.text.replace(fix.before, fix.after), fix.correctedText);
  assert.equal(fix.applied, false); // Historical proposal stays immutable.
  assert.equal(fix.humanReviewed, false);
  const urls: string[] = fix.sourceUrls || fix.supportingUrls;
  assert.ok(urls.length >= 3 && urls.every(url => url.startsWith('https://')));
  e.textOriginal = e.textOriginal || e.text;
  e.text = fix.correctedText;
  e.reviewStatus = 'machine_corrected';
  e.review = '多源机器辅助核对后修订；采集原文及修改依据已保留，未经人工专家校勘。';
  e.correctionHistory = [{ auditId: 'crosscheck-20260926', parentReleaseId: parentId,
    date: '2026-09-26', before: fix.before, after: fix.after, reason: fix.reason,
    sourceUrls: urls, reviewMethod: 'machine_assisted', humanReviewed: false }];
}
for (const e of evidence) e.releaseId = releaseId;
const evidenceText = serialize(evidence);
const appliedText = serialize(fixes.map((fix: any) => ({ ...fix, applied: true, appliedReleaseId: releaseId,
  parentReleaseId: parentId, auditFile })));
const manifest = { ...parent, version: releaseId, releaseId, parentReleaseId: parentId,
  parentEvidenceSha256: sha(original), evidenceSha256: sha(evidenceText),
  correctionSet: { file: 'corrections.json', sha256: sha(appliedText), records: fixes.length,
    auditFile, auditSha256: sha(auditText), method: 'machine_assisted', humanReviewed: false },
  review: '12 machine-assisted corrections; original text and provenance retained. Zero expert/human review claims.' };
const files = { 'evidence.json': evidenceText, 'manifest.json': serialize(manifest),
  'corrections.json': appliedText, 'legacy-aliases.json': read(`releases/${parentId}/legacy-aliases.json`) };
// Validate all existing artifacts before writing anything; published bytes cannot change.
for (const [name, text] of Object.entries(files)) {
  const path = `releases/${releaseId}/${name}`;
  if (existsSync(new URL(path, root))) assert.equal(read(path), text, `IMMUTABLE_RELEASE_CHANGED:${name}`);
}
mkdirSync(new URL(`releases/${releaseId}/`, root), { recursive: true });
for (const [name, text] of Object.entries(files)) {
  const path = new URL(`releases/${releaseId}/${name}`, root);
  if (!existsSync(path)) writeFileSync(path, text, { flag: 'wx' });
}
if (process.argv.includes('--activate')) {
  const active = JSON.parse(read('active.json')).releaseId;
  assert.ok([parentId, releaseId].includes(active), 'Unexpected active release; refusing to replace it');
  const tmp = new URL('active.json.tmp', root);
  writeFileSync(tmp, serialize({ releaseId }));
  renameSync(tmp, new URL('active.json', root));
}
console.log(JSON.stringify({ releaseId, corrections: fixes.length, records: evidence.length,
  activated: process.argv.includes('--activate') }, null, 2));
