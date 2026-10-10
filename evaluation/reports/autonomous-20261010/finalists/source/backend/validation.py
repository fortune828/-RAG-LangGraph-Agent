"""Mechanical checks, not semantic certification. Return actionable repair errors."""
import re

from pydantic import ValidationError

from .domain import HEXAGRAMS
from .knowledge import normalize
from .models import Answer

AUTHORS = {'wang-bi': ['王弼'], 'cheng-yi': ['程颐', '程頤', '伊川'], 'zhu-xi': ['朱熹'],
           'kong-yingda': ['孔颖达', '孔穎達'], 'han-kangbo': ['韩康伯', '韓康伯'], 'jiao-xun': ['焦循']}
WORKS = {'zhouyi-zhengyi': ['周易正义', '周易正義'], 'yichuan-yizhuan': ['伊川易传', '伊川易傳'],
         'zhouyi-benyi': ['周易本义', '周易本義']}
ATTRIBUTION_VERBS = r'认为|認為|强调|強調|指出|主张|主張|解释|解釋|说|說|曰|看来|看來|写道|寫道'
# Negation must govern this attribution, not merely occur elsewhere in its sentence.
ATTRIBUTION_REFUSAL = re.compile(
    r'(?:无法|無法|不能|不应|不應|不会|不會|不可|不宜|不代表|并非|並非|不是)'
    r'(?:据此|據此|由此)?(?:确认|確認|证明|證明|断言|斷言|认定|認定|推断|推斷|声称|聲稱|说|說)?[《“「『"\s]*$'
    r'|(?:未收录|未收錄|缺少|没有|沒有|尚无|尚無)(?:关于|關於)?[《\s]*$')
ATTRIBUTION_NEGATED_VERB = re.compile(r'(?:未曾|并未|並未|没有|沒有|不能|无法|無法|不|未)(?:明确|明確)?$')
MISSING_EXPLANATION = re.compile(r'(?:的)?(?:原文|材料|内容|內容)?(?:尚未|未|没有|沒有)(?:收录|收錄|提供)')


def named_hexagrams(question):
    return [g['number'] for g in HEXAGRAMS.values() if g['name'] in question or
            ((g['name'].split('为')[0] if '为' in g['name'] else g['name'][2:]) + '卦') in question]


def requested_targets(question, cast):
    ids = named_hexagrams(question) or [cast['original']['number']]
    digits = dict(zip('一二三四五六123456', [1, 2, 3, 4, 5, 6] * 2))
    lines = [digits[v] for v in re.findall(r'第?([一二三四五六1-6])爻', question)]
    lines += [digits[v] for v in re.findall(r'[九六]([二三四五])', question)]
    if re.search('初九|初六|初爻', question):
        lines.append(1)
    if re.search('上九|上六|上爻', question):
        lines.append(6)
    sections = []
    if lines:
        sections = ['line_xiang'] if re.search('小象|象传|象傳', question) else ['line']
        if re.search('小象|象传|象傳', question) and re.search('爻辞|爻辭', question):
            sections = ['line', 'line_xiang']
    elif re.search('用九|用六', question):
        sections, lines = ['special'], [7]
    elif named_hexagrams(question) or re.search('本卦|当前|本次|刚才', question):
        if re.search('卦辞|卦辭', question):
            sections = ['judgment']
        elif re.search('彖传|彖傳', question):
            sections = ['tuan']
        elif re.search('大象|象传|象傳', question) and re.search('原文|出处|查询|查出|请查', question):
            sections = ['xiang']
    classic = [dict(hexagramId=h, section=s, line=line, layer='classic')
               for h in ids for s in sections for line in sorted(set(lines)) or [None]]
    authors = [a for a, names in AUTHORS.items() if any(n in question for n in names)]
    works = [w for w, names in WORKS.items() if any(n in question for n in names)]
    if not authors and not works:
        return classic
    objects = classic or [dict(hexagramId=h) for h in ids]
    annotations = [dict(t, layer='commentary', **({'authorId': a} if a else {}),
                        **({'workId': w} if w else {}))
                   for t in objects for a in authors or [None] for w in works or [None]]
    return classic + annotations if re.search(r'原文.*(?:及|和|与|以及).*注|(?:原文|经文).*解释', question) else annotations


