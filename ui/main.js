// Aurora Studio — Create / Queue / Gallery / System. Same-origin /api (Vite proxies to :8000).
const API = '/api';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const getJson = async (path) => (await fetch(API + path)).json();
const fmtSecs = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`; };

const state = { mode: 'reel', aspect: '9:16', musicPath: null, tab: 'create', motion: 'camera', source: 'prompt', restyle: 'asis', photoPath: null, jobs: [], prevRunning: false, galleryKey: '' };

// ─── Tabs ────────────────────────────────────────────────
function showTab(name) {
  state.tab = name;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${name}`));
  document.querySelectorAll('.tabbar button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  if (name === 'gallery') loadGallery();
  if (name === 'system') loadSystem();
  window.scrollTo(0, 0);
}
document.querySelectorAll('.tabbar button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ─── Create form ─────────────────────────────────────────
function bindSeg(id, key, after) {
  $(id).addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    state[key] = b.dataset.v;
    after?.();
  });
}
function syncForm() {
  const up = state.source === 'upload', restyle = up && state.restyle === 'restyle';
  $('uploadBox').hidden = !up;
  $('denoiseWrap').hidden = !restyle;
  $('promptLabel').textContent = !up ? 'Prompt' : restyle ? 'Prompt (describe the new look)' : 'Prompt / motion idea (optional)';
  $('negative').closest('label').hidden = up && !restyle;
  const reel = state.mode === 'reel';
  $('motionWrap').hidden = !reel;
  $('actionWrap').hidden = !(reel && state.motion === 'ai');
  $('reelOpts').hidden = !reel;
  $('musicWrap').hidden = !reel;
  $('customSize').hidden = state.aspect !== 'custom';
  $('submit').textContent = reel ? 'Generate image + reel' : 'Generate image';
  updateEstimate();
}
function updateEstimate() {
  const p = $('provider').value;
  const up = state.source === 'upload';
  const img = up ? (state.restyle === 'restyle' ? '~5–10 min · free (local CPU restyle)' : 'your photo, no generation') : p === 'nvidia_nim' ? '~10 s · uses 1 NIM credit' : p === 'local_comfyui' ? '~5–10 min · free (local CPU)' : '~10 s with NIM (1 credit) or ~5–10 min on local CPU';
  $('estimate').textContent = `Image: ${img}` + (state.mode === 'reel' ? (state.motion === 'ai' ? ' · AI motion on Kaggle: ~15–20 min (free GPU, falls back to camera if it fails)' : ' · Reel: ~30 s') : '');
}
bindSeg('modeSeg', 'mode', syncForm);
bindSeg('sourceSeg', 'source', syncForm);
bindSeg('restyleSeg', 'restyle', syncForm);
$('denoise').addEventListener('input', () => { $('keepLabel').textContent = `${Math.round((0.75 - $('denoise').value) / 0.4 * 100)}%`; });
$('keepLabel').textContent = `${Math.round((0.75 - $('denoise').value) / 0.4 * 100)}%`;
$('photo').addEventListener('change', async () => {
  const f = $('photo').files[0];
  state.photoPath = null;
  $('photoPreview').hidden = true;
  if (!f) { $('photoInfo').textContent = ''; return; }
  $('photoPreview').src = URL.createObjectURL(f);
  $('photoPreview').hidden = false;
  $('photoInfo').textContent = `Uploading ${f.name}…`;
  try {
    const res = await (await fetch(`${API}/upload-image?name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f })).json();
    if (res.status !== 'success') throw new Error(res.message);
    state.photoPath = res.path;
    $('photoInfo').textContent = `${f.name} · ${res.width}×${res.height} ready`;
  } catch (err) {
    $('photoInfo').textContent = `Upload failed: ${err.message}`;
    $('photo').value = ''; $('photoPreview').hidden = true;
  }
});
bindSeg('motionSeg', 'motion', syncForm);
bindSeg('aspectSeg', 'aspect', syncForm);
syncForm();

const PROVIDER_HINTS = {
  auto: 'Uses the order in config.yaml (NIM first if enabled, else ComfyUI).',
  local_comfyui: 'Free, runs on this PC’s CPU: expect roughly 5–10 minutes per image.',
  nvidia_nim: 'Fast (seconds) but spends free NIM credits.',
};
$('provider').addEventListener('change', () => { updateEstimate(); $('providerHint').textContent = PROVIDER_HINTS[$('provider').value]; });
$('providerHint').textContent = PROVIDER_HINTS.auto;

$('music').addEventListener('change', async () => {
  const f = $('music').files[0];
  state.musicPath = null;
  if (!f) { $('musicName').textContent = 'No file chosen — reel will be silent'; return; }
  $('musicName').textContent = `Uploading ${f.name}…`;
  try {
    const res = await (await fetch(`${API}/upload-music?name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f })).json();
    if (res.status !== 'success') throw new Error(res.message);
    state.musicPath = res.path;
    $('musicName').textContent = `${f.name} (${(f.size / 1e6).toFixed(1)} MB) ready`;
  } catch (err) {
    $('musicName').textContent = `Upload failed: ${err.message}`;
    $('music').value = '';
  }
});

