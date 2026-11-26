// Pi Durable build worker, started only by tink-sdlc's pi-durable-launcher from its pinned install folder.
//
// Promises: one durable SQLite session per step, opened with the exact pinned provider/model and thinking level;
// Pi's stored credentials, skills and project context files, read through Pi's own package; read, write, edit and
// bash run in the step checkout, and a command dies with the worker. Launch admits one input for the step. Resume
// continues a run a crash left pending instead of starting another; only an ended or failed run gets a new input.
// An interrupted tool call is never run again: the model gets an interrupted result. Every committed message is
// mirrored, in Pi's session format, to `<session>.jsonl` beside the store and as `message_end` events on stdout,
// which the launcher and Tinkery read.
// Refuses: a model absent from Pi's configured models, a launch over an existing store, and a resume without one.
import { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { BACKGROUND_CONTEXT as context } from '@earendil-works/chord/context';
import { createRegistry, defineExtension, Harness, LiveDoc, section } from '@earendil-works/pi-durable';
import { NodeExecutionEnv } from '@earendil-works/pi-durable/env/node';
import { openNodeSqliteStorage } from '@earendil-works/pi-durable/storage/sqlite/node';
import { createCodingTools } from '@earendil-works/pi-durable/tools';
import { formatSkillsForPrompt, loadProjectContextFiles, loadSkills, ModelRuntime } from '@earendil-works/pi-coding-agent';

const { values } = parseArgs({ options: {
  operation: { type: 'string' }, 'session-dir': { type: 'string' }, 'session-id': { type: 'string' },
  model: { type: 'string' }, thinking: { type: 'string' }, cwd: { type: 'string' },
  'prompt-file': { type: 'string' }, admitted: { type: 'string' },
  // Unused here; it names the step on the command line, so a stop can confirm this process is that step's worker.
  contract: { type: 'string' },
} });
const fail = (message) => { console.error(`pi-durable-worker: ${message}`); process.exit(1); };
const operation = values.operation;
if (!['launch', 'resume'].includes(operation)) fail('operation must be launch or resume.');
for (const name of ['session-dir', 'session-id', 'model', 'thinking', 'cwd', 'prompt-file', 'admitted']) {
  if (!values[name]) fail(`--${name} is required.`);
}
const slash = values.model.indexOf('/');
const provider = values.model.slice(0, slash);
const modelId = values.model.slice(slash + 1);
const id = values['session-id'];
const cwd = values.cwd;
const store = join(values['session-dir'], `${id}.sqlite`);
const mirror = join(values['session-dir'], `${id}.jsonl`);
const sidecar = join(values['session-dir'], `${id}.submission`);
if (operation === 'launch' && existsSync(store)) fail(`Session ${id} already exists; launch admits one session.`);
if (operation === 'resume' && !existsSync(store)) fail(`Session store ${store} is missing.`);

const agentDir = process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent');
// Tests replace the provider with an offline stub module; it never reaches a network.
const stub = process.env.TINK_SDLC_PI_DURABLE_STUB;
const models = stub ? (await import(pathToFileURL(stub).href)).models()
  : await ModelRuntime.create({ authPath: join(agentDir, 'auth.json') });
if (!models.getModel(provider, modelId)) fail(`${values.model} is not among Pi's configured models.`);

const contextFiles = loadProjectContextFiles({ cwd, agentDir });
const { skills } = loadSkills({ cwd, agentDir, skillPaths: [], includeDefaults: true });
const Build = defineExtension({ name: 'tink-sdlc-build', sections: [
  section('cwd', () => cwd),
  section('project-context', () => contextFiles.map((file) => `<file path="${file.path}">\n${file.content}\n</file>`)
    .join('\n') || undefined),
  section('skills', () => (skills.length ? formatSkillsForPrompt(skills, 'read') : undefined), { tag: false }),
  section('interrupted-tools', () => 'A tool result that says it was interrupted did not finish. Before running '
    + 'that command again, check what it already changed; never repeat a command whose effects you cannot check.'),
] });
const Coding = createCodingTools();
const registry = createRegistry();
registry.install(Coding);
registry.install(Build);

const harness = await Harness.open(await openNodeSqliteStorage(store), {
  models, registry, settings: { extensions: [Coding, Build] },
  // Pi Durable starts each command in its own process group; tool-shell ends that group if this worker dies.
  env: ({ cwd: dir }) => new NodeExecutionEnv({ cwd: dir ?? cwd,
    shellPath: join(dirname(fileURLToPath(import.meta.url)), 'tool-shell'),
    shellEnv: { TINK_SDLC_WORKER_PID: String(process.pid) } }),
}, context);

// The mirror: Pi's session format, so the launcher's readers and Tinkery's log readers need no second parser.
const now = () => new Date().toISOString();
const emit = (line) => process.stdout.write(JSON.stringify(line) + '\n');
emit({ type: 'session', version: 3, id, timestamp: now(), cwd });
let parent = null;
let last;
if (existsSync(mirror)) {
  for (const line of readFileSync(mirror, 'utf8').split('\n')) {
    let row;
    try { row = JSON.parse(line); } catch { continue; } // a torn last line from a crash

    if (row.id) parent = row.id;
    if (row.entry !== undefined) last = row.entry;
  }
} else {
  writeFileSync(mirror, JSON.stringify({ type: 'session', version: 3, id, timestamp: now(), cwd }) + '\n');
}
const record = (row) => {
  appendFileSync(mirror, JSON.stringify({ ...row, parentId: parent, timestamp: now() }) + '\n');
  parent = row.id;
};
// Each open records the pin it runs on, as Pi does when a session starts or resumes.
record({ type: 'model_change', id: `${id}:model:${Date.now()}`, provider, modelId });
record({ type: 'thinking_level_change', id: `${id}:thinking:${Date.now()}`, thinkingLevel: values.thinking });

const agent = { model: { provider, modelId }, thinkingLevel: values.thinking, cwd };
const root = await harness.root(context, operation === 'launch' ? { agent } : undefined);
if (operation === 'resume') await root.configure(agent, context);

let syncing = Promise.resolve();
const sync = () => {
  syncing = syncing.then(async () => {
    let cursor;
    do {
      const page = await root.entries({ ...(last === undefined ? {} : { minEntryId: last }), order: 'ascending' },
        100, cursor, context);
      for (const entry of page.items) {
        if (entry.id === last) continue;
        (entry.model ?? []).forEach((message, index) => {
          record({ type: 'message', id: `${entry.id}:${index}`, entry: entry.id, message });
          if (message.role === 'assistant' || message.role === 'toolResult') emit({ type: 'message_end', message });
        });
        last = entry.id;
      }
      cursor = page.next;
    } while (cursor);
  });
  return syncing;
};
await sync();
const watch = await root.watch(context);
watch.start(async (call) => { if (call.kind === 'value') await sync(); });

const prompt = readFileSync(values['prompt-file'], 'utf8');
const live = await harness.snapshot(LiveDoc, root.id, context);
// The request ID is saved before its input is submitted. Submitting the same ID again returns the stored
// submission, so a crash at any point leaves one input that resume can find.
const saveRequest = (requestId) => {
  writeFileSync(`${sidecar}.new`, JSON.stringify({ requestId }) + '\n');
  renameSync(`${sidecar}.new`, sidecar);
};
let submission;
if (live?.run !== undefined) {
  // A crash left this run pending: continue it, with no second input.
  if (operation === 'launch' || !existsSync(sidecar)) fail('A run is pending but its request is unknown.');
  const { requestId } = JSON.parse(readFileSync(sidecar, 'utf8'));
  submission = await root.submit({ type: 'input', content: prompt, requestId }, context);
  harness.resume();
} else {
  const requestId = operation === 'launch' ? id : `${id}:${Date.now()}`;
  saveRequest(requestId);
  submission = await root.submit({ type: 'input', content: prompt, requestId }, context);
}
writeFileSync(values.admitted, JSON.stringify({ session: id, submission: submission.id, pid: process.pid }) + '\n');

const outcome = await submission.wait(context);
await sync();
await watch.stop();
await harness.close(context);
console.error(`pi-durable-worker: submission ${submission.id} ${outcome.status}.`);
