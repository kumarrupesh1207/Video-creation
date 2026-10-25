// Aurora Studio — Create / Queue / Gallery / System. Same-origin /api (Vite proxies to :8000).
const API = '/api';

// Any 401 from the API (expired/missing session) sends the user to the login screen.
const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _fetch(...args);
  if (res.status === 401 && String(args[0]).startsWith(API) && !String(args[0]).startsWith(`${API}/login`)) showLogin();
  return res;
};
function showLogin() { document.getElementById('login').hidden = false; }
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const getJson = async (path) => (await fetch(API + path)).json();
const fmtSecs = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`; };

const state = { mode: 'reel', aspect: '9:16', musicPath: null, tab: 'create', motion: 'camera', music: 'auto', gender: 'female', speakerWav: null, source: 'prompt', restyle: 'asis', photoPath: null, jobs: [], prevRunning: false, galleryKey: '' };

// ─── Tabs ────────────────────────────────────────────────
function showTab(name) {
  state.tab = name;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${name}`));
  document.querySelectorAll('.tabbar button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  if (name === 'gallery') loadGallery();
  if (name === 'system') loadSystem();
  if (name === 'queue') loadPublish();
  if (name === 'approvals') loadApprovals();
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
  $('faceWrap').hidden = !restyle;
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
bindSeg('musicSeg', 'music', () => { $('musicFile').hidden = state.music !== 'upload'; });
$('voiceover').addEventListener('change', () => { $('voBox').hidden = !$('voiceover').checked; });
bindSeg('genderSeg', 'gender');
$('voSample').addEventListener('change', async () => {
  const f = $('voSample').files[0];
  state.speakerWav = null;
  if (!f) { $('voInfo').textContent = ''; return; }
  $('voInfo').textContent = `Uploading ${f.name}…`;
  try {
    const res = await (await fetch(`${API}/upload-music?name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f })).json();
    if (res.status !== 'success') throw new Error(res.message);
    state.speakerWav = res.path;
    $('voInfo').textContent = `${f.name} ready (used by "Offline clone")`;
  } catch (err) { $('voInfo').textContent = `Upload failed: ${err.message}`; $('voSample').value = ''; }
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
    music_path: state.mode === 'reel' && state.music === 'upload' ? state.musicPath : null,
    music: state.music === 'none' ? 'none' : 'auto', captions: $('captions').checked,
    voiceover: state.mode === 'reel' && $('voiceover').checked, voiceover_text: $('voText').value.trim(),
    voice_engine: $('voEngine').value, gender: state.gender, speaker_wav: state.speakerWav,
  };
  const up = state.source === 'upload', restyle = up && state.restyle === 'restyle';
  Object.assign(body, { source: state.source, image_path: up ? state.photoPath : null, restyle, denoise: +$('denoise').value, face_lock: $('faceLock').checked });
  if (up && !state.photoPath) { msg.textContent = 'Choose a photo first.'; msg.className = 'hint err'; return; }
  if ((!up || restyle) && !body.prompt) { msg.textContent = 'Write a prompt first.'; msg.className = 'hint err'; return; }
  await submitCreate(body, msg);
});

async function submitCreate(body, msg) {
  $('submit').disabled = true;
  try {
    const res = await (await fetch(`${API}/create`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    if (res.status !== 'success') throw new Error(res.message || (typeof res.detail === 'string' ? res.detail : Array.isArray(res.detail) ? res.detail.map((d) => `${d.loc.slice(-1)}: ${d.msg}`).join('; ') : 'Request failed'));
    msg.textContent = '';
    $('runbar').hidden = true;
    showTab('queue');
    pollJobs();
  } catch (err) {
    msg.textContent = err.message; msg.className = 'hint err';
    $('submit').disabled = false;
    if (state.tab !== 'create') showTab('create');
  }
}

// ─── Queue ───────────────────────────────────────────────
// stage -> [label, rough typical seconds]
const stageInfo = (job) => ({
  queued: ['Starting…', 5],
  image: ['Generating image', job.provider === 'nvidia_nim' ? 15 : 600],
  restyle: ['Restyling with ComfyUI (CPU, ~5–15 min)', 600],
  audio: ['Preparing music / voiceover' + (job.request?.music === 'auto' && !job.request?.music_path ? ' (new AI music can take ~2–3 min)' : ''), job.request?.music === 'auto' && !job.request?.music_path ? 160 : 30],
  reel: ['Assembling reel', 40],
  ai_motion: ['AI motion on Kaggle (~15–35 min)', 1500],
  validating: ['Validating output', 10],
}[job.stage] || [job.stage, 60]);
const STAGE_LABEL = { image: 'Image', restyle: 'Restyle', audio: 'Audio', reel: 'Reel', ai_motion: 'AI motion', validating: 'Validate', done: 'Done', failed: 'Failed', interrupted: 'Interrupted' };

function friendlyError(err = '') {
  if (/8188|ComfyUI|Connection ?refused|ConnectionError|Max retries/i.test(err)) return 'ComfyUI is not running. Start it with start_aurora.bat, then press Retry.';
  if (/kaggle/i.test(err)) return 'Kaggle is not set up (keys missing or rejected). Use Camera motion or add the keys.';
  if (/nvidia|nim|40[13]|429/i.test(err)) return 'NVIDIA NIM refused the request (key or credits). Try ComfyUI instead.';
  if (/restarted/i.test(err)) return 'The server restarted while this job was running. Press Retry.';
  return err;
}

function renderActive(job) {
  const box = $('activeJob');
  if (!job) { box.innerHTML = '<p class="empty">No job yet. Start one from Create.</p>'; return; }
  const seen = (job.timeline || []).map((t) => t.stage);
  const steps = seen.map((k, i) => `<div class="${i === seen.length - 1 && job.status === 'running' ? 'cur' : (k === 'failed' || k === 'interrupted') ? 'bad' : 'done'}">${STAGE_LABEL[k] || k}</div>`).join('');
  const hint = job.status === 'running' && job.stage === 'image' && job.provider !== 'nvidia_nim'
    ? '<p class="hint">Image generation on CPU can take 5–10 minutes. You can leave this page.</p>' : '';
  const out = job.outputs;
  box.innerHTML = `
    <div class="bar"><b>${esc(job.status === 'running' ? 'Running' : job.status === 'done' ? 'Finished' : job.status === 'interrupted' ? 'Interrupted' : 'Failed')}</b>
      <span class="hint">${fmtSecs(job.elapsed_secs)}</span></div>
    <div class="steps">${steps}</div>${hint}
    <dl class="kv"><dt>Prompt</dt><dd>${esc(job.prompt)}</dd><dt>Output</dt><dd>${job.mode === 'reel' ? `Reel ${job.duration}s @ ${job.fps}fps` : 'Image'} · ${esc(job.aspect)}</dd>
      <dt>Provider</dt><dd>${esc(out?.provider || job.provider)}</dd><dt>Job</dt><dd>${esc(job.job_id)}</dd></dl>
    ${(job.events || []).map((e) => `<p class="hint${/failed/.test(e) ? ' err' : ''}">${esc(e)}</p>`).join('')}
    ${job.error ? `<p class="err-text">${esc(friendlyError(job.error))}</p>` : ''}
    ${out ? '<button class="btn primary" id="viewResult" type="button">View result</button>' : ''}`;
  $('viewResult')?.addEventListener('click', () => openViewer(out));
}

function renderList(jobs) {
  const el = $('jobList');
  el.innerHTML = jobs.length ? jobs.map((j) => `
    <div class="item"><span class="t">${esc(j.prompt)}</span>
      <span class="s ${j.status === 'done' ? 'ok' : j.status === 'failed' || j.status === 'interrupted' ? 'bad' : ''}">${esc(j.status)} · ${fmtSecs(j.elapsed_secs)}</span></div>`).join('')
    : '<p class="empty">Nothing here yet.</p>';
}

const dismissed = new Set((() => { try { return JSON.parse(sessionStorage.getItem('dismissed') || '[]'); } catch { return []; } })());
function renderRunBar(latest, running) {
  const bar = $('runbar');
  if (running) {
    const [label, est] = stageInfo(latest);
    const left = Math.max(0, est - (latest.stage_elapsed_secs || 0));
    const pct = Math.min(95, ((latest.stage_elapsed_secs || 0) / est) * 100);
    bar.className = 'runbar run';
    bar.innerHTML = `<div class="spin"></div><div class="rb"><b>Processing on backend… ${esc(label)}</b>
      <span>Elapsed ${fmtSecs(latest.elapsed_secs)} · ${left > 0 ? `about ${fmtSecs(left)} left in this step` : 'taking longer than usual, still working'}</span>
      <div class="bar-bg"><i style="width:${pct}%"></i></div></div>`;
    bar.hidden = false;
  } else if (latest && ['failed', 'interrupted'].includes(latest.status) && !dismissed.has(latest.job_id)) {
    bar.className = 'runbar fail';
    bar.innerHTML = `<div class="rb"><b>${latest.status === 'failed' ? 'Job failed' : 'Job interrupted'}</b>
      <span>${esc(friendlyError(latest.error))}</span>
      <div class="rbtn"><button class="btn small" id="retryBtn" type="button">Retry</button><button class="btn ghost small" id="dismissBtn" type="button">Dismiss</button></div></div>`;
    bar.hidden = false;
    $('dismissBtn').onclick = () => { dismissed.add(latest.job_id); try { sessionStorage.setItem('dismissed', JSON.stringify([...dismissed])); } catch { /* ignore */ } bar.hidden = true; };
    $('retryBtn').onclick = () => submitCreate(latest.request, $('formMsg'));
  } else bar.hidden = true;
}

function toast(text, action) {
  const t = $('toast');
  t.innerHTML = `<span>${esc(text)}</span>${action ? '<button class="btn small" id="toastBtn" type="button">View result</button>' : ''}`;
  t.hidden = false;
  if (action) $('toastBtn').onclick = () => { t.hidden = true; action(); };
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.hidden = true; }, 15000);
}

async function pollJobs() {
  try {
    const { create_jobs = [] } = await getJson('/jobs');
    state.jobs = create_jobs;
    const running = create_jobs.some((j) => j.status === 'running');
    const latest = create_jobs[0];
    renderActive(latest);
    renderList(create_jobs.slice(1));
    renderRunBar(latest, running);
    $('qBadge').hidden = !running;
    const top = $('topStatus');
    const bad = latest && ['failed', 'interrupted'].includes(latest.status);
    top.className = 'pill' + (running ? ' run' : bad ? ' fail' : '');
    top.textContent = running ? `Running · ${stageInfo(latest)[0].split(' (')[0]}` : bad ? 'Last job failed' : 'Idle';
    $('submit').disabled = running;
    if (running) { $('formMsg').textContent = 'A job is running on the backend. Wait for it to finish (see the bar above).'; $('formMsg').className = 'hint'; }
    else if ($('formMsg').textContent.startsWith('A job is running')) $('formMsg').textContent = '';
    if (state.prevRunning && !running) {
      state.galleryKey = '';
      if (latest?.status === 'done' && latest.outputs) toast('Your result is ready.', () => openViewer(latest.outputs));
    }
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

// ─── Login / logout ──────────────────────────────────────
$('pwToggle').addEventListener('click', () => {
  const show = $('loginPw').type === 'password';
  $('loginPw').type = show ? 'text' : 'password';
  $('pwToggle').textContent = show ? 'Hide' : 'Show';
});
$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('loginMsg').textContent = '';
  try {
    const res = await fetch(`${API}/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: $('loginPw').value }) });
    const data = await res.json();
    if (data.status !== 'success') throw new Error(data.message || 'Login failed');
    location.reload();
  } catch (err) { $('loginMsg').textContent = err.message; $('loginPw').select(); }
});
$('logout').addEventListener('click', async () => { await fetch(`${API}/logout`, { method: 'POST' }); location.reload(); });
getJson('/auth').then((a) => {
  $('logout').hidden = !a.auth_required;
  if (a.auth_required && !a.authenticated) showLogin();
}).catch(() => {});

