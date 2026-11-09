// The local assistant on the card or on the CPU: server/llm-gpu.mjs. No Ollama, no GPU, no network:
// every Ollama is a function that answers like /api/ps and /api/version.
import test from 'node:test';
import assert from 'node:assert/strict';
import { keepAliveValue, numGpuOption, baseModelName, pickLoaded, gpuVerdict, describeVerdict, llmGpuReport, unloadLlm, probeLlm, probeAdvice, DEFAULT_KEEP_ALIVE } from '../server/llm-gpu.mjs';
import { llmChat } from '../server/llm.mjs';

const U = 'http://127.0.0.1:11434';
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const bad = (status) => ({ ok: false, status, json: async () => ({ error: 'no' }) });
const GB = 1024 ** 3;

/** Pretend Ollama: ps / version, plus a log of every request. */
function fakeOllama({ models = [], version = '0.12.3', up = true, generate = ok({ done: true }), chat = { message: { content: '{"reply":"ok","actions":[]}' } } } = {}) {
  const log = [];
  const fetchFn = async (url, options = {}) => {
    const u = new URL(url);
    log.push({ path: u.pathname, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : undefined });
    if (!up) throw new Error('ECONNREFUSED');
    if (u.pathname === '/api/ps') return ok({ models });
    if (u.pathname === '/api/version') return ok({ version });
    if (u.pathname === '/api/generate') return typeof generate === 'function' ? generate() : generate;
    if (u.pathname === '/api/chat') return typeof chat === 'function' ? chat() : ok(chat);
    return bad(404);
  };
  return { fetchFn, log };
}

test('keep_alive: default 5m, numbers, durations and "-1" survive, junk falls back', () => {
  assert.equal(keepAliveValue({}), DEFAULT_KEEP_ALIVE);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '  ' }), DEFAULT_KEEP_ALIVE);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '0' }), 0);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '30' }), 30);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '-1' }), -1);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '10m' }), '10m');
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '500ms' }), '500ms');
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: 'forever' }), DEFAULT_KEEP_ALIVE);
  assert.equal(keepAliveValue({ ASTRA_LLM_KEEP_ALIVE: '5 m' }), DEFAULT_KEEP_ALIVE);
});

test('num_gpu: empty options by default, a number when asked, nothing on junk', () => {
  assert.deepEqual(numGpuOption({}), {});
  assert.deepEqual(numGpuOption({ ASTRA_LLM_NUM_GPU: '' }), {});
  assert.deepEqual(numGpuOption({ ASTRA_LLM_NUM_GPU: '0' }), { num_gpu: 0 });
  assert.deepEqual(numGpuOption({ ASTRA_LLM_NUM_GPU: '99' }), { num_gpu: 99 });
  assert.deepEqual(numGpuOption({ ASTRA_LLM_NUM_GPU: '-3' }), {});
  assert.deepEqual(numGpuOption({ ASTRA_LLM_NUM_GPU: 'all' }), {});
});

test('model names: ":latest" is the same model, an unloaded name is null', () => {
  assert.equal(baseModelName('qwen3:8b:latest'), 'qwen3:8b');
  const models = [{ name: 'qwen3-vl:8b', size: 1, size_vram: 1 }, { name: 'qwen3:8b:latest', size: 2, size_vram: 2 }];
  assert.equal(pickLoaded(models, 'qwen3:8b').name, 'qwen3:8b:latest');
  assert.equal(pickLoaded(models, 'qwen3:14b'), null);
  assert.equal(pickLoaded(undefined, 'qwen3:8b'), null);
});

test('verdict: all in VRAM, a part, nothing, nothing loaded', () => {
  assert.equal(gpuVerdict(null).state, 'idle');
  assert.deepEqual(gpuVerdict({ size: 6 * GB, size_vram: 6 * GB }).state, 'gpu');
  assert.equal(gpuVerdict({ size: 6 * GB, size_vram: 6 * GB }).share, 1);
  const part = gpuVerdict({ size: 6 * GB, size_vram: 3 * GB });
  assert.equal(part.state, 'partial');
  assert.equal(Math.round(part.share * 100), 50);
  assert.equal(gpuVerdict({ size: 6 * GB, size_vram: 0 }).state, 'cpu');
  assert.equal(gpuVerdict({ size: 0, size_vram: 0 }).state, 'cpu');
  // A rounding artifact of a fully loaded model is still "gpu", not "partial".
  assert.equal(gpuVerdict({ size: 1000, size_vram: 999 }).state, 'gpu');
});

