#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(pwd)}"
GPU_ENV_DIR="${GPU_ENV_DIR:-$ROOT_DIR/gpu_env}"
MODEL_ROOT="${MODEL_ROOT:-/tmp/models}"
SHARED_ROOT="${SHARED_ROOT:-$ROOT_DIR/shared_mount}"
FRAMEPACK_REPO_DIR="${FRAMEPACK_REPO_DIR:-$ROOT_DIR/third_party/FramePack}"
WAN2GP_REPO_DIR="${WAN2GP_REPO_DIR:-$ROOT_DIR/third_party/Wan2GP}"
WAN2GP_REPO_URL="${WAN2GP_REPO_URL:-}"
SVD_MODEL_ID="${SVD_MODEL_ID:-stabilityai/stable-video-diffusion-img2vid-xt}"
ENABLE_XFORMERS="${ENABLE_XFORMERS:-1}"
FORCE_FP16="${FORCE_FP16:-1}"
export MODEL_ROOT SVD_MODEL_ID

echo "[Aurora] GPU provision starting"
echo "[Aurora] ROOT_DIR=$ROOT_DIR"
echo "[Aurora] GPU_ENV_DIR=$GPU_ENV_DIR"
echo "[Aurora] MODEL_ROOT=$MODEL_ROOT"
echo "[Aurora] SHARED_ROOT=$SHARED_ROOT"
echo "[Aurora] NVIDIA_API_KEY present: $([[ -n "${NVIDIA_API_KEY:-}" ]] && echo yes || echo no)"
echo "[Aurora] Secrets are never printed by this script."

python -m venv "$GPU_ENV_DIR"
source "$GPU_ENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install aria2p gdown huggingface_hub
python -m pip install diffusers accelerate safetensors transformers
python -m pip install xformers || echo "[Aurora] xformers install skipped or unsupported on this host."

mkdir -p "$MODEL_ROOT" "$SHARED_ROOT/outputs/liveportrait_requests" "$SHARED_ROOT/outputs/liveportrait_responses" "$SHARED_ROOT/outputs/liveportrait_archived_requests" "$SHARED_ROOT/outputs/i2v_requests" "$SHARED_ROOT/outputs/i2v_responses" "$SHARED_ROOT/outputs/i2v_archived_requests"

download_if_missing() {
  local target="$1"
  shift
  if [[ -e "$target" ]]; then
    echo "[Aurora] Exists: $target"
    return 0
  fi
  "$@"
}

python - <<'PY'
import os
from huggingface_hub import snapshot_download

model_root = os.environ["MODEL_ROOT"]
svd_model_id = os.environ["SVD_MODEL_ID"]
svd_path = os.path.join(model_root, "stable-video-diffusion-img2vid-xt")
if not os.path.exists(svd_path):
    snapshot_download(repo_id=svd_model_id, local_dir=svd_path, local_dir_use_symlinks=False)
print(f"[Aurora] SVD-XT ready: {svd_path}")
PY

mkdir -p "$(dirname "$FRAMEPACK_REPO_DIR")"
if [[ ! -d "$FRAMEPACK_REPO_DIR/.git" ]]; then
  git clone https://github.com/lllyasviel/FramePack.git "$FRAMEPACK_REPO_DIR"
else
  git -C "$FRAMEPACK_REPO_DIR" pull --ff-only
fi

if [[ -n "${FRAMEPACK_WEIGHTS_URL:-}" ]]; then
  mkdir -p "$MODEL_ROOT/framepack"
  if command -v aria2c >/dev/null 2>&1; then
    aria2c -x 8 -s 8 -d "$MODEL_ROOT/framepack" -o "$(basename "$FRAMEPACK_WEIGHTS_URL")" "$FRAMEPACK_WEIGHTS_URL"
  else
    gdown "$FRAMEPACK_WEIGHTS_URL" -O "$MODEL_ROOT/framepack/$(basename "$FRAMEPACK_WEIGHTS_URL")"
  fi
else
  echo "[Aurora] FRAMEPACK_WEIGHTS_URL not set; use repo auto-download or export a direct weights URL."
fi

if [[ -n "$WAN2GP_REPO_URL" ]]; then
  mkdir -p "$(dirname "$WAN2GP_REPO_DIR")"
  if [[ ! -d "$WAN2GP_REPO_DIR/.git" ]]; then
    git clone "$WAN2GP_REPO_URL" "$WAN2GP_REPO_DIR"
  else
    git -C "$WAN2GP_REPO_DIR" pull --ff-only
  fi
else
  echo "[Aurora] WAN2GP_REPO_URL is not set."
  echo "[Aurora] Set WAN2GP_REPO_URL to the verified upstream repo before enabling wan2gp_provider."
fi

if [[ -n "${WAN2GP_WEIGHTS_URL:-}" ]]; then
  mkdir -p "$MODEL_ROOT/wan2gp"
  if command -v aria2c >/dev/null 2>&1; then
    aria2c -x 8 -s 8 -d "$MODEL_ROOT/wan2gp" -o "$(basename "$WAN2GP_WEIGHTS_URL")" "$WAN2GP_WEIGHTS_URL"
  else
    gdown "$WAN2GP_WEIGHTS_URL" -O "$MODEL_ROOT/wan2gp/$(basename "$WAN2GP_WEIGHTS_URL")"
  fi
else
  echo "[Aurora] WAN2GP_WEIGHTS_URL not set; export it once the verified checkpoint source is available."
fi

cat <<EOF
[Aurora] Export these on the GPU host before running the worker:
export PROJECT_AURORA_GPU_MODE=production
export LIVEPORTRAIT_WORKER_MODE=production
export FORCE_FP16=$FORCE_FP16
export ENABLE_XFORMERS=$ENABLE_XFORMERS
export AURORA_SHARED_ROOT="$SHARED_ROOT"
export AURORA_JSONL_PATH="$SHARED_ROOT/aurora.jsonl"
export AURORA_LOCAL_JSONL_PATH="$ROOT_DIR/aurora.jsonl"
export SVD_PATH="$MODEL_ROOT/stable-video-diffusion-img2vid-xt"
export SVDXT_MODEL_ID="$MODEL_ROOT/stable-video-diffusion-img2vid-xt"
export FRAMEPACK_ROOT="$FRAMEPACK_REPO_DIR"
export FRAMEPACK_REPO="$FRAMEPACK_REPO_DIR"
export WAN2GP_RUNTIME="$WAN2GP_REPO_DIR"
EOF

echo "[Aurora] Provisioning complete."
echo "[Aurora] Activate with: source \"$GPU_ENV_DIR/bin/activate\""
