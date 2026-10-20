from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


STATUS_QUEUED = "QUEUED"
STATUS_PROCESSING = "PROCESSING"
STATUS_SUCCESS = "SUCCESS"
STATUS_PROVIDER_CRASH = "PROVIDER_CRASH"
STATUS_FAILED = "FAILED"


class ProviderExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        stderr_tail: list[str] | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.stderr_tail = stderr_tail or []
        self.exit_code = exit_code


def _tail_lines(text: str, limit: int = 5) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()][-limit:]


def _classify_error_type(text: str, *, exit_code: int | None = None, timed_out: bool = False) -> str:
    lowered = (text or "").lower()
    if timed_out:
        return "TIMEOUT"
    if "cuda out of memory" in lowered or "out of memory" in lowered or "cudnn_status_alloc_failed" in lowered:
        return "CUDA_OOM"
    if "not configured" in lowered or "does not exist" in lowered:
        return "REPO_MISSING"
    if "missing liveportrait checkpoints" in lowered or "pretrained_weights" in lowered:
        return "CHECKPOINT_MISSING"
    if "no mp4 output" in lowered:
        return "OUTPUT_MISSING"
    if exit_code is not None and exit_code != 0:
        return "SUBPROCESS_FAILED"
    return "UNKNOWN_ERROR"


class GPUMonitor:
    """Polls GPU memory during a provider run to capture a peak VRAM watermark."""

    def __init__(self, poll_interval_sec: float = 0.1) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.metrics: dict = {}
        self.peak_vram_mb: int | None = None

    def start(self) -> None:
        self.metrics = collect_gpu_metrics()
        self.peak_vram_mb = self.metrics.get("vram_peak_mb") or self.metrics.get("vram_used_mb")
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        latest = collect_gpu_metrics()
        latest_peak = latest.get("vram_peak_mb") or latest.get("vram_used_mb")
        if latest_peak is not None:
            if self.peak_vram_mb is None:
                self.peak_vram_mb = latest_peak
            else:
                self.peak_vram_mb = max(self.peak_vram_mb, latest_peak)
        self.metrics = latest
        if self.peak_vram_mb is not None:
            self.metrics["vram_peak_mb"] = self.peak_vram_mb
        return self.metrics

    def _poll(self) -> None:
        while not self._stop.is_set():
            sample = collect_gpu_metrics()
            used = sample.get("vram_used_mb")
            peak = sample.get("vram_peak_mb") or used
            if peak is not None:
                if self.peak_vram_mb is None:
                    self.peak_vram_mb = peak
                else:
                    self.peak_vram_mb = max(self.peak_vram_mb, peak)
            if sample.get("gpu_name"):
                self.metrics = sample
            self._stop.wait(self.poll_interval_sec)


@dataclass
class WorkerConfig:
    request_dir: Path
    response_dir: Path
    archive_dir: Path
    poll_interval_sec: int
    mock_source_mp4: str
    liveportrait_repo: Path | None
    driver_video: Path | None
    output_dir: Path
    lipsync_output_dir: Path
    aurora_log: Path
    use_mock: bool
    wav2lip_python_bin: str
    wav2lip_script: Path | None
    wav2lip_checkpoint: Path | None
    wav2lip_resize_factor: int
    wav2lip_pads: tuple[int, int, int, int]
    min_vram_mb: int
    recommended_vram_mb: int
    framepack_high_fidelity_vram_mb: int
    svdxt_model_id: str
    svdxt_width: int
    svdxt_height: int
    svdxt_num_frames: int
    svdxt_fps: int
    svdxt_motion_bucket_id: int
    svdxt_decode_chunk_size: int
    svdxt_noise_aug_strength: float
    svdxt_num_inference_steps: int
    framepack_repo: Path | None
    framepack_python_bin: str
    framepack_entry_script: str
    framepack_driver_video: Path | None
    framepack_output_dir: Path
    wan2gp_command: str
    wan2gp_output_dir: Path
    local_aurora_log_mirror: Path | None


