// Where the local assistant (Ollama) actually computes: GPU, part of it, or CPU. See docs/studio/LLM-GPU.md.
//
// Ollama picks the device itself when it loads a model: it puts into VRAM as many layers as it
// estimates fit at that moment and keeps the rest in RAM. A model that is "half in VRAM" runs every
// token over PCIe - the slowest mode there is - while the card looks free in the task manager.
// So the only honest answer to "is my card being used" comes from Ollama itself: GET /api/ps returns
// `size` (all weights) and `size_vram` (the part that sits in video memory) per loaded model.
//
// Two environment variables, no new settings fields (the studio config is strict on purpose):
//   ASTRA_LLM_KEEP_ALIVE  how long the model stays loaded after a reply; default 5m, "0" = unload at once
//   ASTRA_LLM_NUM_GPU     layers to put into VRAM; unset = let Ollama decide, 0 = force CPU (for tests)
export const DEFAULT_KEEP_ALIVE = '5m';
const DURATION = /^\d+(?:ms|s|m|h)$/;

/** keep_alive for assistant calls: seconds, a duration ("5m", "-1" = forever) or the default. */
export function keepAliveValue(env = process.env) {
  const raw = String(env?.ASTRA_LLM_KEEP_ALIVE ?? '').trim();
  if (!raw) return DEFAULT_KEEP_ALIVE;
  if (raw === '-1') return -1;
  if (/^\d+$/.test(raw)) return Number(raw);
  if (DURATION.test(raw)) return raw;
  return DEFAULT_KEEP_ALIVE;
}

/** num_gpu escape hatch as an options object: {} when unset, {num_gpu: n} otherwise. */
export function numGpuOption(env = process.env) {
  const raw = String(env?.ASTRA_LLM_NUM_GPU ?? '').trim();
  if (!raw || !/^-?\d+$/.test(raw)) return {};
  const layers = Number(raw);
  return Number.isSafeInteger(layers) && layers >= 0 ? { num_gpu: layers } : {};
}

/** "qwen3:8b" and "qwen3:8b:latest" style names compare by their base name. */
export function baseModelName(name) {
  return String(name ?? '').trim().replace(/:latest$/, '');
}

/** The loaded entry for `model`: exact name, then base name, then null. */
export function pickLoaded(models, model) {
  const list = Array.isArray(models) ? models.filter((m) => m && typeof m.name === 'string') : [];
  return list.find((m) => m.name === model) || list.find((m) => baseModelName(m.name) === baseModelName(model)) || null;
}

/** gpu | partial | cpu | idle (nothing loaded) from one /api/ps entry. */
export function gpuVerdict(entry) {
  if (!entry) return { state: 'idle', size: 0, sizeVram: 0, share: 0 };
  const size = Number(entry.size) || 0;
  const sizeVram = Number(entry.size_vram) || 0;
  const share = size > 0 ? Math.min(1, sizeVram / size) : 0;
  const state = sizeVram <= 0 ? 'cpu' : share >= 0.999 ? 'gpu' : 'partial';
  return { state, size, sizeVram, share };
}

const pct = (share) => Math.round(share * 100) + '%';
const gb = (bytes) => (bytes / 1024 ** 3).toFixed(1) + ' GB';

/** One sentence for a verdict, English like the rest of the API. */
export function describeVerdict(report) {
  if (!report?.online) return 'Ollama does not answer at ' + (report?.url || 'the configured address') + '. Start it and check again.';
  const { state, size, sizeVram, share } = report;
  if (state === 'idle') return 'No model is loaded right now: Ollama loads it on the first request. This is the normal state after the model was unloaded.';
  if (state === 'cpu') return 'The model is in RAM only (' + gb(size) + '): not a single layer went into VRAM, so every token is computed on the CPU.';
  if (state === 'partial') return 'Only ' + pct(share) + ' of the weights are in VRAM (' + gb(sizeVram) + ' of ' + gb(size) + '); the rest is in RAM and every token crosses PCIe.';
  return 'The model is on the GPU: all ' + gb(sizeVram) + ' of weights are in VRAM.';
}

/**
 * Ask Ollama what it runs and where.
 * @returns {Promise<{online:boolean,url:string,version:string|null,verdict:string,state:string,model:string,
 *   loaded:{name:string,size:number,sizeVram:number,share:number,expiresAt:string|null}|null,
 *   models:Array<{name:string,size:number,sizeVram:number,share:number}>,keepAlive:number|string,numGpu:object,advice:string}>}
 */
