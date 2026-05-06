import psutil
import subprocess
import os
import threading
import queue
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Global State ────────────────────────────────────────────
_active_process: subprocess.Popen = None
_log_queue: queue.Queue = queue.Queue(maxsize=500)
_pipeline_phase = {"current": 0, "label": "Idle", "progress": 0}

PHASE_MAP = {
    "script_gen":  (1, "Script Generation"),
    "image_gen":   (2, "Image Generation"),
    "video_gen":   (3, "Video Generation"),
    "audio_gen":   (4, "Voice Generation"),
    "lipsync":     (5, "Lip Sync"),
    "assembler":   (6, "Final Export"),
}

def _parse_phase(line: str):
    """Detect pipeline phase transitions from log lines."""
    for key, (idx, label) in PHASE_MAP.items():
        if f"[Log] ▶ {key}" in line:
            _pipeline_phase["current"] = idx
            _pipeline_phase["label"] = label
            _pipeline_phase["progress"] = int((idx / 6) * 100)
        if f"[Log] ✅ {key}" in line:
            _pipeline_phase["progress"] = int((idx / 6) * 100)
    if "Complete in" in line or "Aborted" in line:
        _pipeline_phase["progress"] = 100 if "Complete" in line else _pipeline_phase["progress"]
        _pipeline_phase["label"] = "Done" if "Complete" in line else "Failed"

def _stream_process_output(proc: subprocess.Popen):
    """Background thread: reads stdout from main.py and puts lines into the queue."""
    global _active_process
    for raw in proc.stdout:
        line = raw.rstrip()
        _parse_phase(line)
        try:
            _log_queue.put_nowait(line)
        except queue.Full:
            _log_queue.get_nowait()  # drop oldest
            _log_queue.put_nowait(line)
    proc.wait()
    _log_queue.put_nowait("[SERVER] Process finished.")
    _active_process = None


# ─── Process Discovery (for pause/stop on externally launched jobs) ──────────
def get_active_job_process():
    """Find a running main.py Python process launched externally."""
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['name'] and 'python' in p.info['name'].lower() and p.info['cmdline']:
                cmdline = [str(cmd) for cmd in p.info['cmdline']]
                joined = " ".join(cmdline).lower()
                if "comfyui\\main.py" in joined or "comfyui/main.py" in joined:
                    continue
                if any(cmd.endswith('main.py') or cmd.endswith('\\main.py') or cmd.endswith('/main.py')
                       for cmd in cmdline):
                    return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # Fallback: look for the batch script
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['name'] and 'cmd' in p.info['name'].lower() and p.info['cmdline']:
                cmd_str = " ".join(p.info['cmdline']).lower()
                if 'run_anime_batch.bat' in cmd_str:
                    return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


# ─── API Endpoints ────────────────────────────────────────────

class StartJobRequest(BaseModel):
    topic: str
    preset: str = ""
    frames: int = 1
    skip_upscale: bool = True
    skip_lipsync: bool = True

@app.post("/api/start")
def start_job(req: StartJobRequest):
    global _active_process
    if _active_process and _active_process.poll() is None:
        return {"status": "error", "message": "A job is already running. Please stop it first."}
    if get_active_job_process():
        return {"status": "error", "message": "An external job is running. Please stop it first."}

    cmd = [
        "python", "-u", "main.py",
        "--topic", req.topic,
        "--frames", str(req.frames),
        "--timeout", "2700",
    ]
    if req.preset:
        cmd.extend(["--preset", req.preset])
    if req.skip_upscale:
        cmd.append("--skip-upscale")
    if req.skip_lipsync:
        cmd.append("--skip-lipsync")

    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"

    # Reset state
    _pipeline_phase.update({"current": 0, "label": "Starting...", "progress": 0})
    while not _log_queue.empty():
        try: _log_queue.get_nowait()
        except: break

    try:
        _active_process = subprocess.Popen(
            cmd,
            cwd=os.getcwd(),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        t = threading.Thread(target=_stream_process_output, args=(_active_process,), daemon=True)
        t.start()
        return {"status": "success", "message": "Job started.", "pid": _active_process.pid}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/logs/stream")
def stream_logs():
    """Server-Sent Events endpoint for live log streaming."""
    def event_gen():
        yield "data: [SERVER] Log stream connected.\n\n"
        while True:
            try:
                line = _log_queue.get(timeout=15)
                # Escape for SSE
                safe = line.replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"
            except queue.Empty:
                yield "data: [ping]\n\n"  # Keep-alive

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/status")
def get_status():
    global _active_process

    # Check our managed process
    if _active_process and _active_process.poll() is None:
        try:
            proc = psutil.Process(_active_process.pid)
            is_paused = (proc.status() == psutil.STATUS_STOPPED)
            return {
                "running": True,
                "paused": is_paused,
                "pid": _active_process.pid,
                "phase": _pipeline_phase["current"],
                "phase_label": _pipeline_phase["label"],
                "progress": _pipeline_phase["progress"],
            }
        except Exception:
            pass

    # Check for external processes
    p = get_active_job_process()
    if p:
        try:
            is_paused = (p.status() == psutil.STATUS_STOPPED)
            return {
                "running": True,
                "paused": is_paused,
                "pid": p.pid,
                "phase": _pipeline_phase["current"],
                "phase_label": _pipeline_phase["label"],
                "progress": _pipeline_phase["progress"],
            }
        except Exception:
            pass

    return {"running": False, "paused": False, "phase": 0, "phase_label": "Idle", "progress": 0}


@app.get("/api/metrics")
def get_metrics():
    """Real system metrics for the sidebar gauges."""
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    return {
        "cpu_percent": cpu,
        "ram_used_gb": round(ram.used / 1e9, 1),
        "ram_total_gb": round(ram.total / 1e9, 1),
        "ram_percent": ram.percent,
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_percent": disk.percent,
    }


@app.post("/api/pause")
def pause_job():
    global _active_process
    target = None
    if _active_process and _active_process.poll() is None:
        try: target = psutil.Process(_active_process.pid)
        except: pass
    if not target:
        target = get_active_job_process()
    if target:
        try:
            target.suspend()
            return {"status": "success", "message": "Job paused.", "pid": target.pid}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "No active job found."}


@app.post("/api/resume")
def resume_job():
    global _active_process
    target = None
    if _active_process and _active_process.poll() is None:
        try: target = psutil.Process(_active_process.pid)
        except: pass
    if not target:
        target = get_active_job_process()
    if target:
        try:
            target.resume()
            return {"status": "success", "message": "Job resumed.", "pid": target.pid}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "No active job found."}


@app.post("/api/stop")
def stop_job():
    global _active_process
    killed = False
    if _active_process and _active_process.poll() is None:
        try:
            _active_process.terminate()
            killed = True
        except Exception:
            pass
    target = get_active_job_process()
    if target:
        try:
            target.terminate()
            killed = True
        except Exception:
            pass
    if killed:
        _pipeline_phase.update({"current": 0, "label": "Idle", "progress": 0})
        _active_process = None
        return {"status": "success", "message": "Job terminated."}
    return {"status": "error", "message": "No active job found."}


@app.post("/api/open-output")
def open_output():
    outputs_dir = os.path.join(os.getcwd(), "outputs", "final")
    os.makedirs(outputs_dir, exist_ok=True)
    try:
        os.startfile(outputs_dir)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    print("Starting AI Video Factory Job Manager API on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
