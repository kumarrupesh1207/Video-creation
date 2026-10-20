"""
comfy_orchestrator.py
─────────────────────
WebSocket-based ComfyUI API client.

Key public methods:
  run_workflow(workflow_path, overrides)  ← used by the "First Light" test
  queue_prompt(workflow_path, overrides)  ← alias
  free_vram()                             ← VRAM cleanup between phases

Execution model:
  - Connects to ComfyUI WebSocket ONCE per call
  - Polls execution messages until the prompt is fully done
  - Downloads all output images/videos and saves them locally
  - Returns list of saved Path objects

VRAM note:
  Call free_vram() immediately after run_workflow() completes
  BEFORE loading XTTS-v2 or invoking Wav2Lip.
"""

import json
import uuid
import time
import subprocess
import requests
from pathlib import Path

try:
    import websocket          # pip install websocket-client
except ImportError:
    websocket = None


class ComfyOrchestrator:
    """WebSocket-based ComfyUI API orchestrator."""

    # ──────────────────────────────────────────────────────────────────────────
    # Init
    # ──────────────────────────────────────────────────────────────────────────
    def __init__(self, config: dict):
        comfy_cfg = config.get("models", {}).get("comfyui", {})
        self.comfy_cfg = comfy_cfg
        self.api_url   = comfy_cfg.get("api_url",   "http://127.0.0.1:8188")
        self.ws_url    = comfy_cfg.get("ws_url",    "ws://127.0.0.1:8188/ws")
        self.client_id = comfy_cfg.get("client_id", f"vf_{uuid.uuid4().hex[:8]}")

        self.img_out = Path(config.get("paths", {}).get("images_dir", "outputs/images"))
        self.vid_out = Path(config.get("paths", {}).get("videos_dir", "outputs/videos"))
        self.img_out.mkdir(parents=True, exist_ok=True)
        self.vid_out.mkdir(parents=True, exist_ok=True)

        self._verify_connection()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────
    def run_workflow(self, workflow_path: str, overrides: dict = None,
                     timeout: int = 900) -> list:
        """
        Load a workflow JSON, apply overrides, queue it, and return
        the list of downloaded output Paths.

        Args:
            timeout: Seconds to wait for ComfyUI to finish.
                     Default 900s (15 min) — covers CPU-mode SD 1.5 generation.
                     For GPU: typically 30-120s per image.
        """
        if Path(workflow_path).name == "image_gen.json":
            timeout = max(timeout, 3600)
        return self._execute(workflow_path, overrides, timeout)

    # Alias for backward compat
    def queue_prompt(self, workflow_path: str, overrides: dict = None,
                     timeout: int = 900) -> list:
        return self.run_workflow(workflow_path, overrides, timeout)

    def free_vram(self) -> None:
        """
        POST /free to ComfyUI to unload all models from VRAM.
        Always call this BEFORE loading XTTS-v2 or Wav2Lip.
        """
        try:
            resp = requests.post(
                f"{self.api_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10
            )
            if resp.status_code == 200:
                print("[ComfyUI] ✅ VRAM freed via /free endpoint.")
            else:
                print(f"[ComfyUI] ⚠️  /free returned HTTP {resp.status_code}")
        except Exception as e:
            print(f"[ComfyUI] ⚠️  free_vram() failed (non-fatal): {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────
    def _try_healthcheck(self) -> dict | None:
        """Return parsed system stats when available, else any live HTTP signal."""
        stats_url = f"{self.api_url}/system_stats"
        try:
            resp = requests.get(stats_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        # Some ComfyUI builds can answer the UI before system_stats is ready.
        resp = requests.get(self.api_url, timeout=5)
        resp.raise_for_status()
        return None

    def _start_comfyui(self) -> bool:
        """Launch ComfyUI using the configured portable command."""
        if not self.comfy_cfg.get("auto_start", False):
            return False

        launcher_dir = self.comfy_cfg.get("launcher_dir")
        launcher_cmd = self.comfy_cfg.get("launcher_cmd")
        if not launcher_dir or not launcher_cmd:
            print("[ComfyUI] ⚠️  auto_start enabled, but launcher_dir/launcher_cmd is missing.")
            return False

        cmd = launcher_cmd if isinstance(launcher_cmd, list) else [str(launcher_cmd)]
        launch_dir = Path(launcher_dir)
        if cmd and not Path(cmd[0]).is_absolute():
            cmd = [str(launch_dir / cmd[0]), *cmd[1:]]
        try:
            subprocess.Popen(
                cmd,
                cwd=str(launch_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
            )
            print(f"[ComfyUI] Launching portable server from: {launcher_dir}")
            return True
        except Exception as e:
            print(f"[ComfyUI] ⚠️  auto-start failed: {e}")
            return False

    def _verify_connection(self) -> None:
        """Fail fast if ComfyUI is not reachable."""
        try:
            stats = self._try_healthcheck()
            if stats:
                devices = stats.get("devices", [{}])
                vram_gb = devices[0].get("vram_total", 0) / (1024 ** 3)
                print(f"[ComfyUI] ✅ Connected — VRAM: {vram_gb:.1f} GB total")
            else:
                print("[ComfyUI] ✅ Connected — HTTP server is responding.")
        except requests.ConnectionError:
            launched = self._start_comfyui()
            if launched:
                startup_timeout = int(self.comfy_cfg.get("startup_timeout_sec", 120))
                deadline = time.time() + startup_timeout
                print(f"[ComfyUI] Waiting up to {startup_timeout}s for server startup...")
                while time.time() < deadline:
                    try:
                        stats = self._try_healthcheck()
                        if stats:
                            devices = stats.get("devices", [{}])
                            vram_gb = devices[0].get("vram_total", 0) / (1024 ** 3)
                            print(f"[ComfyUI] ✅ Connected — VRAM: {vram_gb:.1f} GB total")
                        else:
                            print("[ComfyUI] ✅ Connected — HTTP server is responding.")
                        return
                    except Exception:
                        time.sleep(2)

            raise RuntimeError(
                "[ComfyUI] ❌ Cannot reach ComfyUI at " + self.api_url +
                "\n  → Start ComfyUI first:  python main.py  (in ComfyUI folder)"
            )
        except Exception as e:
            raise RuntimeError(f"[ComfyUI] ❌ Connection check failed: {e}")

    def _load_workflow(self, path: str) -> dict:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"[ComfyUI] Workflow not found: {p}\n"
                "  → Export from ComfyUI: Settings → Enable Dev Mode → Save (API Format)"
            )
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"[ComfyUI] Malformed workflow JSON: {e}")

    def _apply_overrides(self, workflow: dict, overrides: dict) -> dict:
        """Merge override values into workflow node inputs."""
        if not overrides:
            return workflow
        for node_id, inputs in overrides.items():
            nid = str(node_id)
            if nid not in workflow:
                print(f"[ComfyUI] ⚠️  Override node '{nid}' not found in workflow — skipped.")
                continue
            workflow[nid]["inputs"].update(inputs)
            print(f"[ComfyUI]    Node [{nid}] inputs updated: {list(inputs.keys())}")
        return workflow

    def _post_prompt(self, workflow: dict) -> str:
        """POST the prompt and return the prompt_id."""
        payload = {
            "prompt":    workflow,
            "client_id": self.client_id
        }
        resp = requests.post(f"{self.api_url}/prompt", json=payload, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"[ComfyUI] Prompt POST failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"[ComfyUI] ComfyUI validation error: {result['error']}")
        prompt_id = result["prompt_id"]
        print(f"[ComfyUI] ✅ Prompt queued — ID: {prompt_id}")
        return prompt_id

    def _wait_for_completion(self, prompt_id: str, timeout: int = 900) -> None:
        """
        Open a WebSocket and block until ComfyUI signals execution complete.
        Falls back to HTTP polling if websocket-client is missing.

        timeout: Total seconds to wait. Default 900s covers CPU-mode SD 1.5.
                 GPU-mode: 120s is usually enough.
        """
        if websocket is None:
            print("[ComfyUI] ⚠️  websocket-client not installed — using HTTP polling.")
            self._poll_until_done(prompt_id, timeout)
            return

        ws_url = f"{self.ws_url}?clientId={self.client_id}"
        ws     = websocket.WebSocket()
        deadline = time.time() + timeout

        try:
            # Connect timeout = 30s (covers slow CPU startup)
            ws.connect(ws_url, timeout=30)
            ws.settimeout(120)   # Per-recv timeout: 120s (one SD step on CPU)
            print(f"[ComfyUI] WebSocket connected. Waiting up to {timeout}s "
                  f"(CPU-safe) for {prompt_id[:8]}...")

            last_step = 0
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # No message in 120s — check if still in queue
                    elapsed = int(time.time() - (deadline - timeout))
                    print(f"\r[ComfyUI] Still generating... {elapsed}s elapsed "
                          f"(CPU mode — patience!)   ", end="", flush=True)
                    continue

                if not raw:
                    continue
                if isinstance(raw, (bytes, bytearray)):
                    # ComfyUI can stream binary preview frames over the websocket.
                    # They are not JSON status messages and must be ignored here.
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type", "")
                data  = msg.get("data", {})

                if mtype == "progress":
                    step  = data.get("value", 0)
                    total = data.get("max", 1)
                    pct   = int(100 * step / total) if total else 0
                    bar   = "█" * int(20 * step / total) + "░" * (20 - int(20 * step / total))
                    elapsed = int(time.time() - (deadline - timeout))
                    if step != last_step:
                        print(f"\r  [{bar}] {pct:3d}%  step {step}/{total}  [{elapsed}s]",
                              end="", flush=True)
                        last_step = step

                elif mtype == "executing":
                    node = data.get("node")
                    pid  = data.get("prompt_id", "")
                    if node is None and pid == prompt_id:
                        self._wait_for_history_terminal(prompt_id, deadline)
                        print("\n[ComfyUI] ✅ Execution complete.")
                        return
                    elif node:
                        print(f"\r[ComfyUI] Running node [{node}]...   ",
                              end="", flush=True)

                elif mtype in ("execution_error", "execution_interrupted"):
                    self._wait_for_history_terminal(prompt_id, deadline, expected_failure=mtype)

            raise TimeoutError(
                f"[ComfyUI] ❌ Timed out after {timeout}s.\n"
                f"  → CPU mode: use --timeout 1800 for 30 min\n"
                f"  → Or reduce --frames and --resolution"
            )

        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _poll_until_done(self, prompt_id: str, timeout: int = 900) -> None:
        """Fallback: poll /history until the prompt appears (CPU-safe)."""
        deadline = time.time() + timeout
        print(f"[ComfyUI] Polling for completion (up to {timeout}s)...")
        self._wait_for_history_terminal(prompt_id, deadline)
        print(f"\n[ComfyUI] ✅ Poll: prompt done.")

    def _wait_for_history_terminal(self, prompt_id: str, deadline: float,
                                   expected_failure: str = None) -> None:
        """Poll /history until the prompt reaches a terminal success/failure state."""
        while time.time() < deadline:
            try:
                resp = requests.get(f"{self.api_url}/history/{prompt_id}", timeout=5)
                if resp.status_code == 200:
                    history = resp.json()
                    if prompt_id in history:
                        prompt_state = history[prompt_id]
                        status = prompt_state.get("status", {})
                        if status.get("completed"):
                            return

                        status_str = str(status.get("status_str", "")).lower()
                        messages = status.get("messages") or prompt_state.get("messages") or []
                        interrupted = expected_failure == "execution_interrupted"
                        errored = expected_failure == "execution_error"
                        if status_str in {"error", "failed", "interrupted"} or interrupted or errored:
                            raise RuntimeError(
                                f"[ComfyUI] ❌ Execution failed ({expected_failure or status_str or 'history'}): "
                                f"{status or prompt_state}"
                            )
            except RuntimeError:
                raise
            except Exception:
                pass

            elapsed = int(time.time() - (deadline - 1 if deadline <= time.time() else 0))
            print(f"\r  Waiting for history... {int(max(0, deadline - time.time()))}s left   ",
                  end="", flush=True)
            time.sleep(3)

        raise TimeoutError(
            f"[ComfyUI] Prompt {prompt_id[:8]} did not reach a terminal /history state in time\n"
            f"  → Increase timeout or reduce --frames / --resolution"
        )

    def _fetch_outputs(self, prompt_id: str) -> list:
        """Download all output files from history and return saved Paths."""
        resp = requests.get(f"{self.api_url}/history/{prompt_id}", timeout=10)
        resp.raise_for_status()
        history = resp.json()

        outputs = []
        node_outputs = history.get(prompt_id, {}).get("outputs", {})

        for node_id, node_out in node_outputs.items():
            for img in node_out.get("images", []):
                path = self._download_file(img, is_video=False)
                if path:
                    outputs.append(path)
            for vid in node_out.get("gifs", []):
                path = self._download_file(vid, is_video=True)
                if path:
                    outputs.append(path)
            for vid in node_out.get("video", []):
                # VHS_VideoCombine uses "video" key
                path = self._download_file(vid, is_video=True)
                if path:
                    outputs.append(path)

        if not outputs:
            print("[ComfyUI] ⚠️  No downloadable outputs found in history.")
        return outputs

    def _download_file(self, file_info: dict, is_video: bool = False) -> "Path | None":
        """Download a single output file from ComfyUI's /view endpoint."""
        fname   = file_info.get("filename", "")
        subfolder = file_info.get("subfolder", "")
        ftype   = file_info.get("type", "output")

        if not fname:
            return None

        url = (f"{self.api_url}/view"
               f"?filename={fname}&subfolder={subfolder}&type={ftype}")
        try:
            data = requests.get(url, timeout=60).content
        except Exception as e:
            print(f"[ComfyUI] ⚠️  Download failed for {fname}: {e}")
            return None

        dest_dir = self.vid_out if is_video else self.img_out
        dest = dest_dir / fname
        dest.write_bytes(data)
        print(f"[ComfyUI] 💾 Saved → {dest}")
        return dest

    def _execute(self, workflow_path: str, overrides: dict,
                 timeout: int = 900) -> list:
        """Full pipeline: load → override → queue → wait → download."""
        print(f"\n[ComfyUI] Loading workflow: {workflow_path}")
        workflow  = self._load_workflow(workflow_path)
        workflow  = self._apply_overrides(workflow, overrides or {})
        sampler_cfg = workflow.get("3", {}).get("inputs", {})
        if workflow_path.endswith("image_gen.json") and sampler_cfg:
            print("[ComfyUI] Image workflow sampler settings: "
                  f"steps={sampler_cfg.get('steps')} | "
                  f"cfg={sampler_cfg.get('cfg')} | "
                  f"sampler={sampler_cfg.get('sampler_name')} | "
                  f"scheduler={sampler_cfg.get('scheduler')}")
        prompt_id = self._post_prompt(workflow)
        self._wait_for_completion(prompt_id, timeout)
        outputs   = self._fetch_outputs(prompt_id)
        print(f"[ComfyUI] 🎉 Done — {len(outputs)} output(s) saved.")
        return outputs
