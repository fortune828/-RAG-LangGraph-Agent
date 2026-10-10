import { settings } from './config';
export type Message = { role: 'system' | 'user' | 'assistant' | 'tool'; content: string | null;
  tool_calls?: ToolCall[]; tool_call_id?: string; reasoning_content?: string };
export type ToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } };
export type Completion = { message: Message; usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }; model: string };
export type Chat = (messages: Message[], options?: { tools?: unknown[]; signal?: AbortSignal }) => Promise<Completion>;
export class ModelError extends Error { constructor(public code: string) { super(code); } }
export const deepseekChat: Chat = async (messages, options = {}) => {
  if (!settings.key) throw new ModelError('API_KEY_MISSING');
  const signal = AbortSignal.any([AbortSignal.timeout(60000), ...(options.signal ? [options.signal] : [])]);
  let response: Response;
  try {
    response = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST', signal, headers: { Authorization: `Bearer ${settings.key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: settings.model, messages, temperature: 0.2, max_tokens: 2200,
        thinking: { type: 'disabled' },
        ...(options.tools?.length ? { tools: options.tools, tool_choice: 'auto' } : { response_format: { type: 'json_object' } }),
      }),
    });
  } catch { throw new ModelError('MODEL_TIMEOUT_OR_NETWORK'); }
  if (!response.ok) throw new ModelError(`MODEL_HTTP_${response.status}`);
  let body: any;
  try { body = await response.json(); } catch { throw new ModelError('MODEL_INVALID_RESPONSE'); }
  const choice = body.choices?.[0];
  if (!choice?.message || choice.finish_reason === 'length') throw new ModelError('MODEL_INCOMPLETE_RESPONSE');
  return { message: choice.message, usage: body.usage || {}, model: body.model || settings.model };
};
