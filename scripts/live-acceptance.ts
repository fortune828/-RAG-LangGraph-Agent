/** Explicit opt-in, real-browser / real-model acceptance; never run at startup. */
import { chromium, type Page } from '@playwright/test';
import express from 'express';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { createApp } from '../server/app';
import { Store } from '../server/store';
import { settings } from '../server/config';
import { deepseekChat, ModelError, type Chat } from '../server/llm';
import { budgetedChat } from './lib/model-budget';
import { CURRENT_RELEASE_ID, corpus, initialEvidence } from '../server/knowledge';
import { PROMPT_VERSION } from '../server/agent';
import type { Session } from '../shared/types';

if (!process.argv.includes('--live')) throw new Error('Use --live to authorize real model requests');
const casesPath = process.argv.find(a => a.startsWith('--cases='))?.slice(8) || 'evaluation/results/live-acceptance-20261008-cases.json';
const input = JSON.parse(readFileSync(casesPath, 'utf8'));
const cases: {id:string; questions:string[]}[] = (Array.isArray(input) ? input : input.cases).map((c:any)=>({...c,questions:c.questions.map((q:any)=>typeof q==='string'?q:q.question)}));
const outputDir = resolve(`evaluation/results/live-acceptance-20261008-${new Date().toISOString().slice(11,19).replaceAll(':','')}`);
mkdirSync(outputDir,{recursive:true});
const save = (file:string,value:unknown) => writeFileSync(join(outputDir,file),JSON.stringify(value,null,2)+'\n');
const privateDir = mkdtempSync(join(tmpdir(),'yijing-acceptance-'));
const store = new Store(privateDir);
const modelCalls: unknown[] = [];
let consecutiveFailures=0, injectedFailure=false, stopReason:string|undefined;
let activeCase='',activeTurn=0;
const observed: Chat = async(messages,options) => {
  if (consecutiveFailures>=2) throw new ModelError('ACCEPTANCE_STOP_AFTER_TWO_API_FAILURES');
  const start=Date.now();
  try {
    const result=await deepseekChat(messages,options);
    modelCalls.push({caseId:activeCase,turn:activeTurn,at:new Date().toISOString(),durationMs:Date.now()-start,
      model:result.model,usage:result.usage,response:result.message,toolDefinitions:options?.tools?.length||0});
    consecutiveFailures=0; save('model-calls.json',modelCalls); return result;
  } catch(e) {
    consecutiveFailures++; const code=e instanceof ModelError?e.code:'UNEXPECTED_MODEL_ERROR';
    modelCalls.push({caseId:activeCase,turn:activeTurn,durationMs:Date.now()-start,error:code});
    save('model-calls.json',modelCalls); throw e;
  }
};
const {chat:bounded,budget}=budgetedChat(observed,60,500000);
const chat:Chat=async(messages,options)=>{if(injectedFailure){injectedFailure=false;throw new ModelError('MODEL_TIMEOUT_OR_NETWORK');}return bounded(messages,options);};
const report:any={mode:'real_browser_real_model_agent_assisted_acceptance',humanReviewed:false,createdAt:new Date().toISOString(),
  runnerSha256:createHash('sha256').update(readFileSync('scripts/live-acceptance.ts')).digest('hex'),
  configured:Boolean(settings.key),model:settings.model,releaseId:CURRENT_RELEASE_ID,promptVersion:PROMPT_VERSION,
  corpusSha256:createHash('sha256').update(readFileSync(`data/knowledge/releases/${CURRENT_RELEASE_ID}/evidence.json`)).digest('hex'),
  cases:[],extra:[],budget,limitations:['Agent-generated questions and agent-assisted review, not independent human acceptance or held-out quality evaluation.','Random production casts, not preset demonstration results.']};
