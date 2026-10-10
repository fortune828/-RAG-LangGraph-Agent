import test from 'node:test';
import assert from 'node:assert/strict';
import { budgetedChat } from '../scripts/lib/model-budget';
import type { Chat } from '../server/llm';

const message = [{ role: 'user' as const, content: '开发场景' }];
test('explicit evaluation budget blocks requests before API invocation and retains unknown usage', async () => {
  let calls = 0;
  const source: Chat = async () => { calls++; return { message: { role: 'assistant', content: '{}' }, usage: { total_tokens: 20 }, model: 'mock' }; };
  const run = budgetedChat(source, 1, 5000);
  await run.chat(message);
  await assert.rejects(() => run.chat(message), /EVALUATION_BUDGET_EXHAUSTED/);
  assert.equal(calls, 1);
  assert.equal(run.budget.reportedTokens, 20);
  const tooSmall = budgetedChat(source, 2, 10);
  await assert.rejects(() => tooSmall.chat(message), /EVALUATION_BUDGET_EXHAUSTED/);
  assert.equal(calls, 1);
  const unknown = budgetedChat(async () => ({message: {role: 'assistant', content: '{}'}, usage: {}, model: 'mock'}), 2, 5000);
  await unknown.chat(message);
  assert.equal(unknown.budget.missingUsageCalls, 1);
  assert.ok(unknown.budget.accountedTokens > 3000);
  await assert.rejects(() => unknown.chat(message), /EVALUATION_BUDGET_EXHAUSTED/);
});

test('failed API requests consume call budget and retain reservation', async () => {
  const run = budgetedChat(async () => { throw new Error('provider failed'); }, 1, 5000);
  await assert.rejects(() => run.chat(message), /provider failed/);
  assert.equal(run.budget.calls, 1);
  assert.equal(run.budget.missingUsageCalls, 1);
  assert.ok(run.budget.accountedTokens > 0);
  await assert.rejects(() => run.chat(message), /EVALUATION_BUDGET_EXHAUSTED/);
});