class LivePortraitInferenceEngine:
    """Remote GPU inference wrapper for a notebook/Colab/Kaggle environment."""

    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.mode = "mock" if cfg.use_mock else "production"
        self.python_bin = os.getenv("LIVEPORTRAIT_PYTHON_BIN", "python")
        self.repo = cfg.liveportrait_repo
        self.driver_video = cfg.driver_video
        self.output_dir = cfg.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints = self._resolve_checkpoints()
        self._execution_mode = os.getenv("LIVEPORTRAIT_EXECUTION_MODE", "import").strip().lower()
        self._persistent_runner = None
        self._persistent_runner_name = ""
        self._persistent_runner_load_time_ms = 0
        self._runner_used = False
        self._appearance_cache: dict[str, dict] = {}
        self._active_anchor_key: str | None = None
        self.lipsync_output_dir = cfg.lipsync_output_dir
        self.lipsync_output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_checkpoints(self) -> dict[str, Path]:
        base = None
        if self.repo:
            base = self.repo / "pretrained_weights"
        if base is None:
            return {}
        return {
            "appearance": base / "base_models" / "appearance_feature_extractor.pth",
            "motion": base / "base_models" / "motion_extractor.pth",
            "spade": base / "base_models" / "spade_generator.pth",
        }

    def load_models(self) -> None:
        if self.mode == "mock":
            print("[RemoteWorker] Using mock LivePortrait mode.")
            return
        if not self.repo or not self.repo.exists():
            raise ProviderExecutionError(
                "LIVEPORTRAIT_REPO is not configured or does not exist.",
                error_type="REPO_MISSING",
            )
        if not self.driver_video or not self.driver_video.exists():
            raise ProviderExecutionError(
                "LIVEPORTRAIT_DRIVER_VIDEO is not configured or missing.",
                error_type="DRIVER_VIDEO_MISSING",
            )
        missing = [name for name, path in self.checkpoints.items() if not path.exists()]
        if missing:
            raise ProviderExecutionError(
                f"Missing LivePortrait checkpoints: {', '.join(missing)}. "
                "Download the official pretrained_weights bundle first.",
                error_type="CHECKPOINT_MISSING",
            )
        self._maybe_prepare_persistent_runner()
        print("[RemoteWorker] LivePortrait repo and checkpoints verified.")

    def infer(self, request: dict, response_path: Path, mock_source_mp4: str) -> tuple[Path, dict]:
        source_image = Path(request["source_image"])
        if not source_image.exists():
            raise FileNotFoundError(f"Source image not found: {source_image}")

        if self.mode == "mock":
            if not mock_source_mp4:
                raise RuntimeError(
                    "Mock mode requires LIVEPORTRAIT_MOCK_SOURCE_MP4 or --mock-source-mp4."
                )
            mock_path = Path(mock_source_mp4)
            if not mock_path.exists():
                raise FileNotFoundError(f"Mock source video not found: {mock_path}")
            shutil.copy2(mock_path, response_path)
            details = {"engine_mode": "mock", "mock": True}
            if request.get("audio_narration") or request.get("audio_narration_path"):
                details.update({
                    "lip_sync_status": "SKIPPED_MOCK",
                    "lip_sync_warning": "LIP_SYNC_SKIPPED_IN_MOCK",
                    "lip_sync_time_ms": 0,
                })
            return response_path, details

        output_dir = self.output_dir / request["request_id"]
        output_dir.mkdir(parents=True, exist_ok=True)
        motion_output_path = output_dir / "liveportrait_motion.mp4"
        reference_image = self._resolve_reference_image(request, source_image)
        anchor_key, anchor_info = self._prepare_anchor_reference(request, reference_image)
        env = os.environ.copy()
        acceleration = self._configure_acceleration(env)
        runner_loaded = self._persistent_runner is not None
        if not runner_loaded:
            self._maybe_prepare_persistent_runner()
        cold_start = (self._persistent_runner is not None and not self._runner_used) or self._persistent_runner is None
        monitor = GPUMonitor()
        started = time.time()
        monitor.start()
        try:
            if self._persistent_runner is not None:
                self._run_persistent_inference(source_image, output_dir, request, reference_image, anchor_info)
            else:
                self._run_cli_inference(source_image, output_dir, env, request, reference_image)
        finally:
            gpu_metrics = monitor.stop()
        if self._persistent_runner is not None:
            self._runner_used = True
        inference_time_ms = int((time.time() - started) * 1000)

        produced = self._locate_output_mp4(output_dir)
        if not produced:
            raise ProviderExecutionError(
                "LivePortrait inference completed but no mp4 output was found.",
                error_type="OUTPUT_MISSING",
            )
        if produced.resolve() != motion_output_path.resolve():
            shutil.copy2(produced, motion_output_path)
        final_output_path = response_path
        lip_sync_details = {
            "lip_sync_status": "NOT_REQUESTED",
            "lip_sync_time_ms": 0,
        }
        if request.get("audio_narration") or request.get("audio_narration_path"):
            lip_sync_details, final_output_path = self._run_lipsync_pass(
                request=request,
                motion_video_path=motion_output_path,
                response_path=response_path,
            )
        else:
            shutil.copy2(motion_output_path, response_path)
        return final_output_path, {
            "engine_mode": "production",
            "mock": False,
            "liveportrait_repo": str(self.repo),
            "driver_video": str(self.driver_video),
            "provider": "liveportrait_provider",
            "motion_output_path": str(motion_output_path),
            "reference_anchor_asset": request.get("reference_anchor_asset") or request.get("asset_key"),
            "reference_image_path": str(reference_image),
            "generation_seed": request.get("generation_seed"),
            "identity_lock": bool(request.get("identity_lock")),
            "identity_lock_enforced": bool(request.get("identity_lock") or anchor_info.get("cache_hit")),
            "appearance_cache_key": anchor_key,
            "appearance_cache_hit": anchor_info.get("cache_hit", False),
            "acceleration": acceleration,
            "cold_start": cold_start,
            "warm_start": self._persistent_runner is not None and not cold_start,
            "execution_mode": "persistent_import" if self._persistent_runner is not None else "subprocess_cli",
            "persistent_loaded": self._persistent_runner is not None,
            "model_load_time_ms": self._persistent_runner_load_time_ms if cold_start else 0,
            "load_time_ms": self._persistent_runner_load_time_ms if cold_start else 0,
            "latency_ms": inference_time_ms,
            "inference_time_ms": inference_time_ms,
            "total_inference_latency_ms": inference_time_ms + (self._persistent_runner_load_time_ms if cold_start else 0),
            "vram_peak_mb": gpu_metrics.get("vram_peak_mb"),
            "gpu_name": gpu_metrics.get("gpu_name"),
            "temperature_c": gpu_metrics.get("temperature_c"),
            "vram_used_mb": gpu_metrics.get("vram_used_mb"),
            "vram_total_mb": gpu_metrics.get("vram_total_mb"),
            **lip_sync_details,
        }

    def _locate_output_mp4(self, output_dir: Path) -> Path | None:
        candidates = sorted(output_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _configure_acceleration(self, env: dict) -> str:
        if os.getenv("LIVEPORTRAIT_ENABLE_FLASH_ATTN", "").lower() in {"1", "true", "yes"}:
            env["FLASH_ATTENTION"] = "1"
            return "flash_attention"
        if os.getenv("LIVEPORTRAIT_ENABLE_XFORMERS", "").lower() in {"1", "true", "yes"}:
            env["XFORMERS_FORCE_DISABLE_TRITON"] = "1"
            return "xformers"
        if os.getenv("LIVEPORTRAIT_ENABLE_TORCH_COMPILE", "").lower() in {"1", "true", "yes"}:
            env["LIVEPORTRAIT_FLAG_DO_TORCH_COMPILE"] = "1"
            return "torch_compile"
        return "none"

    def _maybe_prepare_persistent_runner(self) -> None:
        if self._persistent_runner is not None or self._execution_mode == "subprocess":
            return
        inference_script = self.repo / "inference.py"
        if not inference_script.exists():
            return
        import importlib.util
        import inspect
        import sys

        started = time.time()
        spec = importlib.util.spec_from_file_location("aurora_liveportrait_inference", inference_script)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(self.repo))
        try:
            spec.loader.exec_module(module)
        except Exception:
            return
        finally:
            try:
                sys.path.remove(str(self.repo))
            except ValueError:
                pass

        candidate_names = ("run_inference", "infer", "run")
        for name in candidate_names:
            fn = getattr(module, name, None)
            if callable(fn):
                self._persistent_runner = fn
                self._persistent_runner_name = name
                self._persistent_runner_load_time_ms = int((time.time() - started) * 1000)
                return

        main_fn = getattr(module, "main", None)
        if callable(main_fn):
            sig = inspect.signature(main_fn)
            if len(sig.parameters) <= 1:
                self._persistent_runner = main_fn
                self._persistent_runner_name = "main"
                self._persistent_runner_load_time_ms = int((time.time() - started) * 1000)

    def _run_persistent_inference(
        self,
        source_image: Path,
        output_dir: Path,
        request: dict,
        reference_image: Path,
        anchor_info: dict,
    ) -> None:
        import inspect
        import os as _os
        import sys

        runner = self._persistent_runner
        if runner is None:
            raise ProviderExecutionError("LivePortrait runner was not initialized.", error_type="RUNNER_MISSING")
        effective_source = reference_image if request.get("identity_lock") else source_image
        call_map = {
            "source_image": str(effective_source),
            "source": str(effective_source),
            "image_path": str(effective_source),
            "image": str(effective_source),
            "reference_image_path": str(reference_image),
            "reference_image": str(reference_image),
            "anchor_image_path": str(reference_image),
            "identity_lock": bool(request.get("identity_lock")),
            "generation_seed": request.get("generation_seed"),
            "seed": request.get("generation_seed"),
            "asset_key": request.get("asset_key"),
            "reference_anchor_asset": request.get("reference_anchor_asset") or request.get("asset_key"),
            "appearance_cache": anchor_info.get("appearance_features"),
            "appearance_feature_cache": anchor_info.get("appearance_features"),
            "driver_video": str(self.driver_video),
            "driving_video": str(self.driver_video),
            "driving": str(self.driver_video),
            "output_dir": str(output_dir),
            "output_path": str(output_dir),
        }
        signature = inspect.signature(runner)
        supports_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        kwargs = {
            name: value
            for name, value in call_map.items()
            if supports_kwargs or name in signature.parameters
        }

        cwd = _os.getcwd()
        argv_before = sys.argv[:]
        try:
            _os.chdir(self.repo)
            if self._persistent_runner_name == "main":
                sys.argv = [
                    "inference.py",
                    "-s",
                    str(effective_source),
                    "-d",
                    str(self.driver_video),
                    "--output-dir",
                    str(output_dir),
                ]
                try:
                    result = runner()
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                    if code not in {0, None}:
                        raise ProviderExecutionError(
                            f"LivePortrait main() exited with code {code}.",
                            error_type="SUBPROCESS_FAILED",
                            exit_code=code,
                        ) from exc
            else:
                try:
                    result = runner(**kwargs)
                except TypeError:
                    result = runner(str(effective_source), str(self.driver_video), str(output_dir))
                if isinstance(result, int) and result != 0:
                    raise ProviderExecutionError(
                        f"LivePortrait runner returned non-zero exit code {result}.",
                        error_type="SUBPROCESS_FAILED",
                        exit_code=result,
                    )
        except ProviderExecutionError:
            raise
        except Exception as exc:
            tb = traceback.format_exc()
            raise ProviderExecutionError(
                f"LivePortrait persistent import execution failed: {exc}",
                error_type=_classify_error_type(tb),
                stderr_tail=_tail_lines(tb),
            ) from exc
        finally:
            _os.chdir(cwd)
            sys.argv = argv_before

    def _run_cli_inference(
        self,
        source_image: Path,
        output_dir: Path,
        env: dict,
        request: dict,
        reference_image: Path,
    ) -> None:
        effective_source = reference_image if request.get("identity_lock") else source_image
        env["AURORA_REFERENCE_IMAGE_PATH"] = str(reference_image)
        env["AURORA_REFERENCE_ANCHOR_ASSET"] = str(request.get("reference_anchor_asset") or request.get("asset_key") or "")
        env["AURORA_IDENTITY_LOCK"] = "1" if request.get("identity_lock") else "0"
        if request.get("generation_seed") is not None:
            env["AURORA_GENERATION_SEED"] = str(request.get("generation_seed"))
        cmd = [
            self.python_bin,
            "inference.py",
            "-s",
            str(effective_source),
            "-d",
            str(self.driver_video),
            "--output-dir",
            str(output_dir),
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=int(os.getenv("LIVEPORTRAIT_INFER_TIMEOUT_SEC", "600")),
            )
        except subprocess.TimeoutExpired as exc:
            combined = ((exc.stderr or "") + "\n" + (exc.stdout or "")).strip()
            raise ProviderExecutionError(
                "LivePortrait inference timed out.",
                error_type="TIMEOUT",
                stderr_tail=_tail_lines(combined),
                exit_code=-1,
            ) from exc
        if result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            raise ProviderExecutionError(
                "LivePortrait inference failed.",
                error_type=_classify_error_type(stderr, exit_code=result.returncode),
                stderr_tail=_tail_lines(stderr),
                exit_code=result.returncode,
            )

    def _resolve_reference_image(self, request: dict, default_image: Path) -> Path:
        reference = request.get("reference_image_path")
        if reference:
            candidate = Path(reference)
            if candidate.exists():
                return candidate
        return default_image

    def _prepare_anchor_reference(self, request: dict, reference_image: Path) -> tuple[str, dict]:
        anchor_asset = request.get("reference_anchor_asset") or request.get("asset_key") or reference_image.stem
        anchor_key = f"{anchor_asset}:{reference_image.resolve()}"
        if anchor_key in self._appearance_cache:
            self._active_anchor_key = anchor_key
            cached = dict(self._appearance_cache[anchor_key])
            cached["cache_hit"] = True
            return anchor_key, cached

        anchor_info = {
            "cache_hit": False,
            "reference_image_path": str(reference_image),
            "prepared_at": datetime.now().isoformat(timespec="seconds"),
            "appearance_features": None,
        }
        features = self._try_extract_appearance_features(reference_image)
        if features is not None:
            anchor_info["appearance_features"] = features
        self._appearance_cache[anchor_key] = anchor_info
        self._active_anchor_key = anchor_key
        return anchor_key, dict(anchor_info)

    def _try_extract_appearance_features(self, reference_image: Path):
        if self._persistent_runner is None:
            return None
        for attr_name in ("extract_appearance_features", "preload_appearance", "cache_appearance"):
            helper = getattr(self._persistent_runner, attr_name, None)
            if callable(helper):
                try:
                    return helper(str(reference_image))
                except Exception:
                    return None
        return None

    def _run_lipsync_pass(
        self,
        request: dict,
        motion_video_path: Path,
        response_path: Path,
    ) -> tuple[dict, Path]:
        audio_source = self._stage_shared_audio(request, motion_video_path.parent)
        output_path = self.lipsync_output_dir / f"{request['request_id']}_lipsync.mp4"
        monitor = GPUMonitor()
        started = time.time()
        monitor.start()
        try:
            self._run_wav2lip_cli(motion_video_path, audio_source, output_path)
        except ProviderExecutionError as exc:
            gpu_metrics = monitor.stop()
            shutil.copy2(motion_video_path, response_path)
            return {
                "lip_sync_status": "FAILED",
                "lip_sync_warning": "LIP_SYNC_FAILED",
                "lip_sync_error": str(exc),
                "lip_sync_error_type": exc.error_type,
                "lip_sync_stderr_tail": exc.stderr_tail,
                "lip_sync_exit_code": exc.exit_code,
                "lip_sync_time_ms": int((time.time() - started) * 1000),
                "lip_sync_vram_peak_mb": gpu_metrics.get("vram_peak_mb"),
                "lip_sync_audio_path": str(audio_source),
            }, response_path
        gpu_metrics = monitor.stop()
        shutil.copy2(output_path, response_path)
        return {
            "lip_sync_status": "SUCCESS",
            "lip_sync_warning": "",
            "lip_sync_time_ms": int((time.time() - started) * 1000),
            "lip_sync_vram_peak_mb": gpu_metrics.get("vram_peak_mb"),
            "lip_sync_audio_path": str(audio_source),
            "lip_sync_output_path": str(output_path),
        }, response_path

    def _stage_shared_audio(self, request: dict, workspace: Path) -> Path:
        audio_payload = request.get("audio_narration") or {}
        candidates: list[Path] = []
        if request.get("audio_narration_path"):
            candidates.append(Path(request["audio_narration_path"]))
        if isinstance(audio_payload, dict):
            absolute = audio_payload.get("absolute_path")
            if absolute:
                candidates.append(Path(absolute))
            relative = audio_payload.get("relative_to_shared_root")
            shared_root = os.getenv("AURORA_SHARED_ROOT", "").strip()
            if relative and shared_root:
                candidates.append(Path(shared_root) / relative)
        source = next((candidate for candidate in candidates if candidate.exists()), None)
        if source is None:
            raise ProviderExecutionError(
                "Narration audio could not be resolved from shared storage.",
                error_type="AUDIO_MISSING",
            )
        staged = workspace / f"narration{source.suffix.lower() or '.wav'}"
        if source.resolve() != staged.resolve():
            shutil.copy2(source, staged)
        return staged

    def _run_wav2lip_cli(self, video_path: Path, audio_path: Path, output_path: Path) -> None:
        if not self.cfg.wav2lip_script or not self.cfg.wav2lip_script.exists():
            raise ProviderExecutionError(
                "WAV2LIP_SCRIPT is not configured or missing on the GPU host.",
                error_type="REPO_MISSING",
            )
        if not self.cfg.wav2lip_checkpoint or not self.cfg.wav2lip_checkpoint.exists():
            raise ProviderExecutionError(
                "WAV2LIP_CHECKPOINT is not configured or missing on the GPU host.",
                error_type="CHECKPOINT_MISSING",
            )
        cmd = [
            self.cfg.wav2lip_python_bin,
            str(self.cfg.wav2lip_script),
            "--checkpoint_path",
            str(self.cfg.wav2lip_checkpoint),
            "--face",
            str(video_path),
            "--audio",
            str(audio_path),
            "--outfile",
            str(output_path),
            "--resize_factor",
            str(self.cfg.wav2lip_resize_factor),
            "--pads",
            str(self.cfg.wav2lip_pads[0]),
            str(self.cfg.wav2lip_pads[1]),
            str(self.cfg.wav2lip_pads[2]),
            str(self.cfg.wav2lip_pads[3]),
            "--nosmooth",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=int(os.getenv("WAV2LIP_TIMEOUT_SEC", "900")),
            )
        except subprocess.TimeoutExpired as exc:
            combined = ((exc.stderr or "") + "\n" + (exc.stdout or "")).strip()
            raise ProviderExecutionError(
                "Wav2Lip inference timed out.",
                error_type="TIMEOUT",
                stderr_tail=_tail_lines(combined),
                exit_code=-1,
            ) from exc
        if result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            raise ProviderExecutionError(
                "Wav2Lip inference failed.",
                error_type=_classify_error_type(stderr, exit_code=result.returncode),
                stderr_tail=_tail_lines(stderr),
                exit_code=result.returncode,
            )
        if not output_path.exists():
            raise ProviderExecutionError(
                "Wav2Lip completed but no mp4 output was found.",
                error_type="OUTPUT_MISSING",
            )


