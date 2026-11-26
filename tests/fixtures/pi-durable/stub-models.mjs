// TEST ONLY: an offline provider for the Pi Durable worker. Each request appends to TEST_STUB_LEDGER and answers
// from TEST_STUB_SCRIPT, a JSON list of responses: {"bash": "<command>"} asks for one bash call, {"text": "..."}
// ends the turn. It never opens a connection.
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import { createAssistantMessageEventStream, createModels, createProvider } from '@earendil-works/pi-ai';

export function models() {
  const model = {
    id: 'build', name: 'TEST offline build', provider: 'stub', api: 'openai-completions', baseUrl: 'http://127.0.0.1:1',
    input: ['text'], reasoning: true, contextWindow: 65536, maxTokens: 1024,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  };
  const stream = (_model, input) => {
    const ledger = process.env.TEST_STUB_LEDGER;
    const used = existsSync(ledger) ? readFileSync(ledger, 'utf8').split('\n').filter(Boolean).length : 0;
    const script = JSON.parse(readFileSync(process.env.TEST_STUB_SCRIPT, 'utf8'));
    const step = script[used] ?? { text: 'TEST script exhausted.' };
    const interrupted = input.messages.some((m) => m.role === 'toolResult' && m.isError
      && m.content.some((b) => b.type === 'text' && /interrupt/i.test(b.text)));
    appendFileSync(ledger, JSON.stringify({ request: used + 1, pid: process.pid, interrupted }) + '\n');
    const call = step.bash !== undefined;
    const message = {
      role: 'assistant', api: model.api, provider: model.provider, model: model.id,
      content: call ? [{ type: 'toolCall', id: `call-${used + 1}`, name: 'bash', arguments: { command: step.bash } }]
        : [{ type: 'text', text: step.text }],
      timestamp: Date.now(), stopReason: call ? 'toolUse' : 'stop',
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    const events = createAssistantMessageEventStream();
    events.push({ type: 'done', reason: message.stopReason, message });
    events.end(message);
    return events;
  };
  const registry = createModels();
  registry.setProvider(createProvider({ id: model.provider, models: [model], api: { stream, streamSimple: stream },
    auth: { apiKey: { name: 'TEST placeholder', resolve: async () => ({ auth: { apiKey: 'TEST-offline' } }) } } }));
  return registry;
}