const checkpoint=()=>save('report.json',report);
console.log(JSON.stringify({outputDir,configured:Boolean(settings.key),model:settings.model}));
checkpoint();
if(!settings.key){report.stopReason='API_KEY_MISSING';checkpoint();rmSync(privateDir,{recursive:true,force:true});process.exit(1);}
const app=createApp(store,chat);app.use(express.static(resolve('dist')));
const server=app.listen(0,'127.0.0.1');await new Promise<void>(r=>server.once('listening',r));
const address=server.address();if(!address||typeof address==='string')throw new Error('Missing server port');
const baseUrl=`http://127.0.0.1:${address.port}`;
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/home/ubuntu/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',args:['--no-sandbox']});
let lastPage:Page|undefined,lastSession:Session|undefined;
const errors:string[]=[];
async function turn(page:Page,question:string,initial=false,retry=false){
  const callsBefore=budget.calls,tokensBefore=budget.reportedTokens;
  const promise=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/turns'),{timeout:360000});
  if(retry)await page.getByRole('button',{name:'保留卦象，重试解释'}).last().click();
  else {
    await page.getByLabel(initial?'此刻，你想探讨什么？':'沿用当前卦象，继续追问').fill(question);
    await page.getByRole('button',{name:initial?'起卦并解读':'继续探讨',exact:true}).click();
  }
  const response=await promise;
  if(!response.ok())throw new Error(`APP_HTTP_${response.status()}`);
  const session:Session=await response.json();
  await page.locator('.loading').waitFor({state:'hidden',timeout:15000});
  const t=session.turns.at(-1)!;
  const file=`${activeCase}-turn-${activeTurn}${retry?'-retry':''}`;
  save(`${file}.json`,session);
  await page.screenshot({path:join(outputDir,`${file}.png`),fullPage:true});
  const result={question,status:t.status,answerStatus:t.answer?.status,error:t.error,sessionFile:`${file}.json`,screenshot:`${file}.png`,
    modelCalls:budget.calls-callsBefore,totalReportedTokens:budget.reportedTokens-tokensBefore,cumulativeModelCalls:budget.calls,cumulativeReportedTokens:budget.reportedTokens,tools:t.trace.filter(e=>e.step==='tool'),validation:t.trace.filter(e=>e.step==='validate'),
    loadingEnded:await page.locator('.loading').count()===0};
  console.log(JSON.stringify({case:activeCase,turn:activeTurn,status:t.status,answerStatus:t.answer?.status,tools:result.tools.map(t=>t.tool),calls:budget.calls,tokens:budget.reportedTokens}));
  lastSession=session;return {session,result};
}
try{
 for(const c of cases){
  if(consecutiveFailures>=2||budget.stopped){stopReason=consecutiveFailures>=2?'TWO_CONSECUTIVE_API_FAILURES':'BUDGET_EXHAUSTED';break;}
  activeCase=c.id;
  const context=await browser.newContext({viewport:{width:1360,height:1000}}),page=await context.newPage();lastPage=page;
  page.on('pageerror',e=>errors.push(e.message));await page.goto(baseUrl);
  const entry:any={id:c.id,turns:[]};report.cases.push(entry);let baseline='';
  for(let i=0;i<c.questions.length;i++){
   activeTurn=i+1;
   if(consecutiveFailures>=2||budget.stopped){entry.notRun=c.questions.slice(i);break;}
   const {session,result}=await turn(page,c.questions[i],i===0);entry.turns.push(result);
   baseline ||= JSON.stringify(session.cast);result['castPreserved' as keyof typeof result]= (JSON.stringify(session.cast)===baseline) as never;
   checkpoint();
  }
  if(lastSession){
   const before=lastSession;await page.reload();await page.locator('.turn-card').last().waitFor({timeout:20000});
   entry.refresh={turnCount:await page.locator('.turn-card').count(),expectedTurnCount:before.turns.length,
    castVisible:(await page.locator('.cast-values').innerText()).includes(before.cast.lines.join(' · ')),releaseId:before.releaseId};
   const refs=page.locator('.turn-card').last().locator('.citation');
   if(await refs.count()){await refs.first().locator('summary').first().click();entry.referenceExpanded=await refs.first().evaluate(e=>(e as HTMLDetailsElement).open);}
   await page.setViewportSize({width:390,height:844});entry.mobileOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
   await page.screenshot({path:join(outputDir,`${c.id}-mobile-restored.png`),fullPage:true});await page.setViewportSize({width:1360,height:1000});checkpoint();
  }
 }
 if(lastPage&&lastSession&&consecutiveFailures<2&&!budget.stopped){
  const p=lastPage;activeCase='targeted';let n=0;
  const currentIds=new Set([...initialEvidence(lastSession.cast,'',lastSession.releaseId),...lastSession.turns.filter(t=>t.status==='completed').slice(-2).flatMap(t=>t.evidence)].map(e=>e.id));
  const target=corpus.find(e=>e.hexagramId===lastSession!.cast.original.number&&e.section==='line'&&e.layer==='classic'&&!currentIds.has(e.id));
  const extraQuestions=[...(target?[`请给出本卦第${target.line}爻的原文，并结合它说明刚才的问题，注明出处。`]:[]),
    '我不知道“履霜，坚冰至”出自哪一卦哪一爻。请查出原文和出处，再解释它能给日常处理小问题什么提醒。',
    '请给出焦循《易章句》对本卦的完整注解原文。没有收录就明确说明，不要用其他书替代。'];
  for(const q of extraQuestions){if(consecutiveFailures>=2||budget.stopped)break;activeTurn=++n;const r=await turn(p,q);report.extra.push(r.result);checkpoint();}
  if(consecutiveFailures<2&&!budget.stopped){
   activeCase='fault-injection';activeTurn=1;injectedFailure=true;
   const q='沿用刚才的结果，再简短提醒我一个今天可以尝试的小行动。';
   const beforeCalls=budget.calls, failed=await turn(p,q);report.faultInjection={simulated:'MODEL_TIMEOUT_OR_NETWORK before provider request',failure:failed.result,paidCallsForFailure:budget.calls-beforeCalls};
   checkpoint();const retry=await turn(p,q,false,true);
   report.faultInjection.retry=retry.result;report.faultInjection.sameCast=JSON.stringify(failed.session.cast)===JSON.stringify(retry.session.cast);
   report.faultInjection.sameTurnId=failed.session.turns.at(-1)?.id===retry.session.turns.at(-1)?.id;
   report.faultInjection.sameTurnCount=failed.session.turns.length===retry.session.turns.length;checkpoint();
  }
 }
}catch(e){report.runnerError=e instanceof Error?e.message:'UNEXPECTED_RUNNER_ERROR';console.log(JSON.stringify({runnerError:report.runnerError}));}
finally{
 report.stopReason=stopReason||(consecutiveFailures>=2?'TWO_CONSECUTIVE_API_FAILURES':budget.stopped?'BUDGET_EXHAUSTED':undefined);
 report.notRunCaseIds=cases.filter(c=>!report.cases.some((r:any)=>r.id===c.id)).map(c=>c.id);report.consoleErrors=errors;
 await browser.close();await new Promise<void>((r,j)=>server.close(e=>e?j(e):r()));rmSync(privateDir,{recursive:true,force:true});
 report.closedTemporaryResources=true;report.completedAt=new Date().toISOString();checkpoint();console.log(JSON.stringify({outputDir,budget,stopReason:report.stopReason,runnerError:report.runnerError}));
}
