import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tsImport } from 'tsx/esm/api';
import assert from 'node:assert/strict';
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:3001';
const outputDir = process.env.BROWSER_OUTPUT_DIR || `evaluation/results/browser-${new Date().toISOString().replace(/[:.]/g, '-')}`;
const live = process.argv.includes('--live');
const browser = await chromium.launch({ headless:true, executablePath:process.env.CHROMIUM_PATH || undefined, args:['--no-sandbox'] });
const page = await browser.newPage({viewport:{width:1360,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const responses=[];page.on('response',async r=>{ if(r.url().includes('/turns')&&r.status()===200) responses.push(await r.json()); });
mkdirSync(outputDir,{recursive:true});
let fixtureState;
let calculateFixture;
// Default browser run verifies rendering and browser state using explicit fixture API responses.
// It makes no LLM requests; --live opts into the configured backend and its usage.
if (!live) {
  const { calculateHexagram } = await tsImport('../src/lib/yijing.ts', import.meta.url);
  calculateFixture = calculateHexagram;
  const { getPassages, CURRENT_RELEASE_ID } = await tsImport('../server/knowledge.ts', import.meta.url);
  const baseEvidence = getPassages({ hexagramId: 1, sections: ['xiang'] });
  const lineEvidence = getPassages({ hexagramId: 1, sections: ['line'], lineIndices: [2] });
  const annotation = getPassages({ hexagramId: 1, sections: ['line'], lineIndices: [2], authorIds: ['zhu-xi'], layers: ['commentary'] });
  assert.ok(annotation.length, 'Browser metadata fixture needs an actual imported Zhu Xi passage');
  await page.route('**/api/**', async route => {
    let state = fixtureState;
    const request = route.request(), path = new URL(request.url()).pathname;
    const payload = request.method() === 'POST' ? request.postDataJSON() : undefined;
    if (request.method() === 'DELETE') { fixtureState = undefined; return route.fulfill({ status: 204 }); }
    if (path === '/api/sessions' && payload) {
      state = { id: payload.requestId, question: payload.question, releaseId: CURRENT_RELEASE_ID, createdAt: 'ui-mock', turns: [],
        cast: { ...calculateHexagram([7,7,7,7,7,7]), coins: Array.from({length:6},()=>[0,0,1]), id: 'ui-mock-cast', method: 'three-coins', algorithmVersion: 'ui-mock', createdAt: 'ui-mock' } };
    } else if (path.endsWith('/turns') && payload && state) {
      const initial = !state.turns.length, evidence = initial ? baseEvidence : [...lineEvidence, ...annotation];
      const turn = { id: payload.requestId, question: payload.question, mode: initial ? 'initial' : 'followup', status: 'completed', createdAt: 'ui-mock', model: 'ui-fixture-no-model-call', evidence,
        answer: { status: 'answered', summary: '浏览器测试固定样例（不是实时模型回答）。', paragraphs: evidence.map(e => ({ text: '仅用于验证原文与注家引用卡展示。', citations: [{ evidenceId: e.id, quote: e.text.slice(0, 8) }] })), limitations: ['UI fixture，不作为模型质量证据。'] },
        trace: [{ step: 'validate', status: 'passed' }] };
      if (payload.retry) state.turns = state.turns.map(t => t.id === payload.requestId ? turn : t);
      else state.turns.push(turn);
    }
    fixtureState = state;
    return route.fulfill({ status: state ? 200 : 404, contentType: 'application/json', body: JSON.stringify(state || { error: 'NOT_FOUND' }) });
  });
}
try {
  await page.goto(baseUrl);
  await page.getByLabel('此刻，你想探讨什么？').waitFor();
  await page.screenshot({path:`${outputDir}/home.png`,fullPage:true});
  await page.getByLabel('此刻，你想探讨什么？').fill('面对工作变化，我该如何思考进退与合作？');
  await page.getByRole('button',{name:'起卦并解读'}).click();
  await page.locator('.turn-card .answer-summary').waitFor({timeout:180000});
  const before=await page.locator('.cast-values').innerText();
  assert.equal(await page.locator('.error-box').count(),0);
  assert.equal(await page.locator('.answer-references[open]').count(),0);
  assert.equal(await page.locator('.trace[open]').count(),0);
  if (!live) {
    assert.equal(await page.locator('.cast-grid > div').count(),1);
    await page.locator('.cast-method > summary').click();
    const rows = await page.locator('.cast-method li').allTextContents();
    assert.equal(rows.length,6);
    assert.ok(rows[0].includes('初爻：记录 0、0、1 → 2 + 2 + 3 = 7'));
    assert.ok(rows[5].startsWith('上爻'));
  }
  await page.getByLabel('沿用当前卦象，继续追问').fill('请查出本卦第二爻的原文，并说明出处。');
  await page.getByRole('button',{name:'继续探讨'}).click();
  await page.locator('.turn-card').nth(1).locator('.answer-summary').waitFor({timeout:180000});
  assert.equal(await page.locator('.cast-values').innerText(),before);
  const last=responses.at(-1);
  assert.equal(last.turns.length,2);
  assert.equal(last.turns[1].status,'completed');
  const correctId=`zy-${last.cast.original.number}-line-2`;
  assert.ok(last.turns[1].answer.paragraphs.some(p=>p.citations.some(c=>c.evidenceId===correctId)));
  await page.locator('.turn-card').nth(1).locator('.reference-marker a').first().click();
  assert.equal(await page.locator('.turn-card').nth(1).locator('.answer-references').getAttribute('open'),'');
  assert.equal(await page.locator('.turn-card').nth(1).locator('.citation').first().getAttribute('open'),'');
  if (!live) {
    const annotationCard = page.locator('.turn-card').nth(1).locator('.citation').last();
    await annotationCard.locator('summary').click();
    assert.ok((await annotationCard.innerText()).includes('朱熹'));
    assert.ok(await annotationCard.locator('a').getAttribute('href'));
  }
  await page.screenshot({path:`${outputDir}/conversation.png`,fullPage:true});
  await page.reload();await page.locator('.turn-card').nth(1).waitFor();
  assert.equal(await page.locator('.cast-values').innerText(),before);
  assert.equal(await page.locator('.answer-references[open]').count(),0);
  await page.getByLabel('沿用当前卦象，继续追问').fill('我只有三个月积蓄，可以先做什么准备？');
  if (!live) {
    await page.getByRole('button',{name:'继续探讨'}).click();
    await page.locator('.turn-card').nth(2).locator('.answer-summary').waitFor();
    assert.equal(await page.locator('.cast-values').innerText(),before);
  }
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:`${outputDir}/mobile.png`,fullPage:true});
  const report={passed:true,mode:live?'live_backend':'mock_api_ui_only',baseUrl,outputDir,consoleErrors:errors,turns:last.turns.map(t=>({status:t.status,toolCalls:t.trace.filter(e=>e.step==='tool').length,validation:t.trace.filter(e=>e.step==='validate').map(e=>e.status)})),castPreserved:true,restoredAfterReload:true,mobileOverflow:false};
  if (!live) {
    const mobileCases = [];
    for (const kind of ['no-moving','moving','legacy']) {
      const lines = kind === 'moving' ? [6,7,8,9,7,8] : [7,7,7,7,7,7];
      fixtureState.cast = {...fixtureState.cast,...calculateFixture(lines),coins:lines.map(v=>Array.from({length:3},(_,i)=>i<v-6?1:0))};
      if (kind === 'legacy') delete fixtureState.cast.coins;
      for (const width of [1360,390]) {
        await page.setViewportSize({width,height:844});
        await page.reload();
        await page.locator('.cast-method > summary').click();
        assert.equal(await page.locator('.cast-grid > div').count(),kind === 'moving' ? 2 : 1);
        if (kind === 'legacy') assert.ok((await page.locator('.cast-method').innerText()).includes('旧记录未保存单币'));
        else {
          const rows = await page.locator('.cast-method li').allTextContents();
          rows.forEach((text,i)=>assert.ok(text.endsWith(`= ${lines[i]}`)));
          fixtureState.cast.coins.forEach((row,i)=>assert.equal(row.reduce((sum,bit)=>sum+bit+2,0),lines[i]));
        }
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
        mobileCases.push({kind,width,passed:true});
      }
    }
    report.castDisplay = mobileCases;
    const errorCases = ['MODEL_TIMEOUT_OR_NETWORK','RATE_LIMITED','SESSION_BUSY','SERVER_BUSY','RELEASE_UNAVAILABLE','CORPUS_HASH_MISMATCH','ANSWER_VALIDATION_FAILED','CHECKPOINT_VERSION_MISMATCH'];
    const errorChecks = [];
    for (const code of errorCases) {
      const id = `fixture-error-${code}`;
      fixtureState.turns.push({id,question:'受控故障测试',mode:'followup',status:'failed',error:code,evidence:[],trace:[],model:'fixture',createdAt:'ui-mock'});
      await page.reload();
      const failed = page.locator('.turn-card').last();
      await failed.locator('.error-box').waitFor();
      const message = await failed.locator('.error-box').innerText();
      assert.ok(!message.includes(code));
      const castId = fixtureState.cast.id, count = fixtureState.turns.length;
      if (code === 'CHECKPOINT_VERSION_MISMATCH') {
        assert.equal(await failed.getByRole('button',{name:'保留卦象，重试解释'}).count(),0);
        await failed.getByRole('link',{name:'沿用本卦重新提问'}).click();
        assert.equal(await page.locator('#followup').evaluate(el=>el===document.activeElement),true);
        await page.locator('#followup').fill('沿用原卦重新整理我的顾虑');
        await page.getByRole('button',{name:'继续探讨'}).click();
        await page.locator('.turn-card').last().locator('.answer-summary').waitFor();
        assert.equal(fixtureState.turns.length,count+1);
      } else {
        await failed.getByRole('button',{name:'保留卦象，重试解释'}).click();
        await failed.locator('.answer-summary').waitFor();
        assert.equal(fixtureState.turns.length,count);
        assert.equal(fixtureState.turns.at(-1).id,id);
      }
      assert.equal(fixtureState.cast.id,castId);
      errorChecks.push({code,message,nextActionPassed:true});
    }
    report.errorChecks = errorChecks;
    report.errorCheckNote = 'UI fixtures verify messages and actions; backend retry semantics require Python integration tests. Source restoration and rate/busy recovery are simulated.';
  }
  assert.deepEqual(errors,[]);
  await page.getByRole('button',{name:'删除本次会话'}).click();
  await page.getByLabel('此刻，你想探讨什么？').waitFor();
  report.deleted=true;
  writeFileSync(`${outputDir}/report.json`,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close()}