test('report: card, half, CPU — with the numbers that answer "почему процессор"', async () => {
  const gpu = await llmGpuReport({ ...fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 6 * GB, expires_at: '2026-11-07T12:00:00Z' }] }) , url: U, model: 'qwen3:8b' });
  assert.equal(gpu.online, true);
  assert.equal(gpu.verdict, 'gpu');
  assert.equal(gpu.version, '0.12.3');
  assert.equal(gpu.loaded.sizeVram, 6 * GB);
  assert.equal(gpu.loaded.expiresAt, '2026-11-07T12:00:00Z');
  assert.match(gpu.advice, /on the GPU/);

  const part = await llmGpuReport({ ...fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 2 * GB }] }), url: U, model: 'qwen3:8b' });
  assert.equal(part.verdict, 'partial');
  assert.match(part.advice, /33%/);

  const cpu = await llmGpuReport({ ...fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 0 }] }), url: U, model: 'qwen3:8b' });
  assert.equal(cpu.verdict, 'cpu');
  assert.match(cpu.advice, /CPU/);
  assert.equal(cpu.models.length, 1);
});

test('report: nothing loaded is not an error, no Ollama at all is offline', async () => {
  const idle = await llmGpuReport({ ...fakeOllama({ models: [] }), url: U, model: 'qwen3:8b' });
  assert.equal(idle.online, true);
  assert.equal(idle.verdict, 'idle');
  assert.equal(idle.loaded, null);
  assert.match(idle.advice, /first request/);

  const down = await llmGpuReport({ ...fakeOllama({ up: false }), url: U, model: 'qwen3:8b' });
  assert.equal(down.online, false);
  assert.equal(down.verdict, 'offline');
  assert.match(down.advice, /does not answer/);
});

test('describeVerdict never throws on an empty report', () => {
  assert.match(describeVerdict(undefined), /does not answer/);
});

test('unload: posts keep_alive 0 only when the model is loaded', async () => {
  const loaded = fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 6 * GB }] });
  const first = await unloadLlm({ url: U, model: 'qwen3:8b', fetchFn: loaded.fetchFn });
  assert.deepEqual(first, { unloaded: true, model: 'qwen3:8b' });
  const post = loaded.log.find((r) => r.path === '/api/generate');
  assert.deepEqual(post.body, { model: 'qwen3:8b', keep_alive: 0 });

  const idle = fakeOllama({ models: [] });
  assert.deepEqual(await unloadLlm({ url: U, model: 'qwen3:8b', fetchFn: idle.fetchFn }), { unloaded: false, reason: 'not-loaded' });
  assert.equal(idle.log.some((r) => r.path === '/api/generate'), false);

  const down = fakeOllama({ up: false });
  assert.deepEqual(await unloadLlm({ url: U, model: 'qwen3:8b', fetchFn: down.fetchFn }), { unloaded: false, reason: 'offline' });

  assert.deepEqual(await unloadLlm({ url: U, model: '', fetchFn: idle.fetchFn }), { unloaded: false, reason: 'no-model' });
});

test('unload: a refusing Ollama is reported, not thrown', async () => {
  const refusing = fakeOllama({ models: [{ name: 'qwen3:8b', size: 1, size_vram: 1 }], generate: () => bad(500) });
  assert.deepEqual(await unloadLlm({ url: U, model: 'qwen3:8b', fetchFn: refusing.fetchFn }), { unloaded: false, reason: 'http-500' });
});

