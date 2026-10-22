import psutil
from aurora import ensure_utf8_console, load_env

ensure_utf8_console()
load_env()
import subprocess
import os
import threading
import queue
import time
import json
import shutil
import yaml
import contextlib
from types import SimpleNamespace
import re
import sys
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("AURORA_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Auth (single user; password from .env AURORA_PASSWORD; unset = localhost-only, no login) ──
SESSION_DAYS = 7
_AUTH_EXEMPT = {"/api/login", "/api/logout", "/api/health", "/api/auth"}
_login_hits: dict[str, list[float]] = {}


def _password() -> str:
    return os.getenv("AURORA_PASSWORD", "").strip()


def _secret() -> bytes:
    """AURORA_SECRET from env/.env; generated and appended to .env on first use."""
    sec = os.getenv("AURORA_SECRET", "").strip()
    if not sec:
        import secrets
        sec = secrets.token_hex(32)
        os.environ["AURORA_SECRET"] = sec
        try:
            with open(Path(__file__).resolve().parent / ".env", "a", encoding="utf-8") as fh:
                fh.write(f"\nAURORA_SECRET={sec}\n")
        except OSError:
            pass
    return sec.encode()


def _sign(exp: int) -> str:
    import hashlib
    import hmac
    return f"{exp}.{hmac.new(_secret(), str(exp).encode(), hashlib.sha256).hexdigest()}"


def _valid_token(token: str | None) -> bool:
    import hmac
    try:
        exp = int((token or "").split(".", 1)[0])
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(token, _sign(exp))


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    if _password() and path.startswith("/api") and path not in _AUTH_EXEMPT and request.method != "OPTIONS":
        if not _valid_token(request.cookies.get("aurora_session")):
            return JSONResponse(status_code=401, content={"status": "error", "message": "Login required."})
    return await call_next(request)


class LoginRequest(BaseModel):
    password: str = Field("", max_length=200)


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd and host in ("127.0.0.1", "::1") else host


@app.post("/api/login")
def login(req: LoginRequest, request: Request):
    import hmac
    if not _password():
        return {"status": "success", "auth_required": False}
    ip, now = _client_ip(request), time.time()
    hits = [t for t in _login_hits.get(ip, []) if now - t < 60]
    if len(hits) >= 5:
        return JSONResponse(status_code=429, content={"status": "error", "message": "Too many attempts. Wait a minute."})
    _login_hits[ip] = hits + [now]
    if not hmac.compare_digest(req.password.encode(), _password().encode()):
        return JSONResponse(status_code=401, content={"status": "error", "message": "Wrong password."})
    resp = JSONResponse({"status": "success"})
    resp.set_cookie("aurora_session", _sign(int(now) + SESSION_DAYS * 86400), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="strict", path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"status": "success"})
    resp.delete_cookie("aurora_session", path="/")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth")
def auth_state(request: Request):
    return {"auth_required": bool(_password()), "authenticated": (not _password()) or _valid_token(request.cookies.get("aurora_session"))}

# ─── Global State ────────────────────────────────────────────
_active_process: subprocess.Popen = None
_log_queue: queue.Queue = queue.Queue(maxsize=500)
_pipeline_phase = {"current": 0, "label": "Idle", "progress": 0}
_batch_thread: threading.Thread = None
_batch_running = False
_batch_stop_requested = False

PHASE_MAP = {
    "script_gen":  (1, "Script Generation"),
    "image_gen":   (2, "Image Generation"),
    "video_gen":   (3, "Video Generation"),
    "talking_motion_gen": (3, "Talking Motion"),
    "full_i2v_motion_gen": (3, "Full Motion"),
    "audio_gen":   (4, "Voice Generation"),
    "lipsync":     (5, "Lip Sync"),
    "final_assembly": (6, "Final Export"),
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

def _push_log_line(line: str):
    """Append one log line to the in-memory stream buffer."""
    _parse_phase(line)
    try:
        _log_queue.put_nowait(line)
    except queue.Full:
        _log_queue.get_nowait()
        _log_queue.put_nowait(line)


def _stream_process_output(proc: subprocess.Popen):
    """Background thread: reads stdout from main.py and puts lines into the queue."""
    global _active_process, _batch_running
    for raw in proc.stdout:
        _push_log_line(raw.rstrip())
    proc.wait()
    _push_log_line("[SERVER] Process finished.")
    _active_process = None
    _batch_running = False
    _pipeline_phase.update({"current": 0, "label": "Idle", "progress": 0})


def _batch_log_path() -> Path:
    return _output_root() / "batch_log.json"


def _pipeline_log_path() -> Path:
    return Path(os.getcwd()) / "outputs" / "log.json"


def _output_root() -> Path:
    return Path(os.getcwd()) / "outputs"


def _load_config() -> dict:
    cfg_path = Path(os.getcwd()) / "config.yaml"
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _manifest_path() -> Path:
    return Path(os.getcwd()) / "assets" / "images" / "manifest.json"


def _load_manifest() -> list[dict]:
    manifest_path = _manifest_path()
    if not manifest_path.exists():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _latest_job() -> dict:
    jobs = _collect_jobs()
    return jobs[0] if jobs else {}


def _ffprobe_path() -> str:
    cfg = _load_config()
    ffmpeg_bin = cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")
    ffprobe_candidate = Path(ffmpeg_bin).with_name("ffprobe.exe")
    if ffprobe_candidate.exists():
        return str(ffprobe_candidate)
    return "ffprobe"


def _probe_video(path: str | None) -> dict:
    if not path:
        return {"playable": False, "width": None, "height": None}
    target = Path(path)
    if not target.exists():
        return {"playable": False, "width": None, "height": None}
    ffprobe = _ffprobe_path()
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"playable": False, "width": None, "height": None}
        width, height = result.stdout.strip().split("x")
        return {"playable": True, "width": int(width), "height": int(height)}
    except Exception:
        return {"playable": False, "width": None, "height": None}