export async function llmGpuReport({ url = 'http://127.0.0.1:11434', model = '', fetchFn = fetch, env = process.env } = {}) {
  const base = { online: false, url, version: null, verdict: 'offline', state: 'offline', model, loaded: null, models: [], keepAlive: keepAliveValue(env), numGpu: numGpuOption(env) };
  const read = async (path) => {
    try {
      const res = await fetchFn(url + path, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) return null;
      return await res.json();
    } catch { return null; }
  };
  const [ps, version] = await Promise.all([read('/api/ps'), read('/api/version')]);
  if (!ps || !Array.isArray(ps.models)) return { ...base, advice: describeVerdict(base) };
  const models = ps.models.map((m) => {
    const v = gpuVerdict(m);
    return { name: m.name, size: v.size, sizeVram: v.sizeVram, share: v.share, expiresAt: m.expires_at ?? null };
  });
  const entry = pickLoaded(ps.models, model);
  const v = gpuVerdict(entry);
  const report = {
    ...base, online: true, version: version?.version ?? null, verdict: v.state, state: v.state,
    size: v.size, sizeVram: v.sizeVram, share: v.share,
    loaded: entry ? { name: entry.name, size: v.size, sizeVram: v.sizeVram, share: v.share, expiresAt: entry.expires_at ?? null } : null,
    models,
  };
  return { ...report, advice: describeVerdict(report) };
}

/** Unload the assistant model from VRAM (POST /api/generate keep_alive 0). Never throws, reports instead. */
export async function unloadLlm({ url = 'http://127.0.0.1:11434', model = '', fetchFn = fetch, onlyIfLoaded = true, env = process.env } = {}) {
  if (!model) return { unloaded: false, reason: 'no-model' };
  if (onlyIfLoaded) {
    const report = await llmGpuReport({ url, model, fetchFn, env });
    if (!report.online) return { unloaded: false, reason: 'offline' };
    if (!report.loaded) return { unloaded: false, reason: 'not-loaded' };
  }
  try {
    const res = await fetchFn(url + '/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model, keep_alive: 0 }), signal: AbortSignal.timeout(10000) });
    if (!res.ok) return { unloaded: false, reason: 'http-' + res.status };
    return { unloaded: true, model };
  } catch (error) { return { unloaded: false, reason: error?.message || 'request-failed' }; }
}

const ms = (ns) => (Number.isFinite(Number(ns)) ? Math.round(Number(ns) / 1e6) : null);

/** The verdict plus the two numbers that separate "slow card" from "no card": speed and load time. */
export function probeAdvice(report, { loadMs = null, tokensPerSec = null } = {}) {
  const parts = [describeVerdict(report)];
  if (Number.isFinite(tokensPerSec)) parts.push('Generation speed: ' + (Math.round(tokensPerSec * 10) / 10) + ' tokens/s.');
  if (Number.isFinite(loadMs)) parts.push('The model was read from disk in ' + (loadMs / 1000).toFixed(1) + ' s.');
  if (report?.state === 'gpu' && Number.isFinite(loadMs) && loadMs > 3000) parts.push('The weights were read from disk on this request (' + (loadMs / 1000).toFixed(1) + ' s). If every message starts with that delay, keep_alive is 0 somewhere (our studio, OLLAMA_KEEP_ALIVE or another client).');
  if (report?.state === 'cpu' && Number.isFinite(tokensPerSec) && tokensPerSec < 15) parts.push('Speed below 15 tokens/s on a CPU fallback is expected for 8-9B models.');
  return parts.join(' ');
}

/**
 * Ask the model one short question, then look at where it ended up. This is the only check that works
 * when nothing is loaded: /api/ps is empty until the first request.
 * @returns {Promise<{ok:true,model:string,verdict:string,state:string,loaded:object|null,models:Array,
 *   keepAlive:number|string,loadMs:number|null,totalMs:number|null,promptTokens:number,evalTokens:number,
 *   tokensPerSec:number|null,advice:string}>}
 */
export async function probeLlm({ url = 'http://127.0.0.1:11434', model = '', fetchFn = fetch, env = process.env, tokens = 32, timeoutMs = 180000 } = {}) {
  if (!model) throw new Error('No local model is configured: pick one in Connections first.');
  const keepAlive = keepAliveValue(env);
  let data;
  try {
    const res = await fetchFn(url + '/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: [{ role: 'user', content: 'Answer with one short word.' }], stream: false, think: false, keep_alive: keepAlive, options: { num_predict: tokens, num_ctx: 2048 } }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      let message = 'Ollama refused the request (' + res.status + ').';
      try { const v = await res.json(); if (v?.error) message = typeof v.error === 'string' ? v.error : v.error.message || message; } catch { /* keep the default */ }
      throw new Error(message);
    }
    data = await res.json();
  } catch (error) {
    if (error?.name === 'TimeoutError' || error?.name === 'AbortError') throw new Error('Ollama did not answer in ' + Math.round(timeoutMs / 1000) + ' s: the model is loading from disk far too slowly.');
    throw error;
  }
  const evalCount = Number(data?.eval_count) || 0;
  const evalNs = Number(data?.eval_duration) || 0;
  const tokensPerSec = evalNs > 0 ? evalCount / (evalNs / 1e9) : null;
  const report = await llmGpuReport({ url, model, fetchFn, env });
  const loadMs = ms(data?.load_duration);
  return {
    ok: true, model, verdict: report.verdict, state: report.state, loaded: report.loaded, models: report.models,
    keepAlive, loadMs, totalMs: ms(data?.total_duration),
    promptTokens: Number(data?.prompt_eval_count) || 0, evalTokens: evalCount,
    tokensPerSec: tokensPerSec === null ? null : Math.round(tokensPerSec * 10) / 10,
    advice: probeAdvice(report, { loadMs, tokensPerSec }),
  };
}
