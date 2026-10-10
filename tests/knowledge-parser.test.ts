import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { parseWangBi, parseChengYi, parseZhuXi, templates } from '../scripts/knowledge/parse';

test('Wang Bi parser separates explicit line annotation from subcommentary and quoted authors', () => {
  const raw = '九二：見龍在田。{{*|王注测试正文。}}\n{{批|[疏]孔疏引王弼曰：不可合并。}}\n九三：終日乾乾。\n{{批|[疏]不应冒充王弼直接注。}}';
  const r = parseWangBi(raw);
  assert.deepEqual(r.notes.map(n => ({line:n.line,text:n.text})), [{line:2,text:'王注测试正文。'}]);
  assert.equal(raw.slice(r.notes[0].start, r.notes[0].end), '{{*|王注测试正文。}}');
});

test('Cheng Yi parser respects balanced cross-line commentary and next hexagram boundary', () => {
  const raw = '乾下乾上\n九二見龍在田。\n（第一行\n第二行（内层）。）\n坤下坤上\n六二直方大。（另一卦注解。）';
  const r = parseChengYi(raw, 1);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].text, '第一行\n第二行（内层）。');
  assert.equal(raw.slice(r.notes[0].start, r.notes[0].end), '（第一行\n第二行（内层）。）');
  const broken = parseChengYi('乾下乾上\n九二見龍在田。（未闭合\n坤下坤上\n六二直方大。（别卦。）', 1);
  assert.equal(broken.notes.length, 0);
  assert.equal(broken.excluded[0].reason, 'UNBALANCED_COMMENTARY');
});

test('Zhu Xi parser isolates selected hexagram, preserves nested template boundaries and quarantines unresolved glyphs', () => {
  const raw = '{{SKchar|1231}}○九二見龍在田{{SK notes|完整注解。}}○九三君子終日乾乾{{SK notes|缺字{{SKchar|9}}不强补。}}{{SKchar|1232}}○六二直方大{{SK notes|坤卦材料。}}';
  const r = parseZhuXi(raw, 1);
  assert.deepEqual(r.notes.map(n => ({line:n.line,text:n.text})), [{line:2,text:'完整注解。'}]);
  assert.ok(r.excluded.some(e => e.line === 3 && e.reason === 'UNRESOLVED_MARKUP_OR_GLYPH'));
  assert.equal(templates('{{a|outer{{b|inner}}}}')[0].body, 'outer{{b|inner}}');
  assert.throws(() => templates('{{a|unclosed'), /UNBALANCED_WIKI_TEMPLATE/);
});

test('checked-in source snapshots match declared hashes and review offsets are honest machine records', () => {
  const root = new URL('../data/knowledge/', import.meta.url);
  const sources = JSON.parse(readFileSync(new URL('sources.json', root), 'utf8'));
  const reviews = readFileSync(new URL('review/records.jsonl', root), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const byId = new Map<string, {rawFile:string;sha256:string;upstreamRevision:string}>(sources.map((s:any) => [s.sourceId,s]));
  for (const s of sources) {
    const bytes = readFileSync(new URL(s.rawFile, root));
    assert.equal(createHash('sha256').update(bytes).digest('hex'), s.sha256);
    assert.ok(s.upstreamRevision);
  }
  assert.ok(reviews.length > 0);
  for (const r of reviews) {
    const source = byId.get(r.sourceId)!; assert.ok(source);
    const raw = readFileSync(new URL(source.rawFile, root), 'utf8');
    assert.equal(r.offsetUnit, 'UTF-16 code units');
    assert.ok(Number.isInteger(r.rawStart) && r.rawStart >= 0 && r.rawEnd > r.rawStart && r.rawEnd <= raw.length);
    assert.equal(r.status, 'machine_parsed');
    assert.equal(r.humanReviewer, null);
    assert.ok(raw.slice(r.rawStart, r.rawEnd).length > 0);
  }
});
