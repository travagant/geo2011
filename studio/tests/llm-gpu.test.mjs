// The local assistant on the card or on the CPU: server/llm-gpu.mjs. No Ollama, no GPU, no network:
// every Ollama is a function that answers like /api/ps and /api/version.
import test from 'node:test';
import assert from 'node:assert/strict';
import { keepAliveValue, numGpuOption, baseModelName, pickLoaded, gpuVerdict, describeVerdict, llmGpuReport, unloadLlm, DEFAULT_KEEP_ALIVE } from '../server/llm-gpu.mjs';
import { llmChat } from '../server/llm.mjs';

const U = 'http://127.0.0.1:11434';
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const bad = (status) => ({ ok: false, status, json: async () => ({ error: 'no' }) });
const GB = 1024 ** 3;

/** Pretend Ollama: ps / version, plus a log of every request. */
function fakeOllama({ models = [], version = '0.12.3', up = true, generate = ok({ done: true }) } = {}) {
  const log = [];
  const fetchFn = async (url, options = {}) => {
    const u = new URL(url);
    log.push({ path: u.pathname, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : undefined });
    if (!up) throw new Error('ECONNREFUSED');
    if (u.pathname === '/api/ps') return ok({ models });
    if (u.pathname === '/api/version') return ok({ version });
    if (u.pathname === '/api/generate') return typeof generate === 'function' ? generate() : generate;
    if (u.pathname === '/api/chat') return ok({ message: { content: '{"reply":"ok","actions":[]}' } });
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
