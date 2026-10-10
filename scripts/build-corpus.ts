import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import type { Evidence, Section } from '../shared/types';
const commit = '4c5f00f8cb62c6e0d2471243d28150b473ef592f';
const version = 'open-iching-' + commit.slice(0, 12) + '-v2';
const repo = `https://github.com/john-walks-slow/open-iching/blob/${commit}/`;
const read = (name: string) => JSON.parse(readFileSync(`data/upstream/${name}.json`, 'utf8'));
const gua = read('iching'); const tuan = read('tuan'); const xiang = read('xiang');
const records: Evidence[] = [];
function add(id: number, name: string, section: Section, line: number | null, text: string, path: string, locator: string) {
  if (!text?.trim()) return;
  records.push({ id: `zy-${id}-${section}${line === null ? '' : '-' + line}`, hexagramId: id,
    name, section, line, text, source: repo + path, locator, version,
    review: '版本已固定，结构已验证；文字未完成逐条人工校勘' });
}
for (const g of gua) {
  add(g.id, g.name, 'judgment', null, g.scripture, 'iching/iching.json', `${g.name}·卦辞`);
  for (const l of g.lines) add(g.id, g.name, l.id <= 6 ? 'line' : 'special', l.id, l.scripture,
    'iching/iching.json', `${g.name}·${l.name}`);
  add(g.id, g.name, 'tuan', null, tuan[`iching__${g.id}`], 'ichuan/tuan.json', `${g.name}·彖传`);
  add(g.id, g.name, 'xiang', null, xiang[`iching__${g.id}`], 'ichuan/xiang.json', `${g.name}·大象`);
  for (const l of g.lines) add(g.id, g.name, 'line_xiang', l.id, xiang[`iching__${g.id}_${l.id}`],
    'ichuan/xiang.json', `${g.name}·${l.name}小象`);
}
if (new Set(records.map(x => x.id)).size !== records.length || gua.length !== 64) throw new Error('Invalid corpus');
// Explicit, independently sourced corrigenda; upstream snapshots remain byte-identical.
const corrections = [
  { id: 'zy-1-line-2', text: '见龙在田，利见大人。' },
  { id: 'zy-1-line_xiang-2', text: '见龙在田，德施普也。' },
];
for (const correction of corrections) {
  const record = records.find(e => e.id === correction.id)!;
  record.text = correction.text;
  record.source = 'https://zh.wikisource.org/w/index.php?title=周易/乾&oldid=2404991';
  record.review = '已对照维基文库固定修订2404991校正“见龙再田”为“见龙在田”；参见data/SOURCES.md';
}
const manifest = { version, commit, records: records.length, generatedBy: 'npm run corpus:build',
  licenseNotice: '上游 Readme：数据采集自互联网，仅供学习交流使用，不得用于商业用途。仅用于本地学习演示；商业发布前须替换或获得授权。',
  review: '未逐条校勘；保留上游原文，包括疑似异文/录入问题，不宣称权威校订本。',
  missingTuan: gua.filter((g: any) => !tuan[`iching__${g.id}`]).map((g: any) => g.id),
  corrections,
  sourceHashes: Object.fromEntries(['iching','tuan','xiang','names'].map(n => [n,
    createHash('sha256').update(readFileSync(`data/upstream/${n}.json`)).digest('hex')])) };
writeFileSync('data/evidence.json', JSON.stringify(records, null, 2) + '\n');
writeFileSync('data/manifest.json', JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({records: records.length, missingTuan: manifest.missingTuan}));
