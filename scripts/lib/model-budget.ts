import { ModelError, type Chat } from '../../server/llm';

export interface ModelBudget {
  maxCalls: number;
  maxTokens: number;
  calls: number;
  reportedTokens: number;
  accountedTokens: number;
  missingUsageCalls: number;
  stopped: boolean;
}

/** Before each request, reserve serialized UTF-8 bytes plus 2200 output tokens and
 * framing allowance. This is deliberately conservative, not a provider tokenizer
 * or a billing guarantee. Unknown/failed usage keeps the reservation charged. */
export function budgetedChat(chat: Chat, maxCalls = 12, maxTokens = 60000) {
  if (!Number.isInteger(maxCalls) || maxCalls < 1 || !Number.isInteger(maxTokens) || maxTokens < 1) {
    throw new Error('Positive integer model budgets are required');
  }
  const budget: ModelBudget = { maxCalls, maxTokens, calls: 0, reportedTokens: 0,
    accountedTokens: 0, missingUsageCalls: 0, stopped: false };
  const bounded: Chat = async (messages, options) => {
    const reservation = Buffer.byteLength(JSON.stringify({ messages, tools: options?.tools || [] }), 'utf8') + 2200 + 1024;
    if (budget.calls >= maxCalls || budget.accountedTokens + reservation > maxTokens) {
      budget.stopped = true;
      throw new ModelError('EVALUATION_BUDGET_EXHAUSTED');
    }
    budget.calls++;
    budget.accountedTokens += reservation;
    let completed = false;
    try {
      const result = await chat(messages, options);
      const usage = result.usage.total_tokens;
      if (typeof usage === 'number' && Number.isFinite(usage) && usage >= 0) {
        budget.accountedTokens += usage - reservation;
        budget.reportedTokens += usage;
      } else {
        budget.missingUsageCalls++;
      }
      completed = true;
      if (budget.accountedTokens >= maxTokens) budget.stopped = true;
      return result;
    } finally {
      if (!completed) budget.missingUsageCalls++;
    }
  };
  return { chat: bounded, budget };
}