def matches_target(e, t):
    return (t.get('hexagramId') is None or e['hexagramId'] == t['hexagramId']) and (
        e.get('layer', 'classic') == 'classic' if t['layer'] == 'classic' else e.get('layer') in ('commentary', 'subcommentary')) and (
        not t.get('section') or e['section'] == t['section']) and (
        not t.get('line') or e['line'] == t['line']) and (
        not t.get('authorId') or t['authorId'] in e.get('authorIds', [])) and (
        not t.get('workId') or t['workId'] == e.get('workId'))


def asserted_names(text, catalog):
    """Recognize explicit viewpoint claims, not general semantic entailment.

    Check each local claim independently: a disclaimer cannot excuse another
    positive attribution. Coverage statements and explicit refusals are not claims.
    Implicit attribution, irony and nested negation still require human review.
    """
    result = []
    for key, names in catalog.items():
        pattern = re.compile('(?:' + '|'.join(map(re.escape, names)) +
                             r')(?P<bridge>.{0,14}?)(?P<verb>' + ATTRIBUTION_VERBS + ')')
        found = False
        for clause in re.split(r'[。！？；，,\n]|但是|然而|不过|不過|但', text):
            for match in pattern.finditer(clause):
                if ATTRIBUTION_REFUSAL.search(clause[:match.start()]):
                    continue
                if ATTRIBUTION_NEGATED_VERB.search(match['bridge']):
                    continue
                if match['verb'] in ('解释', '解釋') and MISSING_EXPLANATION.match(clause[match.end():]):
                    continue
                found = True
                break
            if found:
                result.append(key)
                break
    return result


def validate_answer(value, evidence, targets=(), require_citations=True):
    try:
        answer = Answer.model_validate(value).model_dump()
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False, include_url=False)][:12]
    errors = []
    by_id = {e['id']: e for e in evidence}
    cited = {c['evidenceId'] for p in answer['paragraphs'] for c in p['citations']}
    if answer['status'] == 'answered':
        if not answer['paragraphs']:
            errors.append('EMPTY_ANSWER_PARAGRAPHS')
        # Source/explanation answers need evidence somewhere in the answer.
        # Ordinary practical paragraphs may remain uncited. Whether a particular
        # interpretation is supported is a separate semantic review, not a regex.
        if require_citations and not cited:
            errors.append('MISSING_ANSWER_CITATION')
        for t in targets:
            if not any(e['id'] in cited and matches_target(e, t) for e in evidence):
                errors.append(f'REQUIRED_TARGET_NOT_CITED:{t}')
    for i, p in enumerate(answer['paragraphs']):
        for c in p['citations']:
            if c['claim'] not in p['text']:
                errors.append('CLAIM_NOT_IN_PARAGRAPH:' + c['evidenceId'])
            e = by_id.get(c['evidenceId'])
            if not e:
                errors.append('UNKNOWN_EVIDENCE:' + c['evidenceId'])
            elif len(normalize(c['quote'])) < 2 or normalize(c['quote']) not in normalize(e['text']):
                errors.append('QUOTE_MISMATCH:' + c['evidenceId'])
            if e:
                for author in asserted_names(c['claim'], AUTHORS):
                    if author not in e.get('authorIds', []):
                        errors.append('CLAIM_AUTHOR_ATTRIBUTION_MISMATCH:' + author)
                for work in asserted_names(c['claim'], WORKS):
                    if work != e.get('workId'):
                        errors.append('CLAIM_WORK_ATTRIBUTION_MISMATCH:' + work)
        for author in asserted_names(p['text'], AUTHORS):
            if not any(author in by_id.get(c['evidenceId'], {}).get('authorIds', []) for c in p['citations']):
                errors.append('AUTHOR_ATTRIBUTION_MISMATCH:' + author)
        for work in asserted_names(p['text'], WORKS):
            if not any(work == by_id.get(c['evidenceId'], {}).get('workId') for c in p['citations']):
                errors.append('WORK_ATTRIBUTION_MISMATCH:' + work)
    return (None if errors else answer), list(dict.fromkeys(errors))


def fallback():
    return dict(status='insufficient',
                paragraphs=[dict(text='这次回答未能通过核验，本次卦象已保留。可以查看原文依据，或继续说明你的问题。', citations=[])],
                limitations=['结构或引用校验修复一次后仍未通过。'])
