"""Reproducible, read-only-to-runtime collation of the 964 classic passages.

Downloads reference witnesses only with --fetch. Neither agreement nor spelling
normalization is an expert certification. Published releases are never edited.
"""
import argparse
import collections
import difflib
import hashlib
import html
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/knowledge/review/crosscheck-20260926/classic'
RAW = OUT / 'raw'
COMMIT = '8284adbf9e3435d713180e24f05bf75f8b7d1d96'
NAMES = '乾 坤 屯 蒙 需 訟 師 比 小畜 履 泰 否 同人 大有 謙 豫 隨 蠱 臨 觀 噬嗑 賁 剝 復 无妄 大畜 頤 大過 坎 離 咸 恒 遯 大壯 晉 明夷 家人 睽 蹇 解 損 益 夬 姤 萃 升 困 井 革 鼎 震 艮 漸 歸妹 豐 旅 巽 兌 渙 節 中孚 小過 既濟 未濟'.split()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def fetch():
    import requests
    from datetime import datetime, timezone
    RAW.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers['User-Agent'] = 'YijingEvidenceAudit/1.0 (educational source collation)'
    date = datetime.now(timezone.utc).isoformat()
    sources = []
    archive_url = f'https://codeload.github.com/kanripo/KR1a0001/zip/{COMMIT}'
    response = session.get(archive_url, timeout=45)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    for number in range(1, 65):
        filename = f'KR1a0001_{number:03d}.txt'
        path = next(n for n in archive.namelist() if n.endswith('/' + filename))
        data = archive.read(path)
        (RAW / filename).write_bytes(data)
        sources.append(dict(source_id=f'kr-{number}', hexagram_id=number,
            source_family='Kanripo TLS electronic witness', upstream_revision=COMMIT,
            url=f'https://github.com/kanripo/KR1a0001/blob/{COMMIT}/{filename}',
            raw_file='raw/' + filename, sha256=sha(data), retrieved_at=date,
            provenance_limit='TLS base edition; physical printed witness not verified. Not assumed genealogically independent of all web transcriptions.'))
    for start in range(0, 64, 32):
        titles = ['周易/' + n for n in NAMES[start:start + 32]]
        response = session.get('https://zh.wikisource.org/w/api.php', params=dict(
            action='query', format='json', prop='revisions', rvprop='ids|content',
            rvslots='main', titles='|'.join(titles), redirects=1), timeout=35)
        response.raise_for_status()
        result = response.json()['query']
        aliases = {x['from']: x['to'] for x in result.get('normalized', []) + result.get('redirects', [])}
        pages = {p['title']: p for p in result['pages'].values()}
        for number, title in enumerate(titles, start + 1):
            target = aliases.get(title, title)
            page = pages[target]
            revision = page['revisions'][0]
            data = revision['slots']['main']['*'].encode()
            filename = f'ws-{number:02d}-{revision["revid"]}.wiki'
            (RAW / filename).write_bytes(data)
            sources.append(dict(source_id=f'ws-{number}', hexagram_id=number,
                source_family='Wikisource Zhouyi electronic witness', upstream_revision=str(revision['revid']),
                url=f'https://zh.wikisource.org/w/index.php?title={quote(page["title"])}&oldid={revision["revid"]}',
                raw_file='raw/' + filename, sha256=sha(data), retrieved_at=date,
                provenance_limit='Community electronic text; same-family check for existing Wikisource-derived records, not independent corroboration for those.'))
    (OUT / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n')

char_map = {}
for line in (ROOT / 'data/knowledge/raw/opencc-TSCharacters.txt').read_text().splitlines():
    if '\t' in line:
        key, values = line.split('\t', 1)
        char_map[key] = values.split(' ')[0]

def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    return ''.join(char_map.get(c, c) for c in text if unicodedata.category(c)[0] not in 'PSZC')

def clean(text):
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)  # note readings retained in raw witness
    text = re.sub(r'-\{(?:[A-Z]\|)?(.*?)\}-', r'\1', text)
    text = re.sub(r'\[\[([^]|]+)\|([^]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^]]+)\]\]', r'\1', text)
    text = re.sub(r'<[^>]*>', '', text)
    return html.unescape(text.replace("'''", '').replace("''", '')).strip()

def line_no(label):
    if label.startswith('初'): return 1
    if label.startswith('上'): return 6
    if label.startswith('用'): return 7
    return '一二三四五六'.index(label[1]) + 1

def parse_wiki(raw):
    out, mode, xiang_index = {}, None, 0
    raw = re.sub(r'<[^>]*>', '', raw)  # Includes HTML tags spanning lines (Kun).
    for rawline in raw.splitlines():
        text = clean(rawline)
        if re.match(r'^\*(?![*#]).*易經', text): mode = 'classic'; continue
        if re.match(r'^\*(?![*#]).*彖(?:曰|傳)', text): mode = 'tuan'; continue
        if re.match(r'^\*(?![*#]).*象(?:曰|傳)', text): mode = 'xiang'; xiang_index = 0; continue
        if re.match(r'^\*(?![*#]).*文言', text): mode = None; continue
        body = re.sub(r'^[*#]+', '', text).strip()
        if not body: continue
        if mode == 'classic':
            m = re.match(r'(初[九六]|[九六][二三四五]|上[九六]|用[九六])[：:、，]\s*(.*)', body)
            if m:
                n = line_no(m[1]); out[('special' if n == 7 else 'line', n)] = m[2]
            elif rawline.startswith('**'):
                body = re.sub(r'^[^：:]{1,12}[：:]\s*', '', body)
                out[('judgment', None)] = out.get(('judgment', None), '') + body
        elif mode == 'tuan' and rawline.startswith('**'):
            out[('tuan', None)] = out.get(('tuan', None), '') + body
        elif mode == 'xiang':
            if rawline.startswith('*#'):
                xiang_index += 1
                out[('line_xiang', xiang_index)] = body
            elif rawline.startswith('**'):
                out[('xiang', None)] = out.get(('xiang', None), '') + body
    return out