$('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('formMsg');
  msg.className = 'hint';
  const body = {
    prompt: $('prompt').value.trim(),
    negative_prompt: $('negative').value.trim(),
    mode: state.mode,
    aspect: state.aspect,
    width: +$('cw').value, height: +$('ch').value,
    duration: +$('duration').value, fps: +$('fps').value,
    provider: $('provider').value,
    motion: state.motion, action: $('action').value.trim(),
    music_path: state.mode === 'reel' ? state.musicPath : null,
  };
  const up = state.source === 'upload', restyle = up && state.restyle === 'restyle';
  Object.assign(body, { source: state.source, image_path: up ? state.photoPath : null, restyle, denoise: +$('denoise').value, consent: up && $('consent').checked });
  if (up && !state.photoPath) { msg.textContent = 'Choose a photo first.'; msg.className = 'hint err'; return; }
  if (up && !body.consent) { msg.textContent = 'Please confirm you own the photo or have permission.'; msg.className = 'hint err'; return; }
  if ((!up || restyle) && !body.prompt) { msg.textContent = 'Write a prompt first.'; msg.className = 'hint err'; return; }
  $('submit').disabled = true;
  try {
    const res = await (await fetch(`${API}/create`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    if (res.status !== 'success') throw new Error(res.message || (Array.isArray(res.detail) ? res.detail.map((d) => `${d.loc.slice(-1)}: ${d.msg}`).join('; ') : 'Request failed'));
    msg.textContent = '';
    showTab('queue');
    pollJobs();
  } catch (err) {
    msg.textContent = err.message; msg.className = 'hint err';
  } finally {
    $('submit').disabled = false;
  }
});

// ─── Queue ───────────────────────────────────────────────
const STAGES = [['image', 'Image'], ['reel', 'Reel'], ['ai_motion', 'AI motion'], ['done', 'Done']];
function renderActive(job) {
  const box = $('activeJob');
  if (!job) { box.innerHTML = '<p class="empty">No job yet. Start one from Create.</p>'; return; }
  const idx = STAGES.findIndex(([k]) => k === job.stage);
  const steps = STAGES.filter(([k]) => k === 'done' || k === 'image' || (job.mode === 'reel' && (job.motion === 'ai' ? k === 'ai_motion' : k === 'reel'))).map(([k, label]) => {
    const i = STAGES.findIndex(([x]) => x === k);
    const cls = job.status === 'done' || i < idx ? 'done' : (i === idx && job.status === 'running' ? 'cur' : '');
    return `<div class="${cls}">${label}</div>`;
  }).join('');
  const hint = job.status === 'running' && job.stage === 'image' && job.provider !== 'nvidia_nim'
    ? '<p class="hint">Image generation on CPU can take 5–10 minutes. You can leave this page.</p>' : '';
  const out = job.outputs;
  box.innerHTML = `
    <div class="bar"><b>${esc(job.status === 'running' ? 'Running' : job.status === 'done' ? 'Finished' : 'Failed')}</b>
      <span class="hint">${fmtSecs(job.elapsed_secs)}</span></div>
    <div class="steps">${steps}</div>${hint}
    <dl class="kv"><dt>Prompt</dt><dd>${esc(job.prompt)}</dd><dt>Output</dt><dd>${job.mode === 'reel' ? `Reel ${job.duration}s @ ${job.fps}fps` : 'Image'} · ${esc(job.aspect)}</dd>
      <dt>Provider</dt><dd>${esc(out?.provider || job.provider)}</dd><dt>Job</dt><dd>${esc(job.job_id)}</dd></dl>
    ${(job.events || []).map((e) => `<p class="hint${/failed/.test(e) ? ' err' : ''}">${esc(e)}</p>`).join('')}
    ${job.error ? `<p class="err-text">${esc(job.error)}</p>` : ''}
    ${out ? '<button class="btn primary" id="viewResult" type="button">View result</button>' : ''}`;
  $('viewResult')?.addEventListener('click', () => openViewer(out));
}

function renderList(jobs) {
  const el = $('jobList');
  el.innerHTML = jobs.length ? jobs.map((j) => `
    <div class="item"><span class="t">${esc(j.prompt)}</span>
      <span class="s ${j.status === 'done' ? 'ok' : j.status === 'failed' ? 'bad' : ''}">${esc(j.status)} · ${fmtSecs(j.elapsed_secs)}</span></div>`).join('')
    : '<p class="empty">Nothing here yet.</p>';
}

async function pollJobs() {
  try {
    const { create_jobs = [] } = await getJson('/jobs');
    state.jobs = create_jobs;
    const running = create_jobs.some((j) => j.status === 'running');
    const latest = create_jobs[0];
    renderActive(latest);
    renderList(create_jobs.slice(1));
    $('qBadge').hidden = !running;
    const top = $('topStatus');
    top.className = 'pill' + (running ? ' run' : latest?.status === 'failed' ? ' fail' : '');
    top.textContent = running ? `Running · ${latest.stage}` : latest?.status === 'failed' ? 'Last job failed' : 'Idle';
    $('submit').disabled = running;
    if (state.prevRunning && !running) state.galleryKey = '';
    state.prevRunning = running;
  } catch { $('topStatus').textContent = 'API offline'; $('topStatus').className = 'pill fail'; }
}

// ─── Gallery ─────────────────────────────────────────────
async function loadGallery() {
  const { items = [] } = await getJson('/gallery');
  const key = items.map((i) => i.slug).join('|');
  if (key === state.galleryKey) return;
  state.galleryKey = key;
  $('gallery').innerHTML = items.length ? items.map((it, n) => {
    const src = it.video_url || it.image_url;
    const media = it.video_url
      ? `<video class="media" data-n="${n}" src="${src}#t=0.5" preload="metadata" muted playsinline></video>`
      : `<img class="media" data-n="${n}" src="${src}" loading="lazy" alt="">`;
    return `<div class="tile">${media}<div class="meta"><b>${esc(it.prompt)}</b>${esc(it.mode)} · ${esc(it.size)} · ${esc(it.provider || '')}</div>
      <a href="${src}?download=true" download>Download ${it.video_url ? 'MP4' : 'image'}</a></div>`;
  }).join('') : '<p class="empty" style="grid-column:1/-1">No outputs yet.</p>';
  $('gallery').querySelectorAll('.media').forEach((m) => m.addEventListener('click', () => openViewer(items[+m.dataset.n])));
}

function openViewer(it) {
  $('viewerBody').innerHTML = it.video_url
    ? `<video src="${it.video_url}" controls autoplay playsinline loop></video>`
    : `<img src="${it.image_url}" alt="">`;
  $('viewer').hidden = false;
}
$('viewerClose').addEventListener('click', () => { $('viewerBody').innerHTML = ''; $('viewer').hidden = true; });
$('openFolder').addEventListener('click', () => fetch(`${API}/open-output`, { method: 'POST' }));

// ─── System ──────────────────────────────────────────────
const gauge = (label, text, pct) => `<div class="gauge"><div class="h"><span>${label}</span><span>${text}</span></div><div class="bar-bg"><i style="width:${Math.min(100, pct || 0)}%"></i></div></div>`;
async function loadSystem() {
  try {
    const [m, p] = await Promise.all([getJson('/metrics'), getJson('/providers/status')]);
    const g = m.gpu;
    $('metrics').innerHTML = '<b>This machine</b>' +
      gauge('CPU', `${m.cpu_percent.toFixed(0)}%`, m.cpu_percent) +
      gauge('RAM', `${m.ram_used_gb} / ${m.ram_total_gb} GB`, m.ram_percent) +
      gauge('Disk', `${m.disk_used_gb} / ${m.disk_total_gb} GB`, m.disk_percent) +
      (g ? gauge(`GPU ${esc(g.name)}`, `${g.util_percent}%`, g.util_percent) + gauge('VRAM', `${g.vram_used_gb} / ${g.vram_total_gb} GB`, g.vram_used_gb / g.vram_total_gb * 100)
         : '<p class="hint">No GPU detected — image generation runs on CPU (ComfyUI) or via NVIDIA NIM.</p>');
    const nim = p.nvidia_nim, a = p.asset_library;
    $('providers').innerHTML = `<b>Providers</b>
      <p><span class="dot ${nim.enabled && nim.api_key_present ? 'on' : 'off'}"></span>NVIDIA NIM — ${nim.enabled ? (nim.api_key_present ? 'ready' : 'enabled, no API key') : 'disabled'}${nim.model ? ` · ${esc(nim.model)}` : ''}</p>
      <p><span class="dot ${p.local_comfyui.enabled ? 'on' : 'off'}"></span>ComfyUI (local) — ${esc(p.local_comfyui.api_url || 'not configured')}</p>
      <p class="hint">Asset library: ${a.count} images · ${a.cached_hits} cache hits · avg ${Math.round(a.avg_latency_ms / 1000)}s</p>`;
  } catch { $('metrics').innerHTML = '<p class="empty">API offline.</p>'; }
}

// ─── Live logs (existing SSE) ────────────────────────────
function startLogs() {
  const es = new EventSource(`${API}/logs/stream`);
  es.onmessage = (e) => {
    if (e.data === '[ping]') return;
    const pre = $('logs');
    pre.textContent += e.data + '\n';
    pre.scrollTop = pre.scrollHeight;
  };
  es.onerror = () => { es.close(); setTimeout(startLogs, 5000); };
}
startLogs();

pollJobs();
setInterval(pollJobs, 2500);
setInterval(() => { if (state.tab === 'system') loadSystem(); if (state.tab === 'gallery') loadGallery(); }, 5000);