def _probe_audio_video_streams(path: str | None) -> dict:
    if not path:
        return {"audio_stream_present": False, "video_duration_sec": None, "audio_duration_sec": None}
    target = Path(path)
    if not target.exists():
        return {"audio_stream_present": False, "video_duration_sec": None, "audio_duration_sec": None}
    try:
        result = subprocess.run(
            [
                _ffprobe_path(), "-v", "error",
                "-show_entries", "stream=codec_type,duration",
                "-of", "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"audio_stream_present": False, "video_duration_sec": None, "audio_duration_sec": None}
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        video = next((float(s.get("duration")) for s in streams if s.get("codec_type") == "video" and s.get("duration")), None)
        audio = next((float(s.get("duration")) for s in streams if s.get("codec_type") == "audio" and s.get("duration")), None)
        return {
            "audio_stream_present": audio is not None,
            "video_duration_sec": video,
            "audio_duration_sec": audio,
        }
    except Exception:
        return {"audio_stream_present": False, "video_duration_sec": None, "audio_duration_sec": None}


def _quality_check(final_output: str | None) -> dict:
    probe = _probe_video(final_output)
    av = _probe_audio_video_streams(final_output)
    delta = (
        abs(av["video_duration_sec"] - av["audio_duration_sec"])
        if av["video_duration_sec"] is not None and av["audio_duration_sec"] is not None
        else None
    )
    fr010 = bool(probe["playable"] and probe["width"] == 1080 and probe["height"] == 1920)
    fr011 = bool(av["audio_stream_present"] and delta is not None and delta <= 0.1)
    return {
        "passed": fr010 and fr011,
        "label": "FR-010/011 Pass" if fr010 and fr011 else "Needs Review",
        "fr010_passed": fr010,
        "fr011_passed": fr011,
        "audio_duration_delta_sec": delta,
        **probe,
        **av,
    }


def _collect_jobs() -> list[dict]:
    jobs = []
    for job_json in sorted(_output_root().glob("*/logs/job.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(job_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        quality = _quality_check(data.get("final_output"))
        g12_check = data.get("g12_check") or {}
        if g12_check.get("checked") and not g12_check.get("passed", False):
            quality["passed"] = False
            quality["label"] = "Needs Review"
        quality["g12_passed"] = g12_check.get("passed")
        data["quality_check"] = quality
        events = data.get("events", [])
        data["using_cached_asset"] = any(evt.get("event") == "asset_cache_hit" for evt in events)
        jobs.append(data)
    return jobs


def _cache_hit_count(jobs: list[dict] | None = None) -> int:
    items = jobs or _collect_jobs()
    return sum(1 for job in items if job.get("using_cached_asset"))


def _credit_cost_per_image() -> float:
    cfg = _load_config()
    return float(cfg.get("providers", {}).get("nvidia_nim", {}).get("credit_cost", 1.0))


def _batch_progress_payload(batch_state: dict) -> dict:
    items = batch_state.get("items", [])
    if not items:
        return {**batch_state, "aggregate_progress_percent": 0}

    latest_job = _latest_job()
    running_progress = int(latest_job.get("progress_percent", _pipeline_phase.get("progress", 0)) or 0)
    total = 0
    running_assigned = False
    for item in items:
        status = item.get("status")
        if status in {"success", "failed"}:
            total += 100
        elif status == "running" and not running_assigned:
            total += running_progress
            running_assigned = True
    return {
        **batch_state,
        "aggregate_progress_percent": int(total / max(len(items), 1)),
    }


def _write_batch_log(batch_state: dict):
    log_path = _batch_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(batch_state, indent=2), encoding="utf-8")


def _build_child_command(req: "StartJobRequest", topic: str) -> list:
    cmd = [
        "python", "-u", "main.py",
        "--topic", topic,
        "--mode", req.mode,
        "--motion-mode", req.motion_mode,
        "--visual-complexity", req.visual_complexity,
        "--frames", str(req.frames),
        "--timeout", "2700",
    ]
    if req.preset:
        cmd.extend(["--preset", req.preset])
    if req.skip_upscale:
        cmd.append("--skip-upscale")
    if req.skip_lipsync:
        cmd.append("--skip-lipsync")
    if req.force_new_image:
        cmd.append("--force-new-image")
    if req.narration_audio_path:
        cmd.extend(["--audio-narration-path", req.narration_audio_path])
    if req.identity_lock:
        cmd.append("--identity-lock")
    if req.width and req.height:
        cmd.extend(["--resolution", str(req.width), str(req.height)])
    return cmd


def _build_asset_child_command(req: "StartJobRequest") -> list:
    cmd = [
        "python", "-u", "scripts/render_from_asset.py",
        "--asset-key", req.asset_key,
        "--topic", (req.topic or ""),
        "--mode", req.mode,
        "--motion-mode", req.motion_mode,
        "--visual-complexity", req.visual_complexity,
        "--config", "config.yaml",
    ]
    if req.narration_audio_path:
        cmd.extend(["--audio-narration-path", req.narration_audio_path])
    if req.identity_lock:
        cmd.append("--identity-lock")
    return cmd


def _run_batch(req: "StartJobRequest", topics: list[str]):
    """Sequentially process each topic and maintain a batch-level log file."""
    global _active_process, _batch_running, _batch_stop_requested

    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"

    batch_state = {
        "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "mode": req.mode,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "items": [
            {
                "topic": topic,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "final_output": None,
                "error": None,
            }
            for topic in topics
        ],
    }
    _write_batch_log(batch_state)

    try:
        for idx, topic in enumerate(topics, start=1):
            if _batch_stop_requested:
                break

            _pipeline_phase.update({"current": 0, "label": f"Batch {idx}/{len(topics)}", "progress": 0})
            batch_state["items"][idx - 1]["status"] = "running"
            batch_state["items"][idx - 1]["started_at"] = datetime.now().isoformat(timespec="seconds")
            _write_batch_log(batch_state)

            _push_log_line(f"[Batch] ▶ Item {idx}/{len(topics)}: {topic}")
            cmd = _build_child_command(req, topic)
            proc = subprocess.Popen(
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
            _active_process = proc

            for raw in proc.stdout:
                _push_log_line(raw.rstrip())
            proc.wait()
            _active_process = None
            _push_log_line(f"[SERVER] Process finished for batch item {idx}/{len(topics)}.")

            child_log = {}
            try:
                child_log = json.loads(_pipeline_log_path().read_text(encoding="utf-8"))
            except Exception:
                child_log = {}

            item = batch_state["items"][idx - 1]
            item["completed_at"] = datetime.now().isoformat(timespec="seconds")
            item["final_output"] = child_log.get("final_output")
            if proc.returncode == 0:
                item["status"] = "success"
                _push_log_line(f"[Batch] ✅ Item {idx}/{len(topics)} complete.")
            else:
                item["status"] = "failed"
                item["error"] = child_log.get("steps", {}).get("image_gen", {}).get("error") or \
                                child_log.get("steps", {}).get("talking_motion_gen", {}).get("error") or \
                                child_log.get("steps", {}).get("full_i2v_motion_gen", {}).get("error") or \
                                child_log.get("steps", {}).get("video_gen", {}).get("error") or \
                                child_log.get("steps", {}).get("audio_gen", {}).get("error") or \
                                child_log.get("steps", {}).get("script_gen", {}).get("error") or \
                                "Pipeline failed"
                _push_log_line(f"[Batch] ❌ Item {idx}/{len(topics)} failed: {item['error']}")
            _write_batch_log(batch_state)

        completed = all(item["status"] == "success" for item in batch_state["items"])
        stopped = _batch_stop_requested
        batch_state["status"] = "stopped" if stopped else ("success" if completed else "completed_with_errors")
        batch_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        _write_batch_log(batch_state)
    finally:
        _batch_running = False
        _batch_stop_requested = False
        _active_process = None
        _pipeline_phase.update({"current": 0, "label": "Idle", "progress": 0})
        _push_log_line("[SERVER] Batch finished.")


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
    topics: list[str] = Field(default_factory=list)
    topic: str | None = None
    asset_key: str | None = None
    mode: str = "cinematic"
    motion_mode: str = "static_cinematic"
    visual_complexity: str = "standard"
    preset: str = ""
    frames: int = 1
    width: int | None = None
    height: int | None = None
    skip_upscale: bool = True
    skip_lipsync: bool = True
    force_new_image: bool = False
    reuse_existing_asset: bool = True
    narration_audio_path: str | None = None
    identity_lock: bool = False

def _start_job_impl(req: StartJobRequest):
    global _active_process, _batch_thread, _batch_running, _batch_stop_requested
    if _batch_running or (_active_process and _active_process.poll() is None):
        return {"status": "error", "message": "A job is already running. Please stop it first."}
    if get_active_job_process():
        return {"status": "error", "message": "An external job is running. Please stop it first."}

    if req.asset_key:
        topics = []
    else:
        topics = [topic.strip() for topic in (req.topics or []) if topic.strip()]
        if req.topic and req.topic.strip():
            topics.insert(0, req.topic.strip())
        if not topics:
            return {"status": "error", "message": "Provide at least one topic."}

    # Reset state
    _pipeline_phase.update({"current": 0, "label": "Starting...", "progress": 0})
    while not _log_queue.empty():
        try: _log_queue.get_nowait()
        except: break

    try:
        _batch_running = True
        _batch_stop_requested = False
        if req.asset_key:
            _write_batch_log({"status": "idle", "items": [], "aggregate_progress_percent": 0})
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUTF8"] = "1"
            cmd = _build_asset_child_command(req)
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
            threading.Thread(target=_stream_process_output, args=(_active_process,), daemon=True).start()
            return {
                "status": "success",
                "message": "Asset-based job started.",
                "asset_key": req.asset_key,
                "mode": req.mode,
            }
        _batch_thread = threading.Thread(target=_run_batch, args=(req, topics), daemon=True)
        _batch_thread.start()
        return {
            "status": "success",
            "message": "Batch started.",
            "batch_size": len(topics),
            "mode": req.mode,
        }
    except Exception as e:
        _batch_running = False
        return {"status": "error", "message": str(e)}


@app.post("/api/start")
def start_job(req: StartJobRequest):
    return _start_job_impl(req)


@app.post("/api/jobs")
def create_job(req: StartJobRequest):
    return _start_job_impl(req)


@app.get("/api/jobs")
def list_jobs():
    create = []
    for raw in _create_jobs.values():
        j = {k: v for k, v in raw.items() if not k.startswith("_")}
        if raw["status"] == "running":
            j["elapsed_secs"] = round(time.time() - raw["_t0"], 1)
            j["stage_elapsed_secs"] = round(time.time() - raw.get("_stage_t", raw["_t0"]), 1)
        create.append(j)
    return {"items": _collect_jobs(), "create_jobs": create[::-1]}


@app.get("/api/batch")
def batch_status():
    log_path = _batch_log_path()
    if not log_path.exists():
        return {"status": "idle", "items": [], "aggregate_progress_percent": 0}
    try:
        return _batch_progress_payload(json.loads(log_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {"status": "unknown", "items": [], "aggregate_progress_percent": 0}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job_path = _output_root() / job_id / "logs" / "job.json"
    if not job_path.exists():
        return {"status": "error", "message": "Job not found."}
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["quality_check"] = _quality_check(data.get("final_output"))
    return data


@app.get("/api/providers/status")
def provider_status():
    cfg = _load_config()
    providers_cfg = cfg.get("providers", {})
    manifest = _load_manifest()
    manifest_count = len(manifest)
    nim_enabled = providers_cfg.get("nvidia_nim", {}).get("enabled", False)
    quota_limit = providers_cfg.get("nvidia_nim", {}).get("quota", 0)
    jobs = _collect_jobs()
    cache_hits = _cache_hit_count(jobs)
    credit_cost = _credit_cost_per_image()
    return {
        "backend": {
            "online": True,
            "api_base": "http://127.0.0.1:8000",
        },
        "nvidia_nim": {
            "enabled": nim_enabled,
            "api_key_present": bool(os.getenv("NVIDIA_API_KEY")),
            "model": providers_cfg.get("nvidia_nim", {}).get("models", {}).get("cinematic"),
            "base_url": providers_cfg.get("nvidia_nim", {}).get("base_url"),
            "quota_limit_pct": quota_limit,
            "quota_usage_pct": None,
            "credit_cost": credit_cost,
        },
        "local_comfyui": {
            "enabled": True,
            "api_url": cfg.get("models", {}).get("comfyui", {}).get("api_url"),
        },
        "asset_library": {
            "count": manifest_count,
            "reuse_existing_asset": cfg.get("assets", {}).get("reuse_existing_asset", True),
            "cached_hits": cache_hits,
            "credit_savings": round(cache_hits * credit_cost, 2),
            "avg_latency_ms": round(
                sum(float(item.get("latency_ms", 0) or 0) for item in manifest) / manifest_count, 1
            ) if manifest_count else 0,
        },
    }


@app.get("/api/assets")
def list_assets():
    items = []
    for record in _load_manifest():
        asset_path = Path(record.get("output_path", ""))
        items.append({
            **record,
            "latency_ms": int(record.get("latency_ms", 0) or 0),
            "generation_cost": float(record.get("generation_cost", 0.0) or 0.0),
            "image_url": f"/api/assets/image/{asset_path.name}" if asset_path.name else None,
        })
    return {"items": items}


@app.post("/api/smoke-test")
def create_smoke_test():
    manifest = _load_manifest()
    if manifest:
        first_asset = manifest[-1]
        req = StartJobRequest(
            asset_key=first_asset.get("asset_key"),
            topic=first_asset.get("normalized_prompt", "Aurora smoke test cached asset"),
            mode="static_cinematic",
            force_new_image=False,
            reuse_existing_asset=True,
        )
    else:
        req = StartJobRequest(
            topic="Aurora smoke test cinematic warrior",
            mode="static_cinematic",
            width=384,
            height=640,
            frames=1,
            force_new_image=False,
            reuse_existing_asset=True,
        )
    return _start_job_impl(req)


@app.get("/api/assets/image/{filename}")
def get_asset_image(filename: str):
    asset_dir = Path(os.getcwd()) / "assets" / "images"
    target = (asset_dir / filename).resolve()
    if asset_dir.resolve() not in target.parents and target != asset_dir.resolve():
        return {"status": "error", "message": "Invalid asset path."}
    if not target.exists():
        return {"status": "error", "message": "Asset not found."}
    media_type = "image/jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(target, media_type=media_type, filename=target.name)


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
    global _active_process, _batch_running

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

    if _batch_running:
        return {
            "running": True,
            "paused": False,
            "pid": None,
            "phase": _pipeline_phase["current"],
            "phase_label": _pipeline_phase["label"],
            "progress": _pipeline_phase["progress"],
        }

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


@app.get("/api/latest-output")
def latest_output():
    final_dir = Path(os.getcwd()) / "outputs" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    videos = sorted(final_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not videos:
        return {"video_url": None}

    latest = videos[0]
    return {
        "filename": latest.name,
        "video_url": f"/api/output/final/{latest.name}",
        "updated_at": int(latest.stat().st_mtime),
    }


@app.get("/api/output/final/{filename}")
def get_final_output(filename: str):
    final_dir = Path(os.getcwd()) / "outputs" / "final"
    target = (final_dir / filename).resolve()
    if final_dir.resolve() not in target.parents:
        return {"status": "error", "message": "Invalid file path."}
    if not target.exists():
        return {"status": "error", "message": "File not found."}
    return FileResponse(target, media_type="video/mp4", filename=target.name)


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
        "gpu": _gpu_info(),
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
    global _active_process, _batch_stop_requested, _batch_running
    killed = False
    if _batch_running:
        _batch_stop_requested = True
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


# ─── Create flow (single prompt -> image [-> reel]) ──────────
# Reuses ImageSwitchboard + aurora.pipeline.run + the media validators; status is exposed via /api/jobs.
_create_jobs: dict[str, dict] = {}


def _jobs_dir() -> Path:
    return _output_root() / "jobs"


def _save_job(job: dict):
    """Persist a create-job (without runtime-only '_' keys) so history survives API restarts."""
    try:
        _jobs_dir().mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in job.items() if not k.startswith("_")}
        (_jobs_dir() / f"{job['job_id']}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, KeyError, TypeError):
        pass


def _set_stage(job: dict, stage: str):
    job.update(stage=stage, _stage_t=time.time())
    job.setdefault("timeline", []).append({"stage": stage, "at": datetime.now().isoformat(timespec="seconds")})
    _save_job(job)


def _load_jobs(limit: int = 50):
    """Reload persisted jobs; anything that was still running when the server died becomes 'interrupted'."""
    for p in sorted(_jobs_dir().glob("create_*.json"))[-limit:]:
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("status") == "running":
            job.update(status="interrupted", stage="interrupted",
                       error="The server restarted while this job was running.")
            _save_job(job)
        _create_jobs.setdefault(job["job_id"], job)


_load_jobs()
_create_lock = threading.Lock()
_ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


class CreateRequest(BaseModel):
    prompt: str = Field(..., max_length=1000)
    negative_prompt: str = Field("", max_length=600)
    mode: str = Field("reel", pattern="^(image|reel)$")
    aspect: str = Field("9:16", pattern="^(9:16|1:1|16:9|custom)$")
    width: int = Field(1080, ge=256, le=2160)   # custom only (reel size)
    height: int = Field(1920, ge=256, le=2160)
    duration: float = Field(15.0, ge=3, le=60)
    fps: int = Field(24, ge=12, le=60)
    provider: str = Field("auto", pattern="^(auto|local_comfyui|nvidia_nim)$")
    music_path: str | None = None    # an uploaded file (overrides music)
    music: str = Field("auto", pattern="^(auto|none)$")
    voiceover: bool = False
    voiceover_text: str = Field("", max_length=500)
    captions: bool = True
    voice_engine: str = Field("auto", pattern="^(auto|edge|xtts|piper)$")
    gender: str = Field("female", pattern="^(female|male)$")
    speaker_wav: str | None = None    # uploaded voice sample (xtts cloning)
    motion: str = Field("camera", pattern="^(camera|ai)$")
    action: str = Field("", max_length=300)
    source: str = Field("prompt", pattern="^(prompt|upload)$")
    image_path: str | None = None     # upload only: a file from /api/upload-image
    restyle: bool = False             # upload only: ComfyUI img2img instead of using the photo as-is
    denoise: float = Field(0.45, ge=0.35, le=0.75)
    face_lock: bool = True            # restyle only: IP-Adapter face conditioning on the same photo


class _Tee:
    """Copies stdout while a reel renders so pipeline warnings (AI-motion fallback) can reach the job."""
    def __init__(self, real):
        self.real, self.text = real, ""

    def write(self, data):
        self.text += data
        return self.real.write(data)

    def flush(self):
        self.real.flush()


def _gen_size(w: int, h: int) -> list[int]:
    """Generation size: long side 768, multiples of 8 (SD1.5-friendly)."""
    k = 768 / max(w, h)
    return [max(256, round(w * k / 8) * 8), max(256, round(h * k / 8) * 8)]


def _run_create_job(job_id: str, req: CreateRequest):
    import yaml
    from datetime import date
    job = _create_jobs[job_id]
    t0 = time.perf_counter()
    try:
        from aurora.autorun import QUALITY_SUFFIX, slugify
        from aurora.monitoring.media_validator import validate_image, validate_video
        from aurora.pipeline import run as render_reel
        from backend.app.services.image_switchboard import ImageSwitchboard

        cfg = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml").read_text(encoding="utf-8"))
        if req.provider in ("local_comfyui", "nvidia_nim"):
            cfg.setdefault("providers", {})["fallback_order"] = [req.provider]
        rw, rh = _ASPECTS.get(req.aspect) or (max(256, req.width // 2 * 2), max(256, req.height // 2 * 2))
        out_dir = _output_root() / "final" / date.today().isoformat()
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = f"{(slugify(req.prompt)[:-7].rstrip('-') if req.prompt.strip() else 'photo')}-{datetime.now():%H%M%S}"
        job["slug"] = slug
        _set_stage(job, "restyle" if (req.source == "upload" and req.restyle) else "image")
        t_img = time.perf_counter()
        if req.source == "upload" and not req.restyle:
            from PIL import Image
            with Image.open(req.image_path) as im:
                image = SimpleNamespace(output_path=Path(req.image_path), width=im.width, height=im.height,
                                        provider="upload", model=None, seed=None)
        elif req.source == "upload":
            from backend.app.providers.local_comfyui import LocalComfyUIProvider
            job["events"] = ["Restyling your photo with ComfyUI (CPU, ~5-10 min)"]
            image = LocalComfyUIProvider(cfg).generate_from_image(
                source_path=Path(req.image_path), prompt=req.prompt + QUALITY_SUFFIX, style="cinematic",
                negative_prompt=req.negative_prompt, denoise=req.denoise, face_lock=req.face_lock, resolution=_gen_size(rw, rh), job_id=job_id)
        else:
            image = ImageSwitchboard(cfg).generate(
                req.prompt + QUALITY_SUFFIX, "cinematic", _gen_size(rw, rh), job_id,
                negative_prompt=req.negative_prompt, force_new_image=True)
        t_img = time.perf_counter() - t_img
        report = validate_image(image.output_path, image.width, image.height)
        if not report.ok:
            raise RuntimeError(f"image failed validation: {report.errors}")
        img_copy = out_dir / f"{slug}{Path(image.output_path).suffix}"
        shutil.copyfile(image.output_path, img_copy)
        sidecar = {"status": "ok", "engine_mode": "real", "source": "ui", "topic": req.prompt or "Uploaded photo", "slug": slug,
                   "created_at": datetime.now().isoformat(timespec="seconds"), "mode": req.mode,
                   "input": ({"source": "upload", "restyle": req.restyle, "denoise": req.denoise, "face_lock": req.face_lock}
                             if req.source == "upload" else {"source": "prompt"}),
                   "negative_prompt": req.negative_prompt, "aspect": req.aspect, "caption": req.prompt,
                   "image": {"provider": image.provider, "model": image.model, "seed": image.seed,
                             "path": str(img_copy), "width": image.width, "height": image.height},
                   "reel": None, "timings_secs": {"image": round(t_img, 1)}}
        if req.mode == "reel":
            ai = req.motion == "ai"
            job["events"] = ["AI motion running on Kaggle (free GPU, ~15-35 min)"] if ai else []
            cfg_prompt = req.prompt or "the subject"
            if ai:
                if req.action.strip():
                    cfg.setdefault("motion", {}).setdefault("i2v", {}).setdefault("kaggle", {})["motion_suffix"] = (
                        f"Static camera. {req.action.strip()}. Consistent face and body.")
                    cfg["motion"]["i2v"]["kaggle"]["loop_mode"] = "forward"  # a reversed run looks like running backwards
                cfg["_motion_prompt"] = cfg_prompt
            mp4 = out_dir / f"{slug}.mp4"
            from aurora.audio import resolve_audio
            from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns
            _set_stage(job, "audio")
            audio, audio_note = resolve_audio(
                FFmpegKenBurns().ffmpeg, req.duration, _output_root() / "audio", slug,
                music=req.music_path or (req.music if req.music == "auto" else None), prompt=req.prompt,
                voiceover_text=(req.voiceover_text.strip() or req.prompt) if req.voiceover else None,
                voice_options={"voice_engine": req.voice_engine, "gender": req.gender,
                               **({"speaker_wav": req.speaker_wav} if req.speaker_wav else {})})
            job.setdefault("events", []).append(f"Audio: {audio_note}")
            t_reel = time.perf_counter()
            _set_stage(job, "ai_motion" if ai else "reel")
            tee = _Tee(sys.stdout)
            with contextlib.redirect_stdout(tee):
                extra = {"motion": "ai", "config": cfg} if ai else {}
                if req.captions:
                    from aurora.audio import voice_duration
                    extra.update(caption_text=((req.voiceover_text.strip() or req.prompt) if req.voiceover else req.prompt),
                                 caption_seconds=(voice_duration(_output_root() / "audio", slug) if req.voiceover else None))
                render_reel(Path(image.output_path), mp4, req.duration, req.fps, rw, rh, audio, **extra)
            fell_back = ai and "AI motion failed" in tee.text
            if fell_back:
                reason = tee.text.split("falling back to camera motion:", 1)[-1].strip().splitlines()[0][:200]
                job["events"].append(f"AI motion failed, used camera motion instead: {reason}")
            _set_stage(job, "validating")
            vr = validate_video(mp4, rw, rh, req.fps, req.duration, require_audio=True, deep=True,
                                input_path=image.output_path)
            if not vr.ok:
                raise RuntimeError(f"reel failed validation: {vr.errors}")
            sidecar["reel"] = {"path": str(mp4), "width": vr.width, "height": vr.height, "fps": vr.fps,
                               "duration_secs": vr.duration_secs, "has_audio": vr.has_audio, "sha256": vr.sha256}
            sidecar["audio"] = audio_note
            sidecar["motion"] = "camera_fallback" if fell_back else req.motion
            if ai:
                sidecar["action"] = req.action
            sidecar["timings_secs"]["reel"] = round(time.perf_counter() - t_reel, 1)
        sidecar["timings_secs"]["total"] = round(time.perf_counter() - t0, 1)
        (out_dir / f"{slug}.json").write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
        job.update(status="done", outputs=_gallery_item(sidecar))
        _set_stage(job, "done")
    except Exception as exc:  # noqa: BLE001 - report to the UI
        job.update(status="failed", error=str(exc))
        _set_stage(job, "failed")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        job["elapsed_secs"] = round(time.perf_counter() - t0, 1)
        _save_job(job)


def _comfy_up(cfg: dict) -> bool:
    import urllib.request
    url = (cfg.get("models", {}).get("comfyui", {}) or {}).get("api_url", "http://127.0.0.1:8188").rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/system_stats", timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def _preflight(req: "CreateRequest") -> str | None:
    """Plain-words reason this request cannot run right now, or None."""
    if (req.source == "upload" and req.restyle) or (req.source == "prompt" and req.provider == "local_comfyui"):
        if not _comfy_up(_load_config()):
            return "ComfyUI is not running (port 8188). Start it with start_aurora.bat, then try again."
    if req.mode == "reel" and req.motion == "ai":
        from aurora import load_env
        load_env()
        if not (os.getenv("KAGGLE_USERNAME", "").strip() and os.getenv("KAGGLE_KEY", "").strip()):
            return "AI motion needs Kaggle keys (KAGGLE_USERNAME / KAGGLE_KEY in .env). Use Camera motion or add the keys."
    return None


@app.post("/api/create")
def create_single(req: CreateRequest):
    if req.speaker_wav:
        up = (_output_root() / "uploads").resolve()
        if up not in Path(req.speaker_wav).resolve().parents or not Path(req.speaker_wav).is_file():
            raise HTTPException(status_code=422, detail="speaker_wav must be a file uploaded via /api/upload-music.")
    if req.source == "upload":
        up = (_output_root() / "uploads").resolve()
        if not req.image_path or up not in Path(req.image_path).resolve().parents or not Path(req.image_path).is_file():
            raise HTTPException(status_code=422, detail="image_path must be a file uploaded via /api/upload-image.")
        if req.restyle and not req.prompt.strip():
            raise HTTPException(status_code=422, detail="Restyle needs a prompt.")
    elif not req.prompt.strip():
        return {"status": "error", "message": "Prompt is empty."}
    if req.music_path:
        uploads = (_output_root() / "uploads").resolve()
        if uploads not in Path(req.music_path).resolve().parents or not Path(req.music_path).is_file():
            return {"status": "error", "message": "music_path must be a file uploaded via /api/upload-music."}
    problem = _preflight(req)
    if problem:
        return JSONResponse(status_code=503, content={"status": "error", "message": problem})
    with _create_lock:
        if _batch_running or any(j["status"] == "running" for j in _create_jobs.values()):
            return {"status": "error", "message": "A job is already running."}
        job_id = f"create_{datetime.now():%Y%m%d_%H%M%S}"
        _create_jobs[job_id] = {
            "job_id": job_id, "status": "running", "stage": "queued", "prompt": req.prompt or "Uploaded photo",
            "mode": req.mode, "aspect": req.aspect,
            "provider": ("upload" if req.source == "upload" and not req.restyle else req.provider), "duration": req.duration,
            "fps": req.fps, "motion": req.motion, "started_at": datetime.now().isoformat(timespec="seconds"),
            "_t0": time.time(), "_stage_t": time.time(), "request": req.model_dump(), "timeline": [],
        }
        _save_job(_create_jobs[job_id])
    threading.Thread(target=_run_create_job, args=(job_id, req), daemon=True).start()
    return {"status": "success", "job_id": job_id}


@app.post("/api/upload-music")
async def upload_music(request: Request, name: str = "music.mp3"):
    ext = Path(name).suffix.lower()
    if ext not in _AUDIO_EXT:
        return {"status": "error", "message": f"Unsupported audio type {ext or '(none)'}."}
    if int(request.headers.get("content-length") or 0) > 50 * 1024 * 1024:
        return {"status": "error", "message": "File empty or larger than 50 MB."}
    body = await request.body()
    if not body or len(body) > 50 * 1024 * 1024:
        return {"status": "error", "message": "File empty or larger than 50 MB."}
    dest_dir = _output_root() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).stem)[:40]
    dest = dest_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{safe}{ext}"
    dest.write_bytes(body)
    return {"status": "success", "path": str(dest), "name": name, "size": len(body)}


_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/api/upload-image")
async def upload_image(request: Request, name: str = "photo.jpg"):
    ext = Path(name).suffix.lower()
    if ext not in _IMAGE_EXT:
        return {"status": "error", "message": f"Unsupported image type {ext or '(none)'}. Use jpg, png or webp."}
    if int(request.headers.get("content-length") or 0) > 15 * 1024 * 1024:
        return {"status": "error", "message": "Image empty or larger than 15 MB."}
    body = await request.body()
    if not body or len(body) > 15 * 1024 * 1024:
        return {"status": "error", "message": "Image empty or larger than 15 MB."}
    dest_dir = _output_root() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).stem)[:40]
    dest = dest_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{safe}{ext}"
    dest.write_bytes(body)
    from aurora.monitoring.media_validator import validate_image
    rep = validate_image(dest)
    problem = "; ".join(rep.errors) if not rep.ok else (
        "Image must be at least 256 px on each side." if min(rep.width or 0, rep.height or 0) < 256 else
        "Image is larger than 8000 px." if max(rep.width or 0, rep.height or 0) > 8000 else "")
    if problem:
        dest.unlink(missing_ok=True)
        return {"status": "error", "message": problem}
    return {"status": "success", "path": str(dest), "name": name, "size": len(body), "width": rep.width, "height": rep.height}


def _rel_final(path: str | None) -> str | None:
    root = (_output_root() / "final").resolve()
    try:
        return Path(path).resolve().relative_to(root).as_posix() if path and Path(path).exists() else None
    except ValueError:
        return None


def _gallery_item(data: dict) -> dict:
    reel, img = _rel_final((data.get("reel") or {}).get("path")), _rel_final((data.get("image") or {}).get("path"))
    dims = data.get("reel") or data.get("image") or {}
    return {"slug": data.get("slug"), "prompt": data.get("topic"), "created_at": data.get("created_at"),
            "provider": (data.get("image") or {}).get("provider"), "mode": "reel" if reel else "image",
            "video_url": f"/api/files/{reel}" if reel else None, "image_url": f"/api/files/{img}" if img else None,
            "duration_secs": (data.get("reel") or {}).get("duration_secs"),
            "size": f"{dims.get('width', '?')}x{dims.get('height', '?')}"}


@app.get("/api/gallery")
def gallery():
    items = []
    for sc in (_output_root() / "final").glob("*/*.json"):
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") == "ok":
            item = _gallery_item(data)
            if item["video_url"] or item["image_url"]:
                items.append(item)
    items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return {"items": items}


@app.get("/api/files/{rel_path:path}")
def get_output_file(rel_path: str, download: bool = False):
    root = (_output_root() / "final").resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents or not target.is_file() or target.suffix.lower() not in {".mp4", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target, filename=target.name if download else None)


def _gpu_info():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().splitlines()[0].split(",")
        return {"name": out[0].strip(), "util_percent": float(out[1]), "vram_used_gb": round(float(out[2]) / 1024, 1),
                "vram_total_gb": round(float(out[3]) / 1024, 1)}
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    import uvicorn
    print("Starting AI Video Factory Job Manager API on http://127.0.0.1:8000")
    host = os.getenv("AURORA_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not _password():
        print("[aurora] WARNING: AURORA_PASSWORD is not set; refusing to bind", host, "- using 127.0.0.1 only.", flush=True)
        host = "127.0.0.1"
    elif not _password():
        print("[aurora] No AURORA_PASSWORD set: localhost-only, no login. Set it in .env before any LAN/phone access.", flush=True)
    uvicorn.run(app, host=host, port=8000, log_level="warning")