class RemoteI2VInferenceEngine:
    """Concrete remote backends for FramePack / SVD-XT / Wan2GP."""

    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.mode = "mock" if cfg.use_mock else "production"
        self._svdxt_pipe = None
        self._svdxt_torch = None
        self._svdxt_load_time_ms = 0
        self._loaded_provider: str | None = None

    def release_models(self) -> None:
        released = False
        if self._svdxt_pipe is not None:
            self._svdxt_pipe = None
            self._svdxt_torch = None
            self._svdxt_load_time_ms = 0
            released = True
        if released:
            try:
                import gc
                import torch  # type: ignore

                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
            except Exception:
                pass
        self._loaded_provider = None

    def infer(self, request: dict, response_path: Path, mock_source_mp4: str) -> tuple[Path, dict]:
        requested_provider = request.get("provider", "svdxt_provider")
        metrics = collect_gpu_metrics()
        vram_total = int(metrics.get("vram_total_mb") or 0)
        selected = self._select_provider(
            requested_provider=requested_provider,
            visual_complexity=request.get("visual_complexity", "standard"),
            vram_total_mb=vram_total,
        )

        if vram_total and vram_total < self.cfg.min_vram_mb:
            raise MemoryError(
                f"Remote GPU reports only {vram_total} MB VRAM; "
                f"minimum required is {self.cfg.min_vram_mb} MB."
            )

        if self._loaded_provider and self._loaded_provider != selected:
            self.release_models()

        if self.mode == "mock":
            if not mock_source_mp4:
                raise RuntimeError("Mock I2V mode requires a source mp4.")
            mock_path = Path(mock_source_mp4)
            if not mock_path.exists():
                raise FileNotFoundError(f"Mock source video not found: {mock_path}")
            shutil.copy2(mock_path, response_path)
            return response_path, {
                "engine_mode": "mock",
                "mock": True,
                "provider": selected,
                "visual_complexity": request.get("visual_complexity", "standard"),
                "latency_ms": 0,
                "inference_time_ms": 0,
                "total_inference_latency_ms": 0,
                "it_per_sec": 0,
                "avg_iteration_speed_its": 0,
            }

        if selected == "svdxt_provider":
            return self._infer_svdxt(request, response_path, vram_total)
        if selected == "framepack_provider":
            return self._infer_framepack(request, response_path)
        if selected == "wan2gp_provider":
            return self._infer_wan2gp(request, response_path)
        raise RuntimeError(f"Unsupported I2V provider: {selected}")

    def _select_provider(self, requested_provider: str, visual_complexity: str, vram_total_mb: int) -> str:
        if vram_total_mb and vram_total_mb < self.cfg.recommended_vram_mb:
            return "svdxt_provider"
        if requested_provider == "framepack_provider" and vram_total_mb and vram_total_mb < self.cfg.framepack_high_fidelity_vram_mb:
            return "svdxt_provider"
        if requested_provider in {"framepack_provider", "svdxt_provider", "wan2gp_provider"}:
            return requested_provider
        if visual_complexity == "high action":
            return "framepack_provider"
        if visual_complexity == "atmospheric":
            return "svdxt_provider"
        return "wan2gp_provider"

    def _infer_svdxt(self, request: dict, response_path: Path, vram_total_mb: int) -> tuple[Path, dict]:
        import torch  # type: ignore
        from diffusers import StableVideoDiffusionPipeline  # type: ignore
        from diffusers.utils import export_to_video  # type: ignore
        from PIL import Image, ImageOps  # type: ignore

        force_fp16 = os.getenv("FORCE_FP16", "1").lower() in {"1", "true", "yes"}
        use_fp16 = force_fp16 or bool(vram_total_mb and vram_total_mb < self.cfg.recommended_vram_mb)
        dtype = torch.float16 if use_fp16 else torch.float32
        load_time_ms = 0
        cold_start = self._svdxt_pipe is None or self._svdxt_torch != dtype
        if cold_start:
            load_started = time.time()
            load_kwargs = {"torch_dtype": dtype}
            if use_fp16:
                load_kwargs["variant"] = "fp16"
            self._svdxt_pipe = StableVideoDiffusionPipeline.from_pretrained(
                self.cfg.svdxt_model_id,
                **load_kwargs,
            )
            if (
                os.getenv("ENABLE_XFORMERS", "1").lower() in {"1", "true", "yes"}
                or os.getenv("SVDXT_ENABLE_XFORMERS", "").lower() in {"1", "true", "yes"}
            ):
                try:
                    self._svdxt_pipe.enable_xformers_memory_efficient_attention()
                except Exception:
                    pass
            if torch.cuda.is_available():
                if use_fp16:
                    self._svdxt_pipe.enable_model_cpu_offload()
                else:
                    self._svdxt_pipe.to("cuda")
                    if os.getenv("SVDXT_ENABLE_TORCH_COMPILE", "").lower() in {"1", "true", "yes"}:
                        try:
                            self._svdxt_pipe.unet = torch.compile(
                                self._svdxt_pipe.unet,
                                mode="reduce-overhead",
                                fullgraph=True,
                            )
                        except Exception:
                            pass
            self._svdxt_torch = dtype
            load_time_ms = int((time.time() - load_started) * 1000)
            self._svdxt_load_time_ms = load_time_ms
            self._loaded_provider = "svdxt_provider"

        source_image = Path(request["source_image"])
        image = Image.open(source_image).convert("RGB")
        target_size = (self.cfg.svdxt_width, self.cfg.svdxt_height)
        image = ImageOps.fit(image, target_size, method=Image.Resampling.LANCZOS)
        started = time.time()
        generator = torch.manual_seed(int(request.get("seed") or 42))
        try:
            output = self._svdxt_pipe(
                image=image,
                decode_chunk_size=self.cfg.svdxt_decode_chunk_size,
                generator=generator,
                motion_bucket_id=self.cfg.svdxt_motion_bucket_id,
                noise_aug_strength=self.cfg.svdxt_noise_aug_strength,
                num_frames=self.cfg.svdxt_num_frames,
                num_inference_steps=self.cfg.svdxt_num_inference_steps,
            )
            frames = output.frames[0]
            export_to_video(frames, str(response_path), fps=self.cfg.svdxt_fps)
        except Exception as exc:
            tb = traceback.format_exc()
            raise ProviderExecutionError(
                f"SVD-XT inference failed: {exc}",
                error_type=_classify_error_type(tb),
                stderr_tail=_tail_lines(tb),
            ) from exc
        elapsed_ms = int((time.time() - started) * 1000)
        it_per_sec = round(self.cfg.svdxt_num_inference_steps / max(elapsed_ms / 1000, 0.001), 3)
        return response_path, {
            "engine_mode": "production",
            "mock": False,
            "provider": "svdxt_provider",
            "cold_start": cold_start,
            "warm_start": not cold_start,
            "model_id": self.cfg.svdxt_model_id,
            "precision": "fp16" if use_fp16 else "fp32",
            "latency_ms": elapsed_ms,
            "load_time_ms": load_time_ms,
            "inference_time_ms": elapsed_ms,
            "total_inference_latency_ms": elapsed_ms + load_time_ms,
            "it_per_sec": it_per_sec,
            "avg_iteration_speed_its": it_per_sec,
            "num_frames": self.cfg.svdxt_num_frames,
            "fps": self.cfg.svdxt_fps,
            "motion_bucket_id": self.cfg.svdxt_motion_bucket_id,
            "width": self.cfg.svdxt_width,
            "height": self.cfg.svdxt_height,
        }

    def _infer_framepack(self, request: dict, response_path: Path) -> tuple[Path, dict]:
        if not self.cfg.framepack_repo or not self.cfg.framepack_repo.exists():
            raise RuntimeError("FRAMEPACK_REPO is not configured on the GPU host.")
        self._loaded_provider = "framepack_provider"
        entrypoint = self.cfg.framepack_repo / self.cfg.framepack_entry_script
        if not entrypoint.exists():
            raise RuntimeError(f"FramePack entry script not found: {entrypoint}")

        motion_prompt = request.get("motion_prompt") or self._default_motion_prompt(request)
        cmd = [
            self.cfg.framepack_python_bin,
            str(entrypoint),
            "--image",
            request["source_image"],
            "--prompt",
            motion_prompt,
            "--output",
            str(response_path),
            "--seconds",
            str(request.get("duration_sec", 5)),
        ]
        if self.cfg.framepack_driver_video and self.cfg.framepack_driver_video.exists():
            cmd.extend(["--driver-video", str(self.cfg.framepack_driver_video)])
        if os.getenv("FRAMEPACK_ENABLE_XFORMERS", "").lower() in {"1", "true", "yes"}:
            cmd.append("--enable-xformers")
        if os.getenv("FRAMEPACK_ENABLE_FLASH_ATTN", "").lower() in {"1", "true", "yes"}:
            cmd.append("--enable-flash-attn")
        if collect_gpu_metrics().get("vram_total_mb") and int(collect_gpu_metrics()["vram_total_mb"]) >= self.cfg.framepack_high_fidelity_vram_mb:
            cmd.append("--high-fidelity")
        return self._run_provider_subprocess(
            cmd=cmd,
            cwd=self.cfg.framepack_repo,
            response_path=response_path,
            provider_name="framepack_provider",
            extra={
                "motion_prompt": motion_prompt,
                "entry_script": str(entrypoint),
                "load_time_ms": 0,
                "cold_start": False,
                "warm_start": True,
            },
        )

    def _infer_wan2gp(self, request: dict, response_path: Path) -> tuple[Path, dict]:
        command = self.cfg.wan2gp_command.strip()
        if not command:
            raise RuntimeError(
                "WAN2GP_COMMAND is not configured on the GPU host. "
                "Set it to a runnable inference command template."
            )
        self._loaded_provider = "wan2gp_provider"
        motion_prompt = request.get("motion_prompt") or self._default_motion_prompt(request)
        format_map = {
            "source_image": request["source_image"],
            "prompt": motion_prompt,
            "output": str(response_path),
            "duration_sec": str(request.get("duration_sec", 5)),
        }
        formatted = command.format(**format_map)
        cmd = shlex.split(formatted)
        return self._run_provider_subprocess(
            cmd=cmd,
            cwd=None,
            response_path=response_path,
            provider_name="wan2gp_provider",
            extra={
                "motion_prompt": motion_prompt,
                "command_template": "WAN2GP_COMMAND",
                "load_time_ms": 0,
                "cold_start": False,
                "warm_start": True,
            },
        )

    def _run_provider_subprocess(
        self,
        cmd: list[str],
        cwd: Path | None,
        response_path: Path,
        provider_name: str,
        extra: dict,
    ) -> tuple[Path, dict]:
        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=int(os.getenv("AURORA_I2V_TIMEOUT_SEC", "1800")),
            )
        except subprocess.TimeoutExpired as exc:
            combined = ((exc.stderr or "") + "\n" + (exc.stdout or "")).strip()
            raise ProviderExecutionError(
                f"{provider_name} inference timed out.",
                error_type="TIMEOUT",
                stderr_tail=_tail_lines(combined),
                exit_code=-1,
            ) from exc
        elapsed_ms = int((time.time() - started) * 1000)
        if result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            raise ProviderExecutionError(
                f"{provider_name} inference failed.",
                error_type=_classify_error_type(stderr, exit_code=result.returncode),
                stderr_tail=_tail_lines(stderr),
                exit_code=result.returncode,
            )
        if not response_path.exists():
            candidates = sorted(
                response_path.parent.rglob("*.mp4"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                shutil.copy2(candidates[0], response_path)
        if not response_path.exists():
            raise ProviderExecutionError(
                f"{provider_name} completed but no mp4 output was found.",
                error_type="OUTPUT_MISSING",
            )
        it_per_sec = round(1 / max(elapsed_ms / 1000, 0.001), 3)
        return response_path, {
            "engine_mode": "production",
            "mock": False,
            "provider": provider_name,
            "latency_ms": elapsed_ms,
            "inference_time_ms": elapsed_ms,
            "total_inference_latency_ms": elapsed_ms,
            "it_per_sec": it_per_sec,
            "avg_iteration_speed_its": it_per_sec,
            **extra,
        }

    def _default_motion_prompt(self, request: dict) -> str:
        complexity = request.get("visual_complexity", "standard")
        if complexity == "high action":
            return "Dynamic anime action, strong body motion, hair swaying, energy effects."
        if complexity == "atmospheric":
            return "Subtle cinematic motion, wind, lighting shifts, cloth movement."
        return "Natural cinematic motion, gentle body movement, stable anime-style animation."


def parse_args() -> WorkerConfig:
    parser = argparse.ArgumentParser(description="Remote GPU worker for Aurora motion jobs.")
    shared_root = os.getenv("AURORA_SHARED_ROOT", "").strip()
    default_request_dir = (
        Path(shared_root) / "outputs" / "liveportrait_requests"
        if shared_root else Path(os.getenv("LIVEPORTRAIT_REQUEST_DIR", "outputs/liveportrait_requests"))
    )
    default_response_dir = (
        Path(shared_root) / "outputs" / "liveportrait_responses"
        if shared_root else Path(os.getenv("LIVEPORTRAIT_RESPONSE_DIR", "outputs/liveportrait_responses"))
    )
    default_archive_dir = (
        Path(shared_root) / "outputs" / "liveportrait_archived_requests"
        if shared_root else Path(os.getenv("LIVEPORTRAIT_ARCHIVE_DIR", "outputs/liveportrait_archived_requests"))
    )
    default_aurora_log = (
        Path(shared_root) / "aurora.jsonl"
        if shared_root else Path(os.getenv("AURORA_JSONL_PATH", "aurora.jsonl"))
    )
    parser.add_argument(
        "--request-dir",
        default=os.getenv("AURORA_REQUEST_DIR", str(default_request_dir)),
    )
    parser.add_argument(
        "--response-dir",
        default=os.getenv("AURORA_RESPONSE_DIR", str(default_response_dir)),
    )
    parser.add_argument(
        "--archive-dir",
        default=os.getenv("AURORA_ARCHIVE_DIR", str(default_archive_dir)),
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=int(os.getenv("AURORA_POLL_INTERVAL_SEC", os.getenv("LIVEPORTRAIT_POLL_INTERVAL_SEC", "3"))),
    )
    parser.add_argument(
        "--mock-source-mp4",
        default=os.getenv("LIVEPORTRAIT_MOCK_SOURCE_MP4", ""),
    )
    parser.add_argument(
        "--liveportrait-repo",
        default=os.getenv("LIVEPORTRAIT_REPO", ""),
    )
    parser.add_argument(
        "--driver-video",
        default=os.getenv("LIVEPORTRAIT_DRIVER_VIDEO", ""),
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("LIVEPORTRAIT_OUTPUT_DIR", "outputs/liveportrait_worker_runs"),
    )
    parser.add_argument(
        "--lipsync-output-dir",
        default=os.getenv("WAV2LIP_OUTPUT_DIR", "outputs/wav2lip_worker_runs"),
    )
    parser.add_argument(
        "--aurora-log",
        default=os.getenv("AURORA_JSONL_PATH", str(default_aurora_log)),
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force mock mode even if GPU provider env vars are present.",
    )
    args = parser.parse_args()
    return WorkerConfig(
        request_dir=Path(args.request_dir),
        response_dir=Path(args.response_dir),
        archive_dir=Path(args.archive_dir),
        poll_interval_sec=args.poll_interval_sec,
        mock_source_mp4=args.mock_source_mp4,
        liveportrait_repo=Path(args.liveportrait_repo) if args.liveportrait_repo else None,
        driver_video=Path(args.driver_video) if args.driver_video else None,
        output_dir=Path(args.output_dir),
        lipsync_output_dir=Path(args.lipsync_output_dir),
        aurora_log=Path(args.aurora_log),
        use_mock=args.mock or os.getenv("LIVEPORTRAIT_WORKER_MODE", "").lower() == "mock",
        wav2lip_python_bin=os.getenv("WAV2LIP_PYTHON_BIN", "python"),
        wav2lip_script=Path(os.getenv("WAV2LIP_SCRIPT", "")) if os.getenv("WAV2LIP_SCRIPT") else None,
        wav2lip_checkpoint=Path(os.getenv("WAV2LIP_CHECKPOINT", "")) if os.getenv("WAV2LIP_CHECKPOINT") else None,
        wav2lip_resize_factor=int(os.getenv("WAV2LIP_RESIZE_FACTOR", "1")),
        wav2lip_pads=(
            int(os.getenv("WAV2LIP_PAD_TOP", "0")),
            int(os.getenv("WAV2LIP_PAD_BOTTOM", "10")),
            int(os.getenv("WAV2LIP_PAD_LEFT", "0")),
            int(os.getenv("WAV2LIP_PAD_RIGHT", "0")),
        ),
        min_vram_mb=int(os.getenv("AURORA_MIN_VRAM_MB", "8192")),
        recommended_vram_mb=int(os.getenv("AURORA_RECOMMENDED_VRAM_MB", "12288")),
        framepack_high_fidelity_vram_mb=int(os.getenv("FRAMEPACK_HIGH_FIDELITY_VRAM_MB", "24576")),
        svdxt_model_id=os.getenv("SVDXT_MODEL_ID", "stabilityai/stable-video-diffusion-img2vid-xt"),
        svdxt_width=int(os.getenv("SVDXT_WIDTH", "576")),
        svdxt_height=int(os.getenv("SVDXT_HEIGHT", "1024")),
        svdxt_num_frames=int(os.getenv("SVDXT_NUM_FRAMES", "25")),
        svdxt_fps=int(os.getenv("SVDXT_FPS", "5")),
        svdxt_motion_bucket_id=int(os.getenv("SVDXT_MOTION_BUCKET_ID", "127")),
        svdxt_decode_chunk_size=int(os.getenv("SVDXT_DECODE_CHUNK_SIZE", "4")),
        svdxt_noise_aug_strength=float(os.getenv("SVDXT_NOISE_AUG_STRENGTH", "0.08")),
        svdxt_num_inference_steps=int(os.getenv("SVDXT_NUM_INFERENCE_STEPS", "25")),
        framepack_repo=Path(os.getenv("FRAMEPACK_REPO", "")) if os.getenv("FRAMEPACK_REPO") else None,
        framepack_python_bin=os.getenv("FRAMEPACK_PYTHON_BIN", "python"),
        framepack_entry_script=os.getenv("FRAMEPACK_ENTRY_SCRIPT", "inference.py"),
        framepack_driver_video=Path(os.getenv("FRAMEPACK_DRIVER_VIDEO", "")) if os.getenv("FRAMEPACK_DRIVER_VIDEO") else None,
        framepack_output_dir=Path(os.getenv("FRAMEPACK_OUTPUT_DIR", "outputs/framepack_worker_runs")),
        wan2gp_command=os.getenv("WAN2GP_COMMAND", ""),
        wan2gp_output_dir=Path(os.getenv("WAN2GP_OUTPUT_DIR", "outputs/wan2gp_worker_runs")),
        local_aurora_log_mirror=Path(os.getenv("AURORA_LOCAL_JSONL_PATH")) if os.getenv("AURORA_LOCAL_JSONL_PATH") else None,
    )


def load_request(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def archive_request(path: Path, archive_dir: Path, suffix: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"{path.stem}_{suffix}{path.suffix}"
    if not path.exists():
        return archived
    shutil.move(str(path), str(archived))
    return archived


def append_aurora_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def append_mirror_log(path: Path | None, payload: dict) -> None:
    if not path:
        return
    append_aurora_log(path, payload)


def collect_gpu_metrics() -> dict:
    metrics = {
        "gpu_name": None,
        "temperature_c": None,
        "vram_used_mb": None,
        "vram_total_mb": None,
        "vram_peak_mb": None,
    }
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        metrics["gpu_name"] = pynvml.nvmlDeviceGetName(handle)
        metrics["temperature_c"] = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        metrics["vram_used_mb"] = int(mem.used / (1024 * 1024))
        metrics["vram_total_mb"] = int(mem.total / (1024 * 1024))
        metrics["vram_peak_mb"] = metrics["vram_used_mb"]
    except Exception:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                metrics["gpu_name"] = props.name
                metrics["vram_used_mb"] = int(torch.cuda.memory_allocated(0) / (1024 * 1024))
                metrics["vram_total_mb"] = int(props.total_memory / (1024 * 1024))
                metrics["vram_peak_mb"] = int(torch.cuda.max_memory_allocated(0) / (1024 * 1024))
        except Exception:
            pass
    return metrics


def merge_metrics(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if value is not None:
            merged[key] = value
    if merged.get("vram_peak_mb") is None:
        merged["vram_peak_mb"] = merged.get("vram_used_mb")
    return merged


def write_response_status(response_json: Path, payload: dict) -> None:
    response_json.parent.mkdir(parents=True, exist_ok=True)
    response_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def near_oom(metrics: dict, threshold: float = 0.92) -> bool:
    used = metrics.get("vram_used_mb")
    total = metrics.get("vram_total_mb")
    if not used or not total:
        return False
    return (float(used) / float(total)) >= threshold


def _generic_failure_details(exc: Exception) -> tuple[str, list[str], int | None]:
    if isinstance(exc, ProviderExecutionError):
        return exc.error_type, exc.stderr_tail, exc.exit_code
    if isinstance(exc, MemoryError):
        return "CUDA_OOM", _tail_lines(str(exc)), None
    tb = traceback.format_exc()
    return type(exc).__name__.upper(), _tail_lines(tb), None


def run_worker(cfg: WorkerConfig) -> int:
    cfg.request_dir.mkdir(parents=True, exist_ok=True)
    cfg.response_dir.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)

    liveportrait_engine = LivePortraitInferenceEngine(cfg)
    liveportrait_engine.load_models()
    i2v_engine = RemoteI2VInferenceEngine(cfg)
    print(f"[RemoteWorker] Watching {cfg.request_dir}")

    while True:
        requests = sorted(cfg.request_dir.glob("*.json"))
        if not requests:
            time.sleep(cfg.poll_interval_sec)
            continue

        for request_path in requests:
            request = load_request(request_path)
            request_id = request["request_id"]
            response_path = Path(request["expected_output"])
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_json = response_path.with_suffix(".json")

            heartbeat = {
                "request_id": request_id,
                "status": STATUS_PROCESSING,
                "motion_status": "waiting_remote_gpu",
                "mock": cfg.use_mock,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                **collect_gpu_metrics(),
            }
            write_response_status(response_json, heartbeat)
            append_aurora_log(cfg.aurora_log, heartbeat)
            append_mirror_log(cfg.local_aurora_log_mirror, heartbeat)

            try:
                print(f"[RemoteWorker] Processing {request_path.name}")
                motion_mode = request.get("motion_mode", "talking_motion")
                pre_metrics = collect_gpu_metrics()
                if motion_mode == "full_i2v_motion":
                    output_path, details = i2v_engine.infer(request, response_path, cfg.mock_source_mp4)
                else:
                    output_path, details = liveportrait_engine.infer(request, response_path, cfg.mock_source_mp4)
                post_metrics = collect_gpu_metrics()
                success_payload = {
                    "request_id": request_id,
                    "status": STATUS_SUCCESS,
                    "motion_status": "remote_gpu_complete",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "response_path": str(output_path),
                    "reference_anchor_asset": request.get("reference_anchor_asset") or request.get("asset_key"),
                    "reference_image_path": request.get("reference_image_path"),
                    "generation_seed": request.get("generation_seed"),
                    "identity_lock": bool(request.get("identity_lock")),
                    **merge_metrics(pre_metrics, post_metrics),
                    **details,
                }
                write_response_status(response_json, success_payload)
                append_aurora_log(cfg.aurora_log, success_payload)
                append_mirror_log(cfg.local_aurora_log_mirror, success_payload)
                archive_request(request_path, cfg.archive_dir, "processed")
                if near_oom(post_metrics):
                    i2v_engine.release_models()
                print(f"[RemoteWorker] Response written -> {response_path}")
            except (ProviderExecutionError, MemoryError) as exc:
                error_type, stderr_tail, exit_code = _generic_failure_details(exc)
                status = STATUS_PROVIDER_CRASH if error_type == "CUDA_OOM" else STATUS_FAILED
                crash_payload = {
                    "request_id": request_id,
                    "status": status,
                    "motion_status": "provider_crash" if status == STATUS_PROVIDER_CRASH else "remote_gpu_failed",
                    "mock": cfg.use_mock,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "reference_anchor_asset": request.get("reference_anchor_asset") or request.get("asset_key"),
                    "reference_image_path": request.get("reference_image_path"),
                    "generation_seed": request.get("generation_seed"),
                    "identity_lock": bool(request.get("identity_lock")),
                    "error": str(exc),
                    "error_type": error_type,
                    "stderr_tail": stderr_tail,
                    "exit_code": exit_code,
                    **collect_gpu_metrics(),
                }
                write_response_status(response_json, crash_payload)
                append_aurora_log(cfg.aurora_log, crash_payload)
                append_mirror_log(cfg.local_aurora_log_mirror, crash_payload)
                archive_request(request_path, cfg.archive_dir, "provider_crash" if status == STATUS_PROVIDER_CRASH else "failed")
                print(f"[RemoteWorker] {status} {request_path.name}: {exc}")
            except Exception as exc:
                error_type, stderr_tail, exit_code = _generic_failure_details(exc)
                failed_payload = {
                    "request_id": request_id,
                    "status": STATUS_FAILED,
                    "motion_status": "remote_gpu_failed",
                    "mock": cfg.use_mock,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "reference_anchor_asset": request.get("reference_anchor_asset") or request.get("asset_key"),
                    "reference_image_path": request.get("reference_image_path"),
                    "generation_seed": request.get("generation_seed"),
                    "identity_lock": bool(request.get("identity_lock")),
                    "error": str(exc),
                    "error_type": error_type,
                    "stderr_tail": stderr_tail,
                    "exit_code": exit_code,
                    **collect_gpu_metrics(),
                }
                write_response_status(response_json, failed_payload)
                append_aurora_log(cfg.aurora_log, failed_payload)
                append_mirror_log(cfg.local_aurora_log_mirror, failed_payload)
                archive_request(request_path, cfg.archive_dir, "failed")
                print(f"[RemoteWorker] ERROR {request_path.name}: {exc}")
            finally:
                try:
                    import torch  # type: ignore

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass


def main() -> int:
    cfg = parse_args()
    run_worker(cfg)
    return 0


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    raise SystemExit(main())