// ─── Publish queue (YouTube autopilot) ───────────────────
const IST = { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true };
const istText = (iso) => new Date(iso).toLocaleString('en-IN', IST) + ' IST';
const countdown = (iso) => {
  const s = Math.round((new Date(iso) - Date.now()) / 1000);
  if (Number.isNaN(s)) return '';
  if (s <= 0) return 'due now';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `in ${h ? `${h}h ` : ''}${m}m ${h ? '' : `${String(s % 60).padStart(2, '0')}s`}`.trim();
};
const pubOpen = new Set();   // item ids with the reschedule row open
let pubItems = [];

function pubCard(it) {
  const st = it.stats || {};
  const acts = [];
  if (['scheduled', 'uploaded_scheduled'].includes(it.status)) acts.push(`<button class="btn stop" data-act="veto" data-id="${esc(it.id)}" type="button">Stop</button>`);
  if (it.status === 'pending_approval') acts.push(`<button class="btn ok" data-act="approve" data-id="${esc(it.id)}" type="button">Approve</button><button class="btn" data-act="veto" data-id="${esc(it.id)}" type="button">Stop</button>`);
  if (it.status === 'vetoed') acts.push(`<button class="btn ok" data-act="approve" data-id="${esc(it.id)}" type="button">Restore</button>`);
  if (['scheduled', 'vetoed', 'failed', 'pending_approval'].includes(it.status)) acts.push(`<button class="btn ghost" data-act="resched" data-id="${esc(it.id)}" type="button">Reschedule</button>`);
  const resched = pubOpen.has(it.id) ? `<div class="row"><input type="datetime-local" id="rs-${esc(it.id)}"><button class="btn small" data-act="rsset" data-id="${esc(it.id)}" type="button">Set (IST)</button></div>` : '';
  return `<div class="pub" id="pub-${esc(it.id)}">
    <span class="tag ${esc(it.status)}">${esc(it.status === 'uploaded_scheduled' ? 'Scheduled on YouTube' : it.status.replace('_', ' '))} · ${esc(it.channel)}${it.kind === 'long' ? ' · long' : ''}</span>
    ${it.video_url ? `<video src="${it.video_url}#t=0.5" preload="metadata" controls playsinline></video>` : ''}
    <h3>${esc(it.title || '(untitled)')}</h3>
    <div class="when">${it.status === 'published' ? `Published · ${it.video_id ? `<a href="https://youtu.be/${esc(it.video_id)}" target="_blank" rel="noopener" style="color:var(--primary-h)">watch</a>` : ''}`
      : `Publishes <b>${esc(istText(it.publish_at))}</b> <span class="cd" data-at="${esc(it.publish_at)}">${esc(countdown(it.publish_at))}</span>`}</div>
    ${it.status === 'published' ? `<div class="when">${st.views ?? '–'} views · ${st.likes ?? '–'} likes</div>` : ''}
    ${it.error ? `<div class="banner">${esc(it.error)}</div>` : ''}
    <div class="acts">${acts.join('')}</div>${resched}</div>`;
}

