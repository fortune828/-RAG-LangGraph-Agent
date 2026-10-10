"""Auditable text lengths / instruction-fragment counts, not token or quality metrics."""
import ast
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
BASELINE='2456f97'


def source(name,old):
    return subprocess.check_output(['git','show',f'{BASELINE}:backend/{name}.py'],cwd=ROOT).decode() if old else (ROOT/f'backend/{name}.py').read_text()


def prompt_values(text):
    return {n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(text).body
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('SYSTEM','ROUTER','RISK_SYSTEM')}


def fragments(text):
    # JSON examples count as one protocol block each; prose splits at full stops,
    # semicolons and newlines. This is a syntactic proxy, not semantic obligations.
    prose=text; blocks=[]
    decoder=json.JSONDecoder();cursor=0
    while cursor<len(prose):
        start=prose.find('{',cursor)
        if start<0:break
        try:obj,length=decoder.raw_decode(prose[start:])
        except ValueError:cursor=start+1;continue
        blocks.append(prose[start:start+length]);prose=prose[:start]+prose[start+length:];cursor=start
    return [s.strip() for s in re.split(r'[。；\n]+',prose) if s.strip()] + blocks


def dynamic(text):
    # All prose-bearing graph literals delivered as instructions, plus the
    # annotation target label. Data keys and computed evidence content excluded.
    funcs={'generation_messages','route','repair','target_label'}
    result=[]
    for n in ast.walk(ast.parse(text)):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in funcs:
            for v in ast.walk(n):
                if isinstance(v,ast.Constant) and isinstance(v.value,str):
                    t=v.value
                    if len(t)>=15 and re.search('[\u4e00-\u9fff]',t) or t.startswith('注解材料') or t == '本轮须引用对应资料。':
                        result.append(t)
    return list(dict.fromkeys(result))


def descriptions(text):
    for n in ast.walk(ast.parse(text)):
        if isinstance(n,ast.Tuple) and len(n.elts)==3 and isinstance(n.elts[0],ast.Constant) and n.elts[0].value in ('get_passages','search_knowledge'):
            yield ast.literal_eval(n.elts[2])


rows=[]
for name in ('SYSTEM','ROUTER','RISK_SYSTEM','GRAPH_DYNAMIC','TOOL_DESCRIPTIONS'):
    row={'name':name}
    for old,version in ((True,'v15'),(False,'v16')):
        texts=([prompt_values(source('prompts',old))[name]] if name in ('SYSTEM','ROUTER','RISK_SYSTEM') else
               dynamic(source('graph',old)) if name=='GRAPH_DYNAMIC' else list(descriptions(source('knowledge',old))))
        pieces=[f for t in texts for f in fragments(t)]
        row[version]=dict(characters=sum(map(len,texts)),utf8Bytes=sum(len(t.encode()) for t in texts),
                          instructionFragments=len(pieces),texts=texts,fragments=pieces)
    rows.append(row)
result=dict(baseline=BASELINE,method='len(str), UTF-8 bytes; prose split on Chinese full stop/semicolon/newline, JSON object examples each count once. Distinct graph instruction literals counted once, not the total runtime context. Counts are syntactic and not claims about semantic rules or tokens.',rows=rows)
result['totals']={v:{k:sum(r[v][k] for r in rows) for k in ('characters','utf8Bytes','instructionFragments')} for v in ('v15','v16')}
Path(__file__).with_name('prompt-metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({r['name']:{v:{k:r[v][k] for k in ('characters','instructionFragments')} for v in ('v15','v16')} for r in rows},ensure_ascii=False))
print(result['totals'])
