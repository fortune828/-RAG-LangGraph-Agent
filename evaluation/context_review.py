"""Offline human review exports. Preserve source ratings; never call a model."""
import argparse
import csv
import hashlib
import html
import json
import random
from collections import Counter
from pathlib import Path


DIMENSIONS = ('理解', '关联', '交流', '帮助')
CTX_TAGS = ('CTX-重复解释', 'CTX-未利用用户修正', 'CTX-重新解释已有状态')
FIELDS = ['编号', *DIMENSIONS, '是否回答到点上', '错误标签', '理由或原句', '评分人', '日期']
RUBRIC = '''# 人工体验评分：从这里开始

本包只评价真实交付的回答。先读问题；追问先读该轮之前的对话，再读回答。
填写 `scores.csv`，四维各取 0 / 1 / 2；不能判断填 NA 并写原因，尚未评分留空。
评分人与日期填实际信息。没有交付回答时四维 NA、回答到点上填否；只有通用失败提示时帮助 0，其余 NA。

| 维度 | 2 | 1 | 0 |
|---|---|---|---|
| 理解 | 理解目标、条件及最新更正 | 局部遗漏或擅自补条件 | 核心误解、沿用已纠正前提 |
| 关联 | 卦意参与理解，联系合理 | 有帮助也有牵强对应 | 主要靠硬套、卦象确认现实或保证结果 |
| 交流 | 自然连贯，能推进或结束当前讨论 | 机械、重复或展开失衡 | 明显妨碍交流或重开已解决问题 |
| 帮助 | 提供有依据的判断、取舍或下一步 | 部分有用，未解决关键矛盾 | 无实质帮助或方向错误 |

“是否回答到点上”填是 / 否 / 不确定，独立于四维平均。理由尽量保留具体原句。
不用字数、段落、引用数或重复词频打分，不要求每轮重讲卦象，也不禁止有必要的回顾。
只用该轮之前的信息评分，不用后续事实倒扣前轮。助手此前的推测不自动成为用户事实。

错误标签可多选，用分号隔开；也可记录其他具体问题。以下三个标签可以同时出现，必须写清理由：

- **CTX-重复解释**：再次讲已解释过的内容，既未回应新的需要，也未推进讨论；用户要求重讲或必要澄清不算。
- **CTX-未利用用户修正**：仍用已被更正的事实、限制或目标作判断；需指出更正及冲突原句。
- **CTX-重新解释已有状态**：把固定卦象、已有决定或已解决条件当成新发现重新展开，妨碍当前交流；必要提醒不算。

你不需要重新算卦、逐字比对引文或给古文释义打专业分。引用原文可按兴趣展开；拿不准的文义写在理由中等待独立知识审查。建议删掉引文后仍然合理，不自动说明关联失败；看卦意是否提供有意义的视角，不要求现实建议依赖经文才成立。

## 三类验证分别记录

1. **机器核验**：起卦与会话一致性、结构、引用ID和逐字引文、可识别的显式归属。程序输出存于本包之外；机械通过不代表解释正确。
2. **知识语义审查**：经典解释、作者观点、条件范围及引用是否支持断言，独立逐命题核对。引用claim只是待审入口，还需检查正文中未引用的传统断言，不能用表内claim覆盖率冒充全部语义审查。AI、普通人工和专家身份分别记录；无法判断留待核实，不将用户体验分包装成专家Gold。
3. **用户体验**：本包四维和错误标签。不读取策略、已有AI评分或解盲键后再打分。原始评价保留，后续汇总不代填未评内容。
'''


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def csv_write(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def normalize_human_scores(source, output_dir):
    """Read the user's Markdown table (or CSV) without altering a single byte."""
    source, output_dir = Path(source), Path(output_dir)
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    if text.lstrip().startswith('|'):
        lines = [line for line in text.splitlines() if line.strip().startswith('|')]
        cells = lambda line: [cell.strip() for cell in line.strip().strip('|').split('|')]
        columns = cells(lines[0])
        source_rows = []
        for line in lines[2:]:
            values = cells(line)
            if len(values) != len(columns):
                raise ValueError('Ambiguous Markdown row; preserve source and fix the importer, not the human ratings')
            source_rows.append(dict(zip(columns, values)))
    else:
        source_rows = list(csv.DictReader(text.splitlines()))
        columns = list(source_rows[0]) if source_rows else []
    rows = []
    for source_row in source_rows:
        scores = {}
        for key in (*DIMENSIONS, '知识'):
            value = source_row.get(key, '').strip()
            if value not in ('', 'NA', 'N/A', '0', '1', '2'):
                raise ValueError(f'Unexpected score at {source_row.get("编号")}: {key}={value}')
            scores[key] = None if value in ('', 'NA', 'N/A') else int(value)
        rows.append(dict(answerId=source_row['编号'], scores=scores, rawFields=source_row,
                         raterType='human_non_expert', expertGold=False,
                         knowledgeScoreNature='User trial judgement, not expert classical verification',
                         contextErrorTags=None, contextErrorTagsState='not_collected'))
    totals = {}
    for key in (*DIMENSIONS, '知识'):
        values = [row['scores'][key] for row in rows if row['scores'][key] is not None]
        totals[key] = dict(n=len(values), missing=len(rows)-len(values), sum=sum(values),
                           mean=sum(values)/len(values) if values else None,
                           distribution=dict(Counter(values)))
    output_dir.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256(raw).hexdigest()
    dump(output_dir/'human-scores.json', dict(source=str(source), sourceSha256=digest,
         sourceFormat='Markdown table' if text.lstrip().startswith('|') else 'CSV',
         note='Original ratings preserved. Null CTX tags mean not collected, not no errors.', rows=rows))
    csv_write(output_dir/'human-scores.csv', columns, source_rows)
    summary = dict(n=len(rows), dimensions=totals,
                   taskJudgement=dict(Counter(row.get('是否回答到点上', '') for row in source_rows)),
                   expertGold=False, representativeSample=False, sourceSha256=digest)
    dump(output_dir/'summary.json', summary)
    lines = ['# 已完成人工试评：原始分数整理', '',
             f'来源 `{source}`，SHA-256 `{digest}`。原文件未改写。', '',
             f'共 {len(rows)} 份，人工非专家评价；按场景挑选，不能估计全体用户满意度。', '',
             '| 项目 | 有效 n | 缺失/NA | 均分 |', '|---|---:|---:|---:|']
    for key, stats in totals.items():
        mean = f'{stats["mean"]:.2f}' if stats['mean'] is not None else 'NA'
        lines.append(f'| {key} | {stats["n"]} | {stats["missing"]} | {mean} |')
    lines += ['', '知识分是用户试评意见，保留但不作为专家文义结论，不与后续四维体验评分混成同一总分。',
              'CTX 三类标签当时未采集，保持缺失；不根据评语自动伪造用户标签。',
              '这批 14 份均无动爻；所对应正式 80 题中 78 题无动爻，独立测试 40/40、生活题 40/40 均无动爻。不能代表完整产品起卦体验。',
              '', '“回答到点上”：' + '；'.join(f'{k} {v}' for k, v in summary['taskJudgement'].items()) + '。', '',
              '逐条原始理由见 human-scores.csv / human-scores.json；不在本整理步骤重打分。']
    (output_dir/'README.md').write_text('\n'.join(lines)+'\n')
    if source.read_bytes() != raw:
        raise RuntimeError('Source changed concurrently; no source writes were made by this exporter')
    return summary


def paragraph_texts(answer):
    if isinstance(answer, str):
        return [answer] if answer else ['未完成可交付回答。']
    return [p['text'] for p in (answer or {}).get('paragraphs', [])] or ['未完成可交付回答。']


def public_history(row):
    history = row.get('history', row.get('inputSession', {}).get('turns', []))
    result = []
    for turn in history:
        if 'question' in turn:
            result.append(dict(question=turn.get('question', ''), answer=turn.get('answer')))
        elif turn.get('role') in ('user', 'assistant'):
            result.append(dict(role=turn['role'], content=turn.get('content', '')))
    return result


def export_experience_pack(rows, output_dir, seed=20261011):
    """Create anonymous MD/HTML/blank CSV; mappings and machine data stay outside."""
    output_dir = Path(output_dir)
    siblings = {key: output_dir.parent/(output_dir.name+'-'+name) for key, name in
                [('key', 'key.json'), ('machine', 'machine-checks.json'), ('knowledge', 'knowledge-review.csv')]}
    if output_dir.exists() or any(path.exists() for path in siblings.values()):
        raise FileExistsError('Refuse to overwrite human material, scores or review mapping')
    groups = {}
    for row in rows:
        groups.setdefault(row.get('groupId', row['recordId']), []).append(row)
    group_ids = list(groups)
    random.Random(seed).shuffle(group_ids)
    ordered = []
    for index, group in enumerate(group_ids, 1):
        ordered.extend((index, row) for row in sorted(groups[group], key=lambda item: item.get('turnIndex', 1)))
    output_dir.mkdir(parents=True)
    md = ['# 匿名体验材料', '', '评分方法见 README.md；当前包不含策略、模型配置或已有评分。按场景阅读，组内保持真实对话顺序。', '']
    articles, materials, keys, machines, knowledge = [], [], [], [], []
    for index, (group_index, row) in enumerate(ordered, 1):
        aid = f'R{index:02}'
        cast = row.get('cast', row.get('inputSession', {}).get('cast', {}))
        answer = row.get('answer')
        material = dict(answerId=aid, scene=f'场景 {group_index:02}', turnIndex=row.get('turnIndex', 1),
                        question=row['question'], coreQuestion=row.get('coreQuestion', row.get('inputSession', {}).get('question', row['question'])),
                        history=public_history(row), cast={key: cast[key] for key in
                            ('lines', 'original', 'changed', 'changingIndices') if key in cast},
                        answer=answer, evidence=row.get('finalGenerationEvidence', row.get('evidence', [])))
        materials.append(material)
        keys.append(dict(answerId=aid, recordId=row['recordId'], groupId=row.get('groupId'),
                         turnIndex=row.get('turnIndex', 1)))
        machines.append(dict(answerId=aid, recordId=row['recordId'], status=row.get('status'),
                             errors=row.get('errors'), error=row.get('error'),
                             sameCast=row.get('sameCast'), repairs=row.get('repairs'),
                             checks=row.get('machineChecks'),
                             validationTrace=[step for step in row.get('trace', []) if step.get('step') in ('validate', 'validation')],
                             note='Captured execution metadata, not semantic verification; absent checks are unknown.'))
        title = f'{aid} · {material["scene"]} · 第 {material["turnIndex"]} 轮'
        md += ['## '+title, '', '**当前用户：** '+row['question'], '']
        h = ['<article><h2>'+html.escape(title)+'</h2><p><b>当前用户：</b>'+html.escape(row['question'])+'</p>']
        if material['coreQuestion'] != row['question']:
            md += ['**最初问题：** '+material['coreQuestion'], '']
            h += ['<p><b>最初问题：</b>'+html.escape(material['coreQuestion'])+'</p>']
        if material['history']:
            md += ['<details><summary>本轮之前的真实对话（追问评分前请阅读）</summary>', '']
            h += ['<details><summary>本轮之前的真实对话（追问评分前请阅读）</summary>']
            for turn in material['history']:
                pairs = [(turn['role'], turn['content'])] if 'role' in turn else [('user', turn['question']), ('assistant', '\n\n'.join(paragraph_texts(turn['answer'])))]
                for role, content in pairs:
                    label = '用户' if role == 'user' else '助手'
                    md += [f'**{label}：**', '', content, '']
                    h += ['<p><b>'+label+'：</b></p><p>'+html.escape(content).replace('\n', '<br>')+'</p>']
            md += ['</details>', '']; h += ['</details>']
        moving = cast.get('changingIndices', [])
        cast_text = '本卦：'+cast.get('original', {}).get('name', '未提供')+'；变卦：'+cast.get('changed', {}).get('name', '未提供')+'；动爻：'+('、'.join(str(i+1) for i in moving) if moving else '无')+'。'
        md += [cast_text, '', '### 实际交付回答', '']
        h += ['<p class="cast">'+html.escape(cast_text)+'</p><section><h3>实际交付回答</h3>']
        for text in paragraph_texts(answer):
            md += [text, '']; h += ['<p>'+html.escape(text).replace('\n', '<br>')+'</p>']
        h += ['</section>']
        md += ['<details><summary>引用与原文（选读，无需为其填写知识分）</summary>', '']
        h += ['<details><summary>引用与原文（选读，无需为其填写知识分）</summary>']
        for pi, paragraph in enumerate((answer or {}).get('paragraphs', []) if isinstance(answer, dict) else [], 1):
            for citation in paragraph.get('citations', []):
                claim = citation.get('claim', '')
                text = f'第{pi}段 · {citation.get("evidenceId", "")}\n待核实的支持关系：{claim}\n引文：{citation.get("quote", "")}'
                md += ['```text', text, '```', '']; h += ['<pre>'+html.escape(text)+'</pre>']
                knowledge.append(dict(answerId=aid, paragraph=pi, claim=claim, evidenceId=citation.get('evidenceId', ''),
                                      quote=citation.get('quote', ''), nature='', support='', reason='', reviewerType='', reviewer=''))
        for evidence in row.get('evidence', []):
            title = ' · '.join(str(evidence.get(key) or '') for key in ('id', 'locator', 'workTitle'))
            text = evidence.get('textOriginal') or evidence.get('text', '')
            md += ['**'+title+'**', '', '> '+text.replace('\n', '\n> '), '']
            h += ['<p><b>'+html.escape(title)+'</b></p><blockquote>'+html.escape(text).replace('\n', '<br>')+'</blockquote>']
        md += ['</details>', '', '---', '']; h += ['</details></article>']; articles.append(''.join(h))
    dump(output_dir/'materials.json', materials)
    (output_dir/'materials.md').write_text('\n'.join(md).rstrip()+'\n')
    (output_dir/'materials.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>匿名体验材料</title><style>body{max-width:900px;margin:2rem auto;padding:0 1rem;font:17px/1.8 sans-serif}article{border-top:1px solid #ccc;margin:2rem 0}section{background:#f5f6f8;padding:1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}blockquote{border-left:3px solid #ccc;padding-left:1rem}.cast{color:#555}</style><h1>匿名体验材料</h1><p>先读README.md，再填写scores.csv。组内按时间顺序阅读，不含已有评分。</p>'+''.join(articles)+'</html>\n')
    (output_dir/'README.md').write_text(RUBRIC)
    csv_write(output_dir/'scores.csv', FIELDS, [{'编号':item['answerId']} for item in materials])
    dump(siblings['key'], dict(seed=seed, rows=keys))
    dump(siblings['machine'], machines)
    csv_write(siblings['knowledge'], ['answerId', 'paragraph', 'claim', 'evidenceId', 'quote', 'nature', 'support', 'reason', 'reviewerType', 'reviewer'], knowledge)
    return dict(count=len(materials), output=str(output_dir), **{key:str(value) for key, value in siblings.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['normalize', 'pack'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = normalize_human_scores(args.source, args.destination) if args.mode == 'normalize' else export_experience_pack(json.loads(args.source.read_text()), args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
