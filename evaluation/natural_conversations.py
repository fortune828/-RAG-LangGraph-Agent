"""Actual graph multiround companion to the isolated generation comparison."""
import asyncio,copy,json,time,subprocess
from pathlib import Path
from langgraph.checkpoint.memory import InMemorySaver
from backend.config import ROOT,MODEL
from backend.graph import build_graph,initial_state,GRAPH_VERSION
from backend.prompts import PROMPT_VERSION
from backend.llm import request_payload
from backend.knowledge import CURRENT_RELEASE
from backend.evaluate import BudgetedChat
from evaluation.paired import Recorder,RecordedChat,load_cases,write_json,sha
async def run(out):
 out.mkdir(parents=True, exist_ok=False)
 questions=[['我最近在读《易经》，想借这卦聊换工作。我还没有拿到录用通知，积蓄大概够三个月。','纠正一下，我只有一个月积蓄，还要付房租。','我不是想马上辞职，只想知道这周可以先做什么。不要替我规定几个月的存款目标。'],['我今天中午吃啥好？','那你具体替我选一道吧，我不想再比较了。','今天不想吃面，也不吃辣，换一个简单的就行。']]
 cases=load_cases()['cases'];rows=[];budget=BudgetedChat(max_calls=24,max_tokens=300000)
 files=list((ROOT/'backend').glob('*.py')) + list((ROOT/'evaluation').glob('*.py')) + [ROOT/'requirements.txt']
 for p in files:
  t=out/'source'/p.relative_to(ROOT);t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(p.read_bytes())
 write_json(out/'manifest.json',dict(mode='real_model_actual_graph_multiturn',graphVersion=GRAPH_VERSION,promptVersion=PROMPT_VERSION,releaseId=CURRENT_RELEASE,model=MODEL,modelOptions=request_payload([]),commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),questions=questions,sourceHashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files}))
 for gi,qs in enumerate(questions):
  case=next(c for c in cases if c['caseId']==('life-correction' if gi==0 else 'lunch-choice'))
  session=copy.deepcopy(case['session']);session['turns']=[];session['question']=qs[0]
  for ti,q in enumerate(qs):
   rid=f'{gi+1}-{ti+1}';rec=Recorder(out/f'{rid}.jsonl',rid,'D-'+PROMPT_VERSION.rsplit('-',1)[-1]);started=time.monotonic();row=dict(id=rid,question=q,input=copy.deepcopy(session))
   try:
    result=await build_graph(InMemorySaver(),RecordedChat(budget,rec),observe=rec).ainvoke(initial_state(session,q,'initial' if ti==0 else 'followup'),{'configurable':{'thread_id':rid}})
    turn=dict(id=rid,status='failed' if result['errors'] else 'completed',question=q,answer=result['answer'],evidence=result['evidence'],trace=result['trace'])
    row.update(turn,errors=result['errors'],sameCast=result['session']['cast']==session['cast'])
    session['turns'].append(turn)
   except Exception as exc: row.update(status='failed',error=str(exc))
   row['durationMs']=round((time.monotonic()-started)*1000);rows.append(row)
   write_json(out/'rows.json',rows);write_json(out/'budget.json',budget.summary());print(rid,row['status'],flush=True)
 lines=['# 实际生产图连续对话（开发，待人工判断）','']
 for r in rows:
  lines+=['## '+r['id']+' '+r['question'],'']+[p['text']+'\n' for p in r.get('answer',{}).get('paragraphs',[])]
 (out/'answers.md').write_text('\n'.join(lines))
 print(json.dumps(budget.summary()))
if __name__ == '__main__':
 import argparse
 parser=argparse.ArgumentParser('Six real production-graph development turns; no automatic semantic judge.')
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args()
 asyncio.run(run(args.output))