def parse_kanripo(raw):
    text = re.sub(r'^#.*$', '', raw, flags=re.M)
    text = re.sub(r'<pb:[^>]+>', '', text).replace('¶', '')
    text = text.split('《文言》')[0]
    # Mark actual line headings before collapsing whitespace; labels also occur
    # inside Xiang prose and must never overwrite the corresponding line text.
    text = re.sub(r'^(初[九六]|[九六][二三四五]|上[九六]|用[九六])[：:、，]', r'§\1:', text, flags=re.M)
    text = re.sub(r'\s+', '', text)
    # Start at the title before judgment, after the hexagram glyph / trigram names.
    text = re.sub(r'^.*?[乾坤震巽坎離艮兌]下[乾坤震巽坎離艮兌]上', '', text)
    out = {}
    tuan = text.find('《彖》曰')
    if tuan < 0: return out
    markers = list(re.finditer(r'《([彖象])》曰[：:]?|§(初[九六]|[九六][二三四五]|上[九六]|用[九六]):', text))
    out[('judgment', None)] = re.sub(r'^《[^》]+》', '', text[:markers[0].start()])
    if '《艮第五十二》' in raw:
        out[('judgment', None)] = text[:markers[0].start()].replace('《艮》', '艮', 1)
    current_line = None
    for i, marker in enumerate(markers):
        body = text[marker.end():markers[i+1].start() if i + 1 < len(markers) else len(text)]
        if marker[1] == '彖': key = ('tuan', None); current_line = None
        elif marker[1] == '象': key = ('xiang', None) if current_line is None else ('line_xiang', current_line)
        else:
            current_line = line_no(marker[2]); key = ('special' if current_line == 7 else 'line', current_line)
        out[key] = body
    if '《乾第一》' in raw and ('xiang', None) in out:
        sentences = [part + '。' for part in out[('xiang', None)].split('。') if part]
        if len(sentences) != 8: raise ValueError('Unexpected Qian Xiang structure')
        out[('xiang', None)] = sentences[0]
        for number, sentence in enumerate(sentences[1:], 1): out[('line_xiang', number)] = sentence
    return out

def differences(a, b):
    return [{'kind': op, 'local': a[i:j], 'reference': b[k:l]}
        for op, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != 'equal']

def compare():
    sources = json.loads((OUT / 'sources.json').read_text())
    parsed = {}
    for source in sources:
        data = (OUT / source['raw_file']).read_bytes()
        if sha(data) != source['sha256']: raise ValueError('Witness hash changed')
        parser = parse_wiki if source['source_id'].startswith('ws-') else parse_kanripo
        parsed[source['source_id']] = parser(data.decode())
    release = 'knowledge-v3-20260926'  # This audit remains pinned to its original subject.
    corpus_path = ROOT / f'data/knowledge/releases/{release}/evidence.json'
    records = [e for e in json.loads(corpus_path.read_text()) if e['layer'] == 'classic']
    rows = []
    for evidence in records:
        key = (evidence['section'], evidence['line'])
        comparisons = []
        for family in ['kr', 'ws']:
            sid = f'{family}-{evidence["hexagramId"]}'
            source = next(s for s in sources if s['source_id'] == sid)
            ref = parsed[sid].get(key)
            a, b = normalize(evidence['text']), normalize(ref or '')
            comparisons.append(dict(source_id=sid, url=source['url'], reference_text=ref,
                aligned=ref is not None, equal_after_normalization=ref is not None and a == b,
                differences=differences(a,b) if ref is not None and a != b else [],
                same_source_family_as_local=family == 'ws' and 'wikisource' in evidence['source']))
        matches = sum(c['equal_after_normalization'] for c in comparisons)
        status = ('matches_two_transcriptions' if matches == 2 else 'matches_one_transcription' if matches else
                  'differs_from_both' if all(c['aligned'] for c in comparisons) else 'alignment_pending')
        rows.append(dict(evidence_id=evidence['id'], hexagram_id=evidence['hexagramId'], section=evidence['section'],
            line=evidence['line'], current_text=evidence['text'], status=status, comparisons=comparisons,
            review_method='deterministic_alignment_with_machine_assisted_followup', human_reviewed=False))
    (OUT / 'records.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows))
    summary = dict(release_id=release, input_sha256=sha(corpus_path.read_bytes()), total=len(rows),
        statuses=dict(collections.Counter(row['status'] for row in rows)),
        normalization='NFKC + remove Unicode punctuation/symbols/spacing + pinned OpenCC single-character folding; never alters source text',
        limitations=['Machine agreement is not historical authenticity or full scholarly collation.',
            'Wikisource and Kanripo are separate electronic witnesses; undocumented common ancestry is not ruled out.',
            'Differences require adjudication; no majority-vote automatic correction.'])
    (OUT / 'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    if args.fetch: fetch()
    compare()
