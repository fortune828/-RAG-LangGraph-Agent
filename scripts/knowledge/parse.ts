/** Source-specific, deterministic parsers. Only directly attached line annotations are published.
 * No regex-based author inference from arbitrary prose or subcommentary is performed. */
export interface Template { name: string; body: string; start: number; end: number }
export function templates(raw: string): Template[] {
  const result: Template[] = [];
  for (let i = 0; i < raw.length - 1; i++) {
    if (raw.slice(i, i + 2) !== '{{') continue;
    const start = i; let depth = 1; i += 2;
    for (; i < raw.length - 1 && depth; i++) {
      if (raw.slice(i, i + 2) === '{{') { depth++; i++; }
      else if (raw.slice(i, i + 2) === '}}') { depth--; i++; }
    }
    if (depth) throw new Error('UNBALANCED_WIKI_TEMPLATE');
    const end = i, content = raw.slice(start + 2, end - 2), pipe = content.indexOf('|');
    result.push({ name: content.slice(0, pipe < 0 ? undefined : pipe).trim(), body: pipe < 0 ? '' : content.slice(pipe + 1), start, end });
    i = end - 1;
  }
  return result;
}
export function cleanWiki(raw: string): string {
  if (/{{|}}|\?|�|□|缺字|待補|待补/.test(raw)) throw new Error('UNRESOLVED_MARKUP_OR_GLYPH');
  return raw.replace(/-\{(?:A\|)?([^{}]*)\}-/g, '$1').replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2')
    .replace(/\[\[([^\]]+)\]\]/g, '$1').replace(/<[^>]+>/g, '').replace(/'{2,}/g, '').trim();
}
export interface ParsedNote { line: number; text: string; start: number; end: number; method: string }
export interface ParseResult { notes: ParsedNote[]; excluded: {line: number; reason: string}[] }
const linePattern = '(初[九六]|[九六][二三四五]|上[九六])';
function lineNumber(label: string) { return label.startsWith('初') ? 1 : label.startsWith('上') ? 6 : '一二三四五六'.indexOf(label[1]) + 1; }
function accept(out: ParseResult, line: number, raw: string, start: number, end: number, method: string) {
  if (out.notes.some(n => n.line === line) || out.excluded.some(n => n.line === line)) return;
  try { const text = cleanWiki(raw); if (!text) throw new Error('EMPTY'); out.notes.push({ line, text, start, end, method }); }
  catch (e) { out.excluded.push({ line, reason: (e as Error).message }); }
}
export function parseWangBi(raw: string): ParseResult {
  const out: ParseResult = { notes: [], excluded: [] };
  // Each source page is one hexagram. Only a line-opening scripture followed by the
  // explicit '*' annotation template qualifies; 批 / [疏] prose is never imported.
  for (const match of raw.matchAll(new RegExp('^(?:\\{\\{\\+\\|)?' + linePattern + '[：，、:][^\\n]*', 'gm'))) {
    const row = match[0], note = templates(row).find(t => t.name === '*');
    if (note) accept(out, lineNumber(match[1]), note.body, match.index! + note.start, match.index! + note.end, 'line-heading + explicit * note; excludes 疏');
  }
  return out;
}
export function parseChengYi(raw: string, hexagramId: number): ParseResult {
  const out: ParseResult = { notes: [], excluded: [] };
  const heads: Record<number,string> = {1:'乾下乾上',2:'坤下坤上',15:'艮下坤上',16:'坤下震上',32:'巽下震上'};
  const header = heads[hexagramId]; if (!header) return out;
  const head = new RegExp('^[△■]*' + header, 'm').exec(raw); if (!head) return out;
  const rest = raw.slice(head.index + header.length), next = /^[△■]*[乾坤震巽坎離艮兌]下[乾坤震巽坎離艮兌]上/m.exec(rest);
  const end = next ? head.index + header.length + next.index : raw.length;
  const block = raw.slice(head.index, end);
  for (const match of block.matchAll(new RegExp('^' + linePattern + '[^（\\n]*(?:\\n+)?（', 'gm'))) {
    const start = head.index + match.index! + match[0].length - 1;
    let depth = 1, j = start + 1;
    for (; j < end && depth; j++) { if (raw[j] === '（') depth++; else if (raw[j] === '）') depth--; }
    if (depth) { out.excluded.push({line:lineNumber(match[1]),reason:'UNBALANCED_COMMENTARY'}); continue; }
    accept(out, lineNumber(match[1]), raw.slice(start + 1, j - 1), start, j, 'first line heading + balanced commentary parentheses within known hexagram block');
  }
  return out;
}
export function parseZhuXi(raw: string, hexagramId: number): ParseResult {
  const out: ParseResult = { notes: [], excluded: [] };
  const header = '{{SKchar|' + (1230 + hexagramId) + '}}', start = raw.indexOf(header);
  if (start < 0) return out;
  // Hexagram-glyph codes 1231..1294 identify blocks, not arbitrary missing glyphs.
  const rest = raw.slice(start + header.length), next = /\{\{SKchar\|(12(?:[3-8][0-9]|9[0-4]))\}\}/.exec(rest);
  const end = next ? start + header.length + next.index : raw.length, block = raw.slice(start, end);
  let prev = 0;
  for (const t of templates(block)) {
    if (t.name === 'SK notes') {
      const before = block.slice(prev, t.start), m = new RegExp('(?:^|○)\\s*' + linePattern + '[^{}]*$').exec(before);
      if (m) accept(out, lineNumber(m[1]), t.body, start + t.start, start + t.end, 'hexagram glyph + line heading + explicit SK notes; unresolved glyphs quarantined');
    }
    prev = t.end;
  }
  return out;
}
