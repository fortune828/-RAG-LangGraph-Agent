"""Mechanical checks, not semantic certification. Return actionable repair errors."""
import re

from pydantic import ValidationError

from .citations import locate_original_quote
from .domain import HEXAGRAMS
from .knowledge import normalize
from .models import Answer, Citation

AUTHORS = {'wang-bi': ['王弼'], 'cheng-yi': ['程颐', '程頤', '伊川'], 'zhu-xi': ['朱熹'],
           'kong-yingda': ['孔颖达', '孔穎達'], 'han-kangbo': ['韩康伯', '韓康伯'], 'jiao-xun': ['焦循']}
WORKS = {'zhouyi-zhengyi': ['周易正义', '周易正義'], 'yichuan-yizhuan': ['伊川易传', '伊川易傳'],
         'zhouyi-benyi': ['周易本义', '周易本義']}
ATTRIBUTION_VERBS = r'认为|認為|强调|強調|指出|主张|主張|解释|解釋|[说說]法\s*(?:是|为|為|即|[:：])|[说說](?!法)|曰|看来|看來|写道|寫道'
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


def requests_classic_and_commentary(question):
    """Recognize a separate request for the classic, not an author's own text.

    The source location may still be unknown. In that case retain both layer
    targets and let retrieval locate them; an empty precise-target list must not
    silently turn a mixed question into an author-only question.
    """
    attribution_names = [name for catalog in (AUTHORS, WORKS) for names in catalog.values() for name in names]
    clauses = re.split(r'[，,。；;？！?!]|以及|然后|再看|再给|和|与|及', question)
    return any(re.search(r'原文|经文|經文|爻辞|爻辭|卦辞|卦辭', clause)
               and not re.search(r'注解|注文|注释|注釋|不要|不用|无需|無需|不必', clause)
               and not any(name in clause for name in attribution_names)
               for clause in clauses)


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
    if requests_classic_and_commentary(question):
        return (classic or [dict(hexagramId=h, layer='classic') for h in ids]) + annotations
    return annotations


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


def explicit_cast_errors(text, cast):
    """Check only standalone affirmative cast facts against deterministic data.

    This does not interpret arbitrary prose or the meaning of a line. Common
    explicit quotations/questions/conditions are skipped; this is not exhaustive
    linguistic recognition. Unrecognized statements are not certified as true.
    """
    aliases = {}
    for hexagram in HEXAGRAMS.values():
        short = hexagram['name'].split('为')[0] if '为' in hexagram['name'] else hexagram['name'][2:]
        for name in (hexagram['name'], short, short + '卦'):
            aliases[name] = hexagram['number']

    def line_name(index, value):
        polarity = '九' if value % 2 else '六'
        return '初' + polarity if index == 0 else '上' + polarity if index == 5 else polarity + '二三四五'[index - 1]

    moving = [line_name(i, cast['lines'][i]) for i in cast['changingIndices']]
    line_pattern = r'初[九六]|[九六][二三四五]|上[九六]'
    errors = []
    # Removing quoted spans prevents a quoted incorrect assertion from becoming
    # the assistant's own fact. It does not exempt adjacent unquoted assertions.
    unquoted = re.sub(r"""“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|'[^'\n]*'""", '', text)
    sentences = re.split(r'(?<=[。！？!?；;\n])', unquoted)
    clauses = [clause for sentence in sentences
               if not re.search(r'[？?]|如果|假如|假设|假設|倘若|若是', sentence)
               for clause in re.split(r'[。；;，,\n]', sentence)]
    for clause in clauses:
        clause = clause.strip()
        match = re.fullmatch(r'(本卦|变卦)(?:仍)?(?:是|为|：|:)\s*(.+)', clause)
        if match and match[2] in aliases:
            role = 'original' if match[1] == '本卦' else 'changed'
            if aliases[match[2]] != cast[role]['number']:
                errors.append(f"CAST_FACT_MISMATCH:{role}:expected={cast[role]['name']}")
        if re.fullmatch(r'(?:本卦|本次|此卦)?(?:无动爻|没有动爻|为静卦|是静卦)', clause) and moving:
            errors.append('CAST_FACT_MISMATCH:moving:expected=' + '、'.join(moving))
        match = re.fullmatch(r'(?:本卦的?|本次的?)?动爻(?:是|为|：|:)\s*(.+)', clause)
        if match:
            names = re.split(r'\s*(?:、|和|与)\s*', match[1])
            if all(re.fullmatch(line_pattern, name) for name in names) and set(names) != set(moving):
                errors.append('CAST_FACT_MISMATCH:moving:expected=' + ('、'.join(moving) or '无动爻'))
        match = re.fullmatch(r'(本卦|变卦)的?(初爻|上爻|第[一二三四五六1-6]爻)(?:是|为|：|:)\s*(' + line_pattern + ')', clause)
        if match:
            index = 0 if match[2] == '初爻' else 5 if match[2] == '上爻' else '一二三四五六123456'.index(match[2][1]) % 6
            values = cast['lines'] if match[1] == '本卦' else [1 if v in (6, 7) else 0 for v in cast['lines']]
            expected = line_name(index, values[index])
            if match[3] != expected:
                errors.append('CAST_FACT_MISMATCH:line:expected=' + expected)
    return list(dict.fromkeys(errors))