async function loadPublish() {
  try {
    const [q, h] = await Promise.all([getJson('/publish/queue'), getJson('/publish/health')]);
    pubItems = q.items || [];
    const chans = Object.entries(h.channels || {});
    $('publish').hidden = !pubItems.length && !chans.length;
    $('pubBanner').innerHTML = chans.filter(([, c]) => !c.token).map(([n]) => `<div class="banner">Channel “${esc(n)}” is not connected to YouTube (no token). Publishing there will fail until you authorise it.</div>`).join('');
    if (document.activeElement?.closest?.('#pubList input')) return;   // don't wipe a half-typed date
    $('pubList').innerHTML = pubItems.map(pubCard).join('') || '<p class="empty">Nothing queued.</p>';
  } catch { /* publish module unavailable: keep the section hidden */ }
}

async function pubClick(e) {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  const { act, id } = b.dataset;
  if (act === 'resched') { pubOpen.has(id) ? pubOpen.delete(id) : pubOpen.add(id); loadPublish(); if (state.tab === 'approvals') loadApprovals(); return; }
  let path = `/publish/${encodeURIComponent(id)}/${act}`, body = null;
  if (act === 'rsset') {
    const v = $(`rs-${id}`)?.value;
    if (!v) return;
    path = `/publish/${encodeURIComponent(id)}/reschedule`;
    body = JSON.stringify({ publish_at: `${v}:00+05:30` });
    pubOpen.delete(id);
  }
  b.disabled = true;
  const res = await fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.detail || 'Action failed.'); }
  loadPublish();
  loadPublish();
  if (state.tab === 'approvals') loadApprovals();
}
$('pubList').addEventListener('click', pubClick);
$('apVideos').addEventListener('click', pubClick);

