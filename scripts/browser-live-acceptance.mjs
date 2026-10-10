// Explicit paid development acceptance. Connect to a separately budgeted real
// Python server; this script never installs API mocks or retries model failures.
import { chromium } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

const baseUrl = process.env.BASE_URL;
const outputDir = process.env.BROWSER_OUTPUT_DIR;
const dataDir = process.env.DATA_DIR;
assert.ok(baseUrl && outputDir && dataDir, 'Set BASE_URL, BROWSER_OUTPUT_DIR and temporary DATA_DIR explicitly');
assert.ok(process.argv.includes('--live'), 'Pass --live to authorize real model calls');
mkdirSync(outputDir, { recursive: true });
const hashes = () => Object.fromEntries(['backend/app.py','backend/graph.py','backend/prompts.py','backend/validation.py','src/App.tsx'].map(path => [path, createHash('sha256').update(readFileSync(path)).digest('hex')]));
const report = { mode: 'real_browser_python_api_real_model_development', humanReviewed: false,
  baseUrl, startedAt: new Date().toISOString(), codeSha256: hashes(), castMode: 'production_random_three_coins',
  automaticRetry: false, cases: [], consoleErrors: [], passed: false };
const save = () => {
  const budgetFile = `${outputDir}/budget.json`;
  if (existsSync(budgetFile)) report.budget = JSON.parse(readFileSync(budgetFile));
  writeFileSync(`${outputDir}/report.json`, JSON.stringify(report, null, 2));
};
const checkpointCounts = ids => JSON.parse(execFileSync('.venv/bin/python', ['-c',
  'import json,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); ids=json.loads(sys.argv[2]); print(json.dumps({t:sum(c.execute("SELECT count(*) FROM "+t+" WHERE thread_id = ?",(i,)).fetchone()[0] for i in ids) for t in ["checkpoints","writes"]}))',
  `${dataDir}/checkpoints.sqlite`, JSON.stringify(ids)], { encoding: 'utf8' }));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
page.on('pageerror', error => report.consoleErrors.push(error.message));
const scenarios = [
  { id: 'job-change', questions: ['我想换工作，但担心收入不稳定，应该怎样整理进退的思路？', '补充一下：我只有三个月积蓄，还没有拿到新公司的录用通知，下一步可以先准备什么？', '请查出本卦第二爻的原文，说明出处，并说明它与当前处境的关系。'] },
  { id: 'family-communication', questions: ['我和父母对搬出去独立生活有分歧，希望兼顾独立和关系，该怎样开始沟通？', '我现在收入可以覆盖租金，每周也能回家一次，但父母担心我离家后就不再关心他们。怎样把这些条件说清楚？', '请查出本卦第二爻的原文，说明出处，并说明它与当前处境的关系。'] },
];
save();
try {
  report.health = await (await page.request.get(`${baseUrl}/api/health`)).json();
  assert.equal(report.health.backend, 'python');
  assert.equal(report.health.configured, true);
  for (const scenario of scenarios) {
    const row = { id: scenario.id, sessions: [], citationChecks: [], humanReviewed: false };
    report.cases.push(row);
    await page.goto(baseUrl);
    for (let index = 0; index < scenario.questions.length; index++) {
      await page.getByLabel(index === 0 ? '此刻，你想探讨什么？' : '沿用当前卦象，继续追问').fill(scenario.questions[index]);
      const responsePromise = page.waitForResponse(r => r.url().endsWith('/turns') && r.request().method() === 'POST', { timeout: 180000 });
      await page.getByRole('button', { name: index === 0 ? '起卦并解读' : '继续探讨', exact: true }).click();
      const response = await responsePromise;
      const session = await response.json();
      row.sessions.push(session);
      save();
      assert.equal(response.status(), 200);
      assert.equal(session.turns.length, index + 1);
      const turn = session.turns[index];
      assert.equal(turn.status, 'completed', turn.error || 'Turn did not complete');
      assert.equal(turn.answer?.status, 'answered', 'Business insufficient is retained but not counted as successful acceptance');
      assert.deepEqual(session.cast, row.sessions[0].cast);
      const card = page.locator('.turn-card').nth(index);
      await card.locator('.answer-summary').waitFor();
      if (index !== 1) {
        const citation = card.locator('.reference-marker a').first();
        await citation.click();
        const target = await citation.getAttribute('href');
        assert.equal(await page.locator(target).getAttribute('open'), '');
        assert.equal(await card.locator('.answer-references').getAttribute('open'), '');
        row.citationChecks.push({ turn: index + 1, target, opened: true });
      }
      if (index === 2) {
        const target = `zy-${session.cast.original.number}-line-2`;
        assert.ok(turn.answer.paragraphs.some(p => p.citations.some(c => c.evidenceId === target)));
        row.sourceQueryToolCalls = turn.trace.filter(event => event.step === 'tool');
      }
      await page.screenshot({ path: `${outputDir}/${scenario.id}-turn-${index + 1}.png`, fullPage: true });
      save();
    }
    const session = row.sessions.at(-1);
    const restoreResponse = page.waitForResponse(r => new URL(r.url()).pathname === `/api/sessions/${session.id}` && r.request().method() === 'GET');
    await page.reload();
    const restored = await (await restoreResponse).json();
    assert.deepEqual(restored.cast, session.cast);
    assert.deepEqual(restored.turns.map(turn => turn.id), session.turns.map(turn => turn.id));
    await page.locator('.turn-card').nth(2).waitFor();
    assert.equal(await page.locator('.turn-card').count(), 3);
    assert.ok((await page.locator('.cast-values').innerText()).includes(session.cast.lines.join(' · ')));
    row.restoredAfterReload = true;
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    row.mobileOverflow = false;
    await page.screenshot({ path: `${outputDir}/${scenario.id}-mobile.png`, fullPage: true });
    const threadIds = session.turns.map(turn => turn.checkpointThreadId);
    assert.ok(threadIds.every(Boolean));
    row.checkpointsBeforeDelete = checkpointCounts(threadIds);
    assert.ok(row.checkpointsBeforeDelete.checkpoints >= 3);
    await page.getByRole('button', { name: '删除本次会话' }).click();
    await page.getByLabel('此刻，你想探讨什么？').waitFor();
    row.checkpointsAfterDelete = checkpointCounts(threadIds);
    assert.deepEqual(row.checkpointsAfterDelete, { checkpoints: 0, writes: 0 });
    assert.equal(existsSync(`${dataDir}/${session.id}.json`), false);
    row.deleted = true;
    save();
    await page.setViewportSize({ width: 1360, height: 1000 });
  }
  assert.deepEqual(report.consoleErrors, []);
  assert.ok(report.cases.some(row => row.sourceQueryToolCalls.some(event => event.status === 'ok')), 'At least one source query must exercise retrieval; existing evidence need not be fetched twice');
  assert.deepEqual(hashes(), report.codeSha256, 'Code changed during acceptance');
  report.passed = true;
} catch (error) {
  report.failure = { name: error.name, message: error.message };
  await page.screenshot({ path: `${outputDir}/failure.png`, fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  save();
  await browser.close();
  console.log(JSON.stringify({ report: `${outputDir}/report.json`, passed: report.passed, budget: report.budget, failure: report.failure }));
}
