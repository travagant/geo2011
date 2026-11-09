'use strict';
// d3studio — the whole browser app. Same origin only: every request goes to
// /api/... of the server that served this page, so nothing here needs to know
// where the generation server lives (the studio asks it).
const $ = (id) => document.getElementById(id);
const state = { project: null, projects: [], jobs: [], assets: [], checkpoints: [], settings: {} };

function toast(text, bad) {
  const box = $('toast');
  box.textContent = text;
  box.className = 'toast' + (bad ? ' bad' : '');
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, bad ? 8000 : 3500);
}

async function api(path, options) {
  const response = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
  const doc = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(doc.error || ('HTTP ' + response.status));
  return doc;
}

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });
const put = (path, body) => api(path, { method: 'PUT', body: JSON.stringify(body || {}) });

const esc = (text) => String(text == null ? '' : text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const when = (seconds) => new Date(seconds * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
const STATUS = { queued: 'в очереди', running: 'идёт', done: 'готово', failed: 'ошибка', cancelled: 'отменено' };

// ---- health & settings ------------------------------------------------------
async function loadHealth() {
  try {
    const doc = await api('/api/health');
    state.checkpoints = doc.comfy.checkpoints || [];
    state.settings = doc.settings;
    const pill = $('engine-pill');
    pill.textContent = doc.comfy.reachable
      ? 'движок: ' + doc.comfy.url.replace(/^https?:\/\//, '')
      : 'движок не отвечает: ' + doc.comfy.url.replace(/^https?:\/\//, '');
    pill.className = 'pill ' + (doc.comfy.reachable ? 'ok' : 'bad');
    fillCheckpoints(doc.settings.checkpoint);
    $('comfy-url').value = doc.settings.comfy_url || '';
    $('d3tool-path').value = doc.settings.d3tool_path || '';
  } catch (error) {
    $('engine-pill').textContent = 'студия не отвечает';
    $('engine-pill').className = 'pill bad';
  }
}

function fillCheckpoints(chosen) {
  const select = $('checkpoint');
  const names = state.checkpoints.slice();
  if (chosen && !names.includes(chosen)) names.unshift(chosen);
  select.innerHTML = names.length
    ? names.map((name) => `<option ${name === chosen ? 'selected' : ''}>${esc(name)}</option>`).join('')
    : '<option value="">— движок не сообщил —</option>';
  select.value = chosen || names[0] || '';
}

// ---- projects ---------------------------------------------------------------
async function loadProjects() {
  const doc = await api('/api/projects');
  state.projects = doc.projects || [];
  if (!state.projects.length) return;
  if (!state.project || !state.projects.some((p) => p.id === state.project)) state.project = state.projects[0].id;
  $('project-select').innerHTML = state.projects
    .map((p) => `<option value="${p.id}" ${p.id === state.project ? 'selected' : ''}>${esc(p.name)} (${p.assets})</option>`)
    .join('');
  $('project-pill').textContent = 'проект: ' + (state.projects.find((p) => p.id === state.project) || {}).name;
}

async function createProject() {
  const name = prompt('Название проекта', 'Новый проект');
  if (!name) return;
  try {
    const project = await post('/api/projects', { name });
    state.project = project.id;
    await loadProjects();
    await refreshAll();
  } catch (error) { toast(error.message, true); }
}

// ---- jobs -------------------------------------------------------------------
function jobCard(job) {
  const progress = Math.round(job.progress || 0);
  const canCancel = job.status === 'queued' || job.status === 'running';
  const result = job.result || {};
  const detail = result.asset
    ? `<div class="hint">${esc(result.asset)} · ${esc(String(result.seconds || ''))} с · seed ${esc(String(result.seed))}</div>`
    : (result.files ? `<div class="hint">${esc(result.outDir || '')} · файлов: ${result.files.length}</div>` : '');
  return `<article class="job ${job.status}">
    <header>
      <span class="kind">${job.kind === 'concept' ? 'концепт' : job.kind === 'export.d3' ? 'экспорт в D3' : esc(job.kind)}</span>
      <span class="state">${STATUS[job.status] || job.status}</span>
      <span class="time">${when(job.created)}</span>
    </header>
    <div class="bar"><i style="width:${progress}%"></i></div>
    <p class="msg">${esc(job.message || '')}</p>
    ${detail}
    ${canCancel ? `<button class="ghost small" data-cancel="${job.id}">Отменить</button>` : ''}
  </article>`;
}

function renderJobs() {
  const box = $('jobs');
  if (!state.jobs.length) { box.innerHTML = '<p class="empty">Пока ничего не запускалось.</p>'; return; }
  box.innerHTML = state.jobs.map(jobCard).join('');
  box.querySelectorAll('[data-cancel]').forEach((button) => {
    button.onclick = async () => {
      try { await post('/api/jobs/' + button.dataset.cancel + '/cancel'); await refreshJobs(); }
      catch (error) { toast(error.message, true); }
    };
  });
}

async function refreshJobs() {
  if (!state.project) return;
  const before = state.jobs.map((j) => j.status).join(',');
  const doc = await api('/api/jobs?limit=30&project=' + encodeURIComponent(state.project));
  state.jobs = doc.jobs || [];
  renderJobs();
  if (before !== state.jobs.map((j) => j.status).join(',')) await refreshAll();
}

// ---- assets -----------------------------------------------------------------
function assetCard(asset) {
  const image = (asset.kind === 'concept' || asset.kind === 'image') && /\.(png|jpe?g|webp)$/i.test(asset.name);
  const meta = asset.meta || {};
  const line = image
    ? `${esc(String(meta.width || ''))}×${esc(String(meta.height || ''))} · ${esc(String(meta.steps || ''))} шагов · seed ${esc(String(meta.seed || ''))}`
    : (asset.kind === 'model' ? 'модель' : esc(asset.kind));
  return `<figure class="asset">
    ${image ? `<img loading="lazy" src="/assets/${asset.id}" alt="${esc(asset.name)}">` : '<div class="ph">GLB</div>'}
    <figcaption>
      <strong>${esc(asset.name)}</strong>
      <span>${line}</span>
      ${asset.kind === 'model' ? `<button class="ghost small" data-export="${asset.id}">Экспорт в D3</button>` : ''}
    </figcaption>
  </figure>`;
}

function renderAssets() {
  const box = $('assets');
  if (!state.assets.length) { box.innerHTML = '<p class="empty">Здесь появятся концепты и модели.</p>'; return; }
  box.innerHTML = state.assets.map(assetCard).join('');
  box.querySelectorAll('[data-export]').forEach((button) => {
    button.onclick = async () => {
      try {
        await post('/api/jobs', { kind: 'export.d3', projectId: state.project, params: { assetId: button.dataset.export } });
        toast('Экспорт поставлен в очередь');
        await refreshJobs();
      } catch (error) { toast(error.message, true); }
    };
  });
}

async function refreshAssets() {
  if (!state.project) return;
  const doc = await api('/api/projects/' + state.project + '/assets');
  state.assets = doc.assets || [];
  renderAssets();
  const exportsDoc = await api('/api/projects/' + state.project + '/exports').catch(() => ({ exports: [] }));
  const list = exportsDoc.exports || [];
  $('exports').innerHTML = list.length
    ? '<h3>Экспорт в Disciples 3</h3>' + list.map((folder) => `<details><summary>${esc(folder.name)} · ${folder.files.length} файлов</summary><ul>${
        folder.files.map((file) => `<li><a href="/exports/${state.project}/${encodeURIComponent(folder.name)}/${file.split('/').map(encodeURIComponent).join('/')}">${esc(file)}</a></li>`).join('')
      }</ul></details>`).join('')
    : '';
}

async function refreshAll() {
  await refreshJobs().catch(() => {});
  await refreshAssets().catch(() => {});
  await loadProjects().catch(() => {});
}

// ---- actions ----------------------------------------------------------------
async function draw() {
  const prompt = $('prompt').value.trim();
  if (!prompt) { toast('Напишите, что нарисовать', true); return; }
  if (!state.project) { toast('Сначала создайте проект', true); return; }
  const seed = $('seed').value.trim();
  const params = {
    prompt,
    negative: $('negative').value.trim(),
    width: Number($('width').value),
    height: Number($('height').value),
    steps: Number($('steps').value),
    cfg: Number($('cfg').value),
    checkpoint: $('checkpoint').value,
  };
  if (seed !== '') params.seed = Number(seed);
  $('draw').disabled = true;
  try {
    await post('/api/jobs', { kind: 'concept', projectId: state.project, params });
    toast('Поставил в очередь');
    await refreshJobs();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('draw').disabled = false;
  }
}

async function upload(file) {
  if (!file || !state.project) return;
  const base64 = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
    reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
    reader.readAsDataURL(file);
  });
  try {
    await post('/api/projects/' + state.project + '/assets', { name: file.name, data: base64 });
    toast('Загружено: ' + file.name);
    await refreshAssets();
  } catch (error) { toast(error.message, true); }
}

async function saveSettings() {
  try {
    await put('/api/settings', {
      comfy_url: $('comfy-url').value.trim(),
      d3tool_path: $('d3tool-path').value.trim(),
      checkpoint: $('checkpoint').value,
    });
    toast('Сохранено');
    await loadHealth();
  } catch (error) { toast(error.message, true); }
}

// ---- boot -------------------------------------------------------------------
$('draw').onclick = draw;
$('refresh').onclick = () => refreshAll().catch((e) => toast(e.message, true));
$('new-project').onclick = createProject;
$('save-settings').onclick = saveSettings;
$('project-select').onchange = (event) => { state.project = event.target.value; refreshAll(); };
$('checkpoint').onchange = () => put('/api/settings', { checkpoint: $('checkpoint').value }).then(loadHealth).catch(() => {});
$('upload').onchange = (event) => upload(event.target.files && event.target.files[0]);

(async function boot() {
  await loadHealth();
  await loadProjects();
  await refreshAll();
  setInterval(() => refreshJobs().catch(() => {}), 1500);
  setInterval(loadHealth, 15000);
})();