loadPublish();
setInterval(() => { if (state.tab === 'queue') loadPublish(); }, 10000);
setInterval(() => document.querySelectorAll('.cd').forEach((el) => { el.textContent = countdown(el.dataset.at); }), 1000);

// ─── Approvals (videos waiting, comment replies, weekly report) ──
const WAITING = ['scheduled', 'uploaded_scheduled', 'pending_approval', 'vetoed'];
async function loadApprovals() {
  try {
    const d = await getJson('/approvals');
    const vids = (d.queue || []).filter((i) => WAITING.includes(i.status));
    pubItems = vids;
    loadDaily();
    $('apVideos').innerHTML = vids.map(pubCard).join('') || '<p class="empty">No videos waiting.</p>';
    const drafts = Object.entries(d.comment_drafts || {}).flatMap(([ch, list]) => list.map((c) => ({ ...c, channel: ch })));
    if (!document.activeElement?.closest?.('#apComments textarea')) {
      $('apComments').innerHTML = drafts.map((c) => `<div class="pub cm" data-ch="${esc(c.channel)}" data-id="${esc(c.id)}">
        <div class="who">${esc(c.author || 'viewer')} · ${esc(c.channel)}</div><blockquote>${esc(c.text)}</blockquote>
        <textarea maxlength="500" aria-label="Reply draft">${esc(c.reply || '')}</textarea>
        <div class="acts"><button class="btn ok" data-cm="approve" type="button">Send</button><button class="btn" data-cm="reject" type="button">Skip</button></div></div>`).join('') || '<p class="empty">No comments need a reply.</p>';
    }
    const reps = Object.entries(d.weekly_report || {}).filter(([, r]) => r);
    $('apReport').innerHTML = reps.map(([ch, r]) => `<div class="pub"><b>${esc(ch)}</b><div class="nums">
      <div><b>${r.views ?? '–'}</b><span>views</span></div><div><b>${r.watch_minutes ?? '–'}</b><span>watch minutes</span></div>
      <div><b>${r.avg_view_pct ?? '–'}%</b><span>avg viewed</span></div><div><b>${r.subs ?? '–'}</b><span>subscribers</span></div></div>
      ${(r.top || []).slice(0, 3).map((x, i) => `<div class="when">${i + 1}. ${esc(typeof x === 'string' ? x : x.title || x.video_id || '')}${x.views != null ? ` · ${x.views} views` : ''}</div>`).join('')}
      ${r.insight ? `<p class="hint">${esc(r.insight)}</p>` : ''}</div>`).join('') || '<p class="empty">No weekly report yet.</p>';
    $('apBadge').hidden = !(vids.some((i) => i.status === 'pending_approval') || drafts.length);
  } catch { /* publish module unavailable */ }
}
$('apComments').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-cm]');
  if (!b) return;
  const card = b.closest('.cm');
  b.disabled = true;
  const body = b.dataset.cm === 'approve' ? JSON.stringify({ reply: card.querySelector('textarea').value }) : null;
  const res = await fetch(`${API}/comments/${encodeURIComponent(card.dataset.ch)}/${encodeURIComponent(card.dataset.id)}/${b.dataset.cm}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.detail || 'Action failed.'); }
  loadApprovals();
});
loadApprovals();
setInterval(() => { if (state.tab === 'approvals') loadApprovals(); }, 15000);

// Minimal markdown for the daily report: ## headings, - bullets, plain lines.
function mdLite(src) {
  let out = '', list = false;
  for (const raw of src.split(/\r?\n/)) {
    const line = raw.trim();
    const bullet = line.match(/^[-*]\s+(.*)/);
    if (!bullet && list) { out += '</ul>'; list = false; }
    if (bullet) { if (!list) { out += '<ul>'; list = true; } out += `<li>${esc(bullet[1])}</li>`; }
    else if (/^#{1,6}\s/.test(line)) out += `<h4>${esc(line.replace(/^#+\s*/, ''))}</h4>`;
    else if (line) out += `<p>${esc(line)}</p>`;
  }
  return out + (list ? '</ul>' : '');
}
async function loadDaily() {
  try {
    const d = await getJson('/reports/daily/latest');
    $('apDaily').innerHTML = d.text ? `<div class="pub daily"><b>Report · ${esc(d.date)}</b>${mdLite(d.text)}</div>` : '<p class="empty">No daily report yet.</p>';
  } catch { /* ignore */ }
}
