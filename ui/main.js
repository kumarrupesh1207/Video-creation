// main.js — AI Video Factory Dashboard (Fully Wired)

document.addEventListener('DOMContentLoaded', () => {
    const API_URL = 'http://127.0.0.1:8000/api';

    // ─── Element References ───────────────────────────────────
    const startBtn      = document.querySelector('.btn-glow');
    const topicTextarea = document.querySelector('textarea');
    const logContainer  = document.querySelector('.panel-5 .terminal, .logs-body, .terminal');
    const statusBadge   = document.querySelector('.status-badge');
    const progressFill  = document.querySelector('.progress-bar .fill, .global-progress .fill');
    const progressValue = document.querySelector('.progress-value, .global-progress .value');
    const elapsedEl     = document.querySelector('.elapsed-value, .elapsed');

    // Clip card selection
    const clipCards = document.querySelectorAll('.output-card.clip');
    clipCards.forEach(card => {
        card.addEventListener('click', () => {
            clipCards.forEach(c => c.classList.remove('active'));
            card.classList.add('active');
        });
    });

    // ─── API Helper ───────────────────────────────────────────
    async function callApi(endpoint, body = null) {
        try {
            const opts = { method: 'POST', headers: {} };
            if (body) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
            const res = await fetch(`${API_URL}${endpoint}`, opts);
            return await res.json();
        } catch (err) {
            console.error('API Error:', err);
            return { status: 'error', message: err.message };
        }
    }

    // ─── Log Appender ─────────────────────────────────────────
    function appendLog(line) {
        if (!logContainer) return;
        if (line === '[ping]') return;

        const el = document.createElement('div');
        el.className = 'log-line';

        // Colour-code by tag
        if (line.includes('[Log] ✅') || line.includes('Complete'))
            el.style.color = '#22C55E';
        else if (line.includes('[Log] ❌') || line.includes('FAILED') || line.includes('Aborted'))
            el.style.color = '#EF4444';
        else if (line.includes('[Log] ▶') || line.includes('Starting'))
            el.style.color = '#A78BFA';
        else if (line.includes('[ComfyUI]'))
            el.style.color = '#38BDF8';
        else if (line.includes('[LLM]'))
            el.style.color = '#FB923C';
        else if (line.includes('[VRAM]') || line.includes('[SERVER]'))
            el.style.color = '#64748B';

        el.textContent = line;
        logContainer.appendChild(el);
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    // ─── Live Log Streaming (SSE) ─────────────────────────────
    let logEventSource = null;

    function startLogStream() {
        if (logEventSource) logEventSource.close();
        logEventSource = new EventSource(`${API_URL}/logs/stream`);
        logEventSource.onmessage = (e) => appendLog(e.data);
        logEventSource.onerror   = () => {
            // Silently reconnect after 3s if server is up
            setTimeout(startLogStream, 3000);
        };
    }
    startLogStream();

    // ─── Start Generation ─────────────────────────────────────
    if (startBtn) {
        startBtn.addEventListener('click', async () => {
            const topic = topicTextarea ? topicTextarea.value.trim() : '';
            if (!topic) { alert('Please enter a Topic / Prompt first!'); return; }

            startBtn.textContent = 'Initializing...';
            startBtn.disabled = true;

            const res = await callApi('/start', { topic });
            if (res.status === 'success') {
                startBtn.textContent = 'Job Running...';
                appendLog(`[UI] Job started — PID: ${res.pid}`);
            } else {
                alert('Error: ' + res.message);
                startBtn.textContent = 'Start Generation';
                startBtn.disabled = false;
            }
        });
    }

    // ─── All Buttons ──────────────────────────────────────────
    let isPaused = false;
    document.querySelectorAll('button').forEach(btn => {
        const t = btn.innerText.trim();

        if (t.includes('Stop Job')) {
            btn.addEventListener('click', async () => {
                const res = await callApi('/stop');
                if (res.status === 'success') {
                    appendLog('[UI] Task terminated.');
                    if (startBtn) { startBtn.textContent = 'Start Generation'; startBtn.disabled = false; }
                } else alert('Error: ' + res.message);
            });
        }

        if (t.includes('Pause Job')) {
            btn.addEventListener('click', async () => {
                if (isPaused) {
                    const res = await callApi('/resume');
                    if (res.status === 'success') { btn.textContent = 'Pause Job'; isPaused = false; appendLog('[UI] Job resumed.'); }
                } else {
                    const res = await callApi('/pause');
                    if (res.status === 'success') { btn.textContent = 'Resume Job'; isPaused = true; appendLog('[UI] Job paused.'); }
                }
            });
        }

        if (t.includes('Clear')) {
            btn.addEventListener('click', () => { if (logContainer) logContainer.innerHTML = ''; });
        }

        if (t.includes('Open Output Folder')) {
            btn.addEventListener('click', () => callApi('/open-output'));
        }

        if (t.includes('View Logs') || t.includes('View Full Logs')) {
            btn.addEventListener('click', () => {
                const panel = document.querySelector('.panel-5');
                if (panel) {
                    panel.scrollIntoView({ behavior: 'smooth' });
                    panel.style.boxShadow = '0 0 24px rgba(123,77,255,0.7)';
                    setTimeout(() => panel.style.boxShadow = '', 2000);
                }
            });
        }

        if (t.includes('+ New Project') || t.includes('New Project')) {
            btn.addEventListener('click', () => {
                if (topicTextarea) topicTextarea.value = '';
                const p = document.querySelector('.panel-1');
                if (p) p.scrollIntoView({ behavior: 'smooth' });
                if (startBtn) { startBtn.textContent = 'Start Generation'; startBtn.disabled = false; }
            });
        }
    });

    // Upload / player placeholders
    document.querySelectorAll('.upload-card, .play-btn, .vol-btn, .fs-btn').forEach(el => {
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (el.classList.contains('upload-card'))
                alert('File upload will be enabled in Phase 3.');
            else
                alert('Video player will stream output in Phase 3.');
        });
    });

    // ─── Pipeline Stepper Updater ─────────────────────────────
    function updateStepper(phase) {
        const steps = document.querySelectorAll('.step, .stepper-step');
        steps.forEach((s, i) => {
            s.classList.remove('active', 'completed', 'in-progress');
            if (i + 1 < phase)  s.classList.add('completed');
            if (i + 1 === phase) s.classList.add('active', 'in-progress');
        });
    }

    // ─── Status Polling (every 2s) ────────────────────────────
    let jobStartTime = null;
    let elapsedTimer = null;

    function startElapsed() {
        jobStartTime = Date.now();
        if (elapsedTimer) clearInterval(elapsedTimer);
        elapsedTimer = setInterval(() => {
            if (!elapsedEl) return;
            const s = Math.floor((Date.now() - jobStartTime) / 1000);
            const h = String(Math.floor(s / 3600)).padStart(2, '0');
            const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
            const sec = String(s % 60).padStart(2, '0');
            elapsedEl.textContent = `${h}:${m}:${sec}`;
        }, 1000);
    }

    function stopElapsed() {
        if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
    }

    setInterval(async () => {
        try {
            const status = await (await fetch(`${API_URL}/status`)).json();

            // Badge
            if (statusBadge) {
                if (status.running && status.paused) {
                    statusBadge.innerHTML = 'Paused';
                    statusBadge.className = 'status-badge paused';
                } else if (status.running) {
                    statusBadge.innerHTML = '<div class="dot"></div> Running';
                    statusBadge.className = 'status-badge running';
                    if (!jobStartTime) startElapsed();
                } else {
                    statusBadge.innerHTML = 'Idle';
                    statusBadge.className = 'status-badge';
                    stopElapsed();
                    jobStartTime = null;
                    if (startBtn && startBtn.textContent === 'Job Running...') {
                        startBtn.textContent = 'Start Generation';
                        startBtn.disabled = false;
                    }
                }
            }

            // Progress bar
            if (progressFill) progressFill.style.width = (status.progress || 0) + '%';
            if (progressValue) progressValue.textContent = (status.progress || 0) + '%';

            // Stepper
            updateStepper(status.phase || 0);

        } catch (e) { /* backend offline — silent */ }
    }, 2000);

    // ─── Real System Metrics (every 3s) ──────────────────────
    setInterval(async () => {
        try {
            const m = await (await fetch(`${API_URL}/metrics`)).json();

            const setGauge = (sel, val, label) => {
                const fill = document.querySelector(sel + ' .fill');
                const txt  = document.querySelector(sel + ' .value');
                if (fill) fill.style.width = val + '%';
                if (txt)  txt.textContent  = label;
            };

            setGauge('.gauge-cpu',  m.cpu_percent, m.cpu_percent.toFixed(0) + '%');
            setGauge('.gauge-ram',  m.ram_percent,  `${m.ram_used_gb} / ${m.ram_total_gb} GB`);
            setGauge('.gauge-disk', m.disk_percent, `${m.disk_used_gb} / ${m.disk_total_gb} GB`);

            // Simple text fallback for any stat bars in sidebar
            const cpuEl  = document.querySelector('.stat-cpu  .stat-val');
            const ramEl  = document.querySelector('.stat-ram  .stat-val');
            const diskEl = document.querySelector('.stat-disk .stat-val');
            if (cpuEl)  cpuEl.textContent  = m.cpu_percent.toFixed(0) + '%';
            if (ramEl)  ramEl.textContent   = `${m.ram_used_gb} / ${m.ram_total_gb} GB`;
            if (diskEl) diskEl.textContent  = `${m.disk_used_gb} / ${m.disk_total_gb} GB`;

        } catch (e) { /* silent */ }
    }, 3000);

});