def validate_answer(value, evidence, targets=(), require_citations=True, *, issues=None, quote_resolutions=None, cast=None):
    try:
        answer = Answer.model_validate(value).model_dump()
    except ValidationError as exc:
        if issues is not None:
            issues.extend(dict(code=e['msg'], path=list(e['loc'])) for e in exc.errors(include_input=False, include_url=False)[:12])
        return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False, include_url=False)][:12]
    errors = []
    def add(code, path=()):
        errors.append(code)
        if issues is not None:
            issue = dict(code=code, path=list(path))
            if code.startswith('AUTHOR_ATTRIBUTION_MISMATCH:'):
                issue['message'] = '本段声称该作者的观点，但本段 citations 没有该作者的证据。将支持该观点的证据关联到本段，或更正无依据的归属。'
            elif code.startswith('WORK_ATTRIBUTION_MISMATCH:'):
                issue['message'] = '本段声称该作品的观点，但本段 citations 没有该作品的证据。将支持该观点的证据关联到本段，或更正无依据的归属。'
            issues.append(issue)

    by_id = {e['id']: e for e in evidence}
    cited = {c['evidenceId'] for p in answer['paragraphs'] for c in p['citations']}
    if answer['status'] == 'answered':
        if not answer['paragraphs']:
            add('EMPTY_ANSWER_PARAGRAPHS', ('paragraphs',))
        # Source/explanation answers need evidence somewhere in the answer.
        # Ordinary practical paragraphs may remain uncited. Whether a particular
        # interpretation is supported is a separate semantic review, not a regex.
        if require_citations and not cited:
            add('MISSING_ANSWER_CITATION', ('paragraphs',))
        for t in targets:
            if not any(e['id'] in cited and matches_target(e, t) for e in evidence):
                add(f'REQUIRED_TARGET_NOT_CITED:{t}', ('paragraphs',))
    for i, p in enumerate(answer['paragraphs']):
        if cast is not None:
            for error in explicit_cast_errors(p['text'], cast):
                add(error, ('paragraphs', i, 'text'))
        for j, c in enumerate(p['citations']):
            path = ('paragraphs', i, 'citations', j)
            if c['claim'] not in p['text']:
                add('CLAIM_NOT_IN_PARAGRAPH:' + c['evidenceId'], path + ('claim',))
            e = by_id.get(c['evidenceId'])
            if not e:
                add('UNKNOWN_EVIDENCE:' + c['evidenceId'], path + ('evidenceId',))
            elif len(normalize(c['quote'])) < 2 or normalize(c['quote']) not in normalize(e['text']):
                located = locate_original_quote(e['text'], c['quote'])
                if located is not None:
                    try:
                        # The source span can contain more punctuation than the
                        # requested quote. It must still fit the public schema.
                        Citation.model_validate(dict(c, quote=located['quote']))
                    except ValidationError:
                        located = None
                if (located is not None and len(normalize(located['quote'])) >= 2
                        and normalize(located['quote']) in normalize(e['text'])):
                    requested = c['quote']
                    c['quote'] = located['quote']
                    if quote_resolutions is not None:
                        quote_resolutions.append(dict(method='unique_source_span', path=list(path + ('quote',)),
                            evidenceId=c['evidenceId'], requestedQuote=requested, sourceQuote=c['quote'],
                            start=located['start'], end=located['end']))
                else:
                    add('QUOTE_MISMATCH:' + c['evidenceId'], path + ('quote',))
            if e:
                for author in asserted_names(c['claim'], AUTHORS):
                    if author not in e.get('authorIds', []):
                        add('CLAIM_AUTHOR_ATTRIBUTION_MISMATCH:' + author, path + ('claim',))
                for work in asserted_names(c['claim'], WORKS):
                    if work != e.get('workId'):
                        add('CLAIM_WORK_ATTRIBUTION_MISMATCH:' + work, path + ('claim',))
        for author in asserted_names(p['text'], AUTHORS):
            if not any(author in by_id.get(c['evidenceId'], {}).get('authorIds', []) for c in p['citations']):
                add('AUTHOR_ATTRIBUTION_MISMATCH:' + author, ('paragraphs', i, 'citations'))
        for work in asserted_names(p['text'], WORKS):
            if not any(work == by_id.get(c['evidenceId'], {}).get('workId') for c in p['citations']):
                add('WORK_ATTRIBUTION_MISMATCH:' + work, ('paragraphs', i, 'citations'))
    return (None if errors else answer), list(dict.fromkeys(errors))


def fallback():
    return dict(status='insufficient',
                paragraphs=[dict(text='这次回答未能通过核验，本次卦象已保留。可以查看原文依据，或继续说明你的问题。', citations=[])],
                limitations=['结构或引用校验修复一次后仍未通过。'])