test('llmChat sends our keep_alive and num_gpu, not a hard-coded 0', async () => {
  const real = globalThis.fetch;
  const { fetchFn, log } = fakeOllama({});
  globalThis.fetch = fetchFn;
  const env = { ...process.env };
  try {
    delete process.env.ASTRA_LLM_KEEP_ALIVE;
    delete process.env.ASTRA_LLM_NUM_GPU;
    const answer = await llmChat({ provider: 'ollama', ollamaUrl: U, ollamaModel: 'qwen3:8b' }, { context: '', history: [], message: 'hi' });
    assert.equal(answer.reply, 'ok');
    const chat = log.find((r) => r.path === '/api/chat');
    assert.equal(chat.body.keep_alive, '5m');
    assert.equal('num_gpu' in chat.body.options, false);
    assert.equal(chat.body.options.num_ctx, 8192);

    process.env.ASTRA_LLM_KEEP_ALIVE = '0';
    process.env.ASTRA_LLM_NUM_GPU = '12';
    await llmChat({ provider: 'ollama', ollamaUrl: U, ollamaModel: 'qwen3:8b' }, { context: '', history: [], message: 'hi' });
    const second = log.filter((r) => r.path === '/api/chat')[1];
    assert.equal(second.body.keep_alive, 0);
    assert.equal(second.body.options.num_gpu, 12);
  } finally {
    globalThis.fetch = real;
    process.env.ASTRA_LLM_KEEP_ALIVE = env.ASTRA_LLM_KEEP_ALIVE ?? '';
    process.env.ASTRA_LLM_NUM_GPU = env.ASTRA_LLM_NUM_GPU ?? '';
    if (env.ASTRA_LLM_KEEP_ALIVE === undefined) delete process.env.ASTRA_LLM_KEEP_ALIVE;
    if (env.ASTRA_LLM_NUM_GPU === undefined) delete process.env.ASTRA_LLM_NUM_GPU;
  }
});

const chatMetrics = (answer = 'ok') => ({
  message: { content: answer }, done: true,
  load_duration: 7e9, prompt_eval_count: 12, prompt_eval_duration: 0.4e9,
  eval_count: 32, eval_duration: 1e9, total_duration: 8.9e9,
});

test('probe: loads the model itself, then reports speed, load time and the verdict', async () => {
  const fake = fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 6 * GB }], chat: chatMetrics() });
  const r = await probeLlm({ url: U, model: 'qwen3:8b', fetchFn: fake.fetchFn, env: {} });
  assert.equal(r.ok, true);
  assert.equal(r.verdict, 'gpu');
  assert.equal(r.tokensPerSec, 32);
  assert.equal(r.loadMs, 7000);
  assert.equal(r.totalMs, 8900);
  assert.equal(r.promptTokens, 12);
  assert.equal(r.evalTokens, 32);
  assert.match(r.advice, /on the GPU/);
  assert.match(r.advice, /32 tokens\/s/);
  assert.match(r.advice, /7\.0 s/);
  const body = fake.log.find((x) => x.path === '/api/chat').body;
  assert.equal(body.stream, false);
  assert.equal(body.keep_alive, DEFAULT_KEEP_ALIVE);
  assert.equal(body.options.num_predict, 32);
});

test('probe: a CPU verdict spells out the expected slow speed', async () => {
  const fake = fakeOllama({ models: [{ name: 'qwen3:8b', size: 6 * GB, size_vram: 0 }], chat: { ...chatMetrics(), eval_count: 20, eval_duration: 4e9 } });
  const r = await probeLlm({ url: U, model: 'qwen3:8b', fetchFn: fake.fetchFn, env: { ASTRA_LLM_KEEP_ALIVE: '0' } });
  assert.equal(r.verdict, 'cpu');
  assert.equal(r.tokensPerSec, 5);
  assert.equal(r.keepAlive, 0);
  assert.match(r.advice, /CPU/);
  assert.match(r.advice, /below 15/);
});

test('probe: a card that is fine but reloads every time is called out', () => {
  const advice = probeAdvice({ online: true, state: 'gpu', size: 1, sizeVram: 1, share: 1 }, { loadMs: 9500, tokensPerSec: 40 });
  assert.match(advice, /keep_alive is 0/);
});

test('probe: Ollama errors and a missing model are real errors, not silent zeros', async () => {
  const refusing = fakeOllama({ chat: () => ({ ok: false, status: 404, json: async () => ({ error: 'model "qwen3:8b" not found, try pulling it first' }) }) });
  await assert.rejects(() => probeLlm({ url: U, model: 'qwen3:8b', fetchFn: refusing.fetchFn }), /not found/);
  await assert.rejects(() => probeLlm({ url: U, model: '' }), /No local model/);
  const hanging = async () => { const e = new Error('timed out'); e.name = 'TimeoutError'; throw e; };
  await assert.rejects(() => probeLlm({ url: U, model: 'qwen3:8b', fetchFn: hanging, timeoutMs: 5000 }), /did not answer in 5 s/);
});
