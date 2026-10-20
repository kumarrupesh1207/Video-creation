from __future__ import annotations

import os
import time
from pathlib import Path

import requests
import streamlit as st


DEFAULT_API_BASE = "http://localhost:8000"


def _resolve_api_base() -> str:
    env_value = os.getenv("API_BASE")
    if env_value:
        return env_value

    try:
        secrets = getattr(st, "secrets", None)
        if secrets:
            secret_value = secrets.get("api_base")
            if secret_value:
                return secret_value
    except Exception:
        pass

    return DEFAULT_API_BASE


API_BASE = _resolve_api_base()


def _narration_upload_dir() -> Path:
    shared_root = os.getenv("AURORA_SHARED_ROOT", "").strip()
    if shared_root:
        return Path(shared_root) / "outputs" / "narration_uploads"
    return Path("outputs/narration_uploads")


def _stage_uploaded_narration(uploaded_file) -> str | None:
    if uploaded_file is None:
        return None
    cache_key = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("narration_upload_cache_key") == cache_key:
        return st.session_state.get("selected_narration_audio_path")
    upload_dir = _narration_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{int(time.time() * 1000)}_{uploaded_file.name}"
    target.write_bytes(uploaded_file.getbuffer())
    st.session_state["narration_upload_cache_key"] = cache_key
    return str(target.resolve())


def api_get(path: str, timeout: int = 30) -> dict:
    resp = requests.get(f"{API_BASE}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str, payload: dict | None = None, timeout: int = 60) -> dict:
    resp = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def backend_heartbeat() -> tuple[bool, dict | None, str | None]:
    try:
        status = api_get("/api/providers/status", timeout=5)
        return True, status, None
    except Exception as exc:
        return False, None, str(exc)


def render_sidebar() -> None:
    st.sidebar.markdown("## System Pulse")
    online, status, error = backend_heartbeat()
    if online and status:
        backend = status.get("backend", {})
        nim = status.get("nvidia_nim", {})
        asset = status.get("asset_library", {})
        local = status.get("local_comfyui", {})
        st.sidebar.metric("Backend Status", "BACKEND ONLINE")
        st.sidebar.caption(backend.get("api_base", API_BASE))
        st.sidebar.metric("NVIDIA NIM", "Online" if nim.get("enabled") and nim.get("api_key_present") else "Offline")
        st.sidebar.caption(f"Model: {nim.get('model', '—')}")
        st.sidebar.metric("Quota Limit", f"{nim.get('quota_limit_pct', 0)}%")
        st.sidebar.metric("Asset Library", asset.get("count", 0))
        st.sidebar.metric("Credit Savings", f"{asset.get('credit_savings', 0):.2f}")
        st.sidebar.caption(f"Cached Hits: {asset.get('cached_hits', 0)}")
        st.sidebar.caption(f"Avg Asset Latency: {asset.get('avg_latency_ms', 0)} ms")
        st.sidebar.caption(f"Asset Reuse: {'Enabled' if asset.get('reuse_existing_asset') else 'Disabled'}")
        st.sidebar.caption(f"Local Fallback: {local.get('api_url', '—')}")
    else:
        st.sidebar.metric("Backend Status", "OFFLINE")
        st.sidebar.error(f"Heartbeat failed: {error}")

    if st.sidebar.button("Run Smoke Test", use_container_width=True):
        try:
            result = api_post("/api/smoke-test", timeout=20)
            st.sidebar.success(result.get("message", "Smoke test started"))
        except Exception as exc:
            st.sidebar.error(f"Smoke test failed to start: {exc}")

    auto_refresh = st.sidebar.toggle("Auto Refresh", value=True)
    if auto_refresh:
        time.sleep(2)
        st.rerun()


def render_job_creator() -> None:
    st.subheader("Job Creator")
    motion_label = st.segmented_control(
        "Motion Mode",
        options=["Static Cinematic", "Talking Head", "Full Motion"],
        default="Static Cinematic",
        key="motion_mode_toggle",
    )
    motion_mode = (
        "talking_motion" if motion_label == "Talking Head"
        else "full_i2v_motion" if motion_label == "Full Motion"
        else "static_cinematic"
    )
    visual_complexity = st.selectbox(
        "Visual Complexity",
        options=["standard", "high action", "atmospheric"],
        index=0,
        disabled=motion_mode != "full_i2v_motion",
    )
    narration_upload = st.file_uploader(
        "Audio Narration",
        type=["mp3", "wav", "m4a"],
        accept_multiple_files=False,
        help="Optional narration track for talking-motion lip sync.",
        key="narration_uploader",
    )
    narration_path_input = st.text_input(
        "Or Existing Narration Path",
        placeholder="C:\\path\\to\\peace_of_mind.mp3",
        key="narration_path_input",
    )
    identity_lock = st.toggle(
        "Identity Lock",
        value=True,
        help="Reuse the base asset as the strict identity anchor across repeated motion jobs.",
        key="identity_lock_toggle",
    )
    selected_narration_path = _stage_uploaded_narration(narration_upload) or (narration_path_input.strip() or None)
    st.session_state["selected_narration_audio_path"] = selected_narration_path
    st.session_state["selected_identity_lock"] = identity_lock
    st.session_state["selected_motion_mode"] = motion_mode
    st.session_state["selected_visual_complexity"] = visual_complexity
    single_tab, batch_tab = st.tabs(["Single Topic", "Batch Upload"])

    with single_tab:
        with st.form("single_job_form"):
            topic = st.text_input("Topic", placeholder="Super Saiyan Warrior")
            force_new = st.checkbox("Force New Image", value=False)
            submit = st.form_submit_button("Generate")
        if submit and topic.strip():
            result = api_post(
                "/api/jobs",
                {
                    "topic": topic.strip(),
                    "mode": "static_cinematic",
                    "motion_mode": motion_mode,
                    "visual_complexity": visual_complexity,
                    "force_new_image": force_new,
                    "reuse_existing_asset": not force_new,
                    "narration_audio_path": selected_narration_path,
                    "identity_lock": identity_lock,
                },
            )
            st.success(result.get("message", "Job started"))

    with batch_tab:
        with st.form("batch_job_form"):
            topics_blob = st.text_area("Batch Topics", placeholder="One topic per line")
            submit_batch = st.form_submit_button("Start Batch")
        if submit_batch:
            topics = [line.strip() for line in topics_blob.splitlines() if line.strip()]
            if topics:
                result = api_post(
                    "/api/jobs",
                    {
                        "topics": topics,
                        "mode": "static_cinematic",
                        "motion_mode": motion_mode,
                        "visual_complexity": visual_complexity,
                        "reuse_existing_asset": True,
                    },
                )
                st.success(result.get("message", "Batch started"))


def render_progress_monitor() -> None:
    st.subheader("Live Monitor")
    try:
        status = api_get("/api/status")
        jobs = api_get("/api/jobs").get("items", [])
    except Exception as exc:
        st.error(f"Monitor unavailable: {exc}")
        return

    latest_job = jobs[0] if jobs else {}
    progress = latest_job.get("progress_percent", status.get("progress", 0))
    label = latest_job.get("topic") or status.get("phase_label", "Idle")
    st.progress(min(max(int(progress), 0), 100), text=label)

    cols = st.columns(5)
    cols[0].metric("Running", "Yes" if status.get("running") else "No")
    cols[1].metric("Paused", "Yes" if status.get("paused") else "No")
    cols[2].metric("Provider", latest_job.get("provider_used", "—"))
    cols[3].metric("Motion", latest_job.get("motion_provider_used", latest_job.get("motion_mode", "—")))
    cols[4].metric("Latency", f"{latest_job.get('latency_ms', 0)} ms")
    motion_metrics = latest_job.get("motion_gpu_metrics") or {}
    metric_cols = st.columns(3)
    metric_cols[0].metric("Remote VRAM Peak", f"{motion_metrics.get('vram_peak_mb', 0) or 0} MB")
    metric_cols[1].metric("Remote it/s", f"{motion_metrics.get('it_per_sec', motion_metrics.get('avg_iteration_speed_its', 0)) or 0}")
    metric_cols[2].metric("Remote Load", f"{motion_metrics.get('load_time_ms', 0) or 0} ms")
    st.caption(
        f"Cached Asset: {'Yes' if latest_job.get('using_cached_asset') else 'No'}"
        + (f" | {latest_job.get('motion_badge')}" if latest_job.get("motion_badge") else "")
    )
    if latest_job.get("motion_status") == "waiting_remote_gpu":
        st.info("Status: Waiting for Remote GPU...")

    action_cols = st.columns(3)
    if action_cols[0].button("Pause Batch / Job"):
        st.info(api_post("/api/pause").get("message", "Pause requested"))
    if action_cols[1].button("Resume Batch / Job"):
        st.info(api_post("/api/resume").get("message", "Resume requested"))
    if action_cols[2].button("Stop"):
        st.info(api_post("/api/stop").get("message", "Stop requested"))

    if latest_job.get("final_output"):
        final_path = Path(latest_job["final_output"])
        if final_path.exists():
            st.video(str(final_path))
            quality = latest_job.get("quality_check", {})
            badge = "✅ Quality Check" if quality.get("passed") else "⚠ Quality Check"
            st.caption(f"{badge}: {quality.get('label', 'Pending')}")
            base_image_path = latest_job.get("reference_image_path")
            if base_image_path:
                anchor_path = Path(base_image_path)
                if anchor_path.exists():
                    st.markdown("**Comparison View**")
                    compare_cols = st.columns(2)
                    compare_cols[0].image(str(anchor_path), caption="Base Anchor", use_container_width=True)
                    compare_cols[1].video(str(final_path))
                    g12 = latest_job.get("g12_check", {})
                    if g12:
                        st.caption(
                            f"G12: {'Pass' if g12.get('passed') else 'Review'}"
                            + (f" | MSE {g12.get('mse')}" if g12.get('mse') is not None else "")
                        )


def render_jobs_table() -> None:
    st.subheader("Recent Jobs")
    try:
        jobs = api_get("/api/jobs").get("items", [])
    except Exception as exc:
        st.error(f"Jobs unavailable: {exc}")
        return
    if not jobs:
        st.info("No jobs yet.")
        return

    rows = []
    for job in jobs:
        rows.append(
            {
                "Job": job.get("job_id"),
                "Topic": job.get("topic"),
                "Provider": job.get("provider_used"),
                "Progress": job.get("progress_percent"),
                "Cached Asset": "Using Cached Asset" if job.get("using_cached_asset") else "Fresh Render",
                "Motion Provider": job.get("motion_provider_used", "—"),
                "Motion Badge": job.get("motion_badge", ""),
                "Motion Status": job.get("motion_status", ""),
                "Complexity": job.get("visual_complexity", ""),
                "Quality": job.get("quality_check", {}).get("label", "Pending"),
                "Latency (ms)": job.get("latency_ms", 0),
                "Remote VRAM Peak (MB)": (job.get("motion_gpu_metrics") or {}).get("vram_peak_mb", 0),
                "Remote it/s": (job.get("motion_gpu_metrics") or {}).get("it_per_sec", (job.get("motion_gpu_metrics") or {}).get("avg_iteration_speed_its", 0)),
                "Cost": job.get("generation_cost", 0.0),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_asset_gallery() -> None:
    st.subheader("Asset Gallery")
    motion_mode = st.session_state.get("selected_motion_mode", "static_cinematic")
    visual_complexity = st.session_state.get("selected_visual_complexity", "standard")
    try:
        assets = api_get("/api/assets").get("items", [])
    except Exception as exc:
        st.error(f"Asset gallery unavailable: {exc}")
        return

    if not assets:
        st.info("No assets registered yet.")
        return

    for asset in assets:
        with st.container(border=True):
            cols = st.columns([1, 2])
            image_path = Path(asset["output_path"])
            if image_path.exists():
                cols[0].image(str(image_path), use_container_width=True)
            cols[1].markdown(f"**{asset['provider']}**")
            cols[1].caption(asset["normalized_prompt"][:200])
            cols[1].write(f"Model: {asset['model']}")
            cols[1].write(f"Native Size: {asset['width']}x{asset['height']}")
            cols[1].write(f"Latency: {asset.get('latency_ms', 0)} ms")
            cols[1].write(f"Generation Cost: {asset.get('generation_cost', 0.0):.2f}")
            cols[1].write(f"Motion Variants: {asset.get('motion_variant_count', 0)}")
            if cols[1].button("Generate Video", key=f"asset-{asset['asset_key']}"):
                staged_narration_path = st.session_state.get("selected_narration_audio_path")
                result = api_post(
                    "/api/jobs",
                    {
                        "asset_key": asset["asset_key"],
                        "topic": asset["normalized_prompt"],
                        "mode": "static_cinematic",
                        "motion_mode": motion_mode,
                        "visual_complexity": visual_complexity,
                        "reuse_existing_asset": True,
                        "force_new_image": False,
                        "narration_audio_path": staged_narration_path,
                        "identity_lock": st.session_state.get("selected_identity_lock", True),
                    },
                )
                st.success(result.get("message", "Asset render started"))


def render_batch_status() -> None:
    st.subheader("Batch Status")
    try:
        batch = api_get("/api/batch")
    except Exception as exc:
        st.error(f"Batch status unavailable: {exc}")
        return

    st.write(f"Status: {batch.get('status', 'idle')}")
    aggregate = int(batch.get("aggregate_progress_percent", 0) or 0)
    st.progress(min(max(aggregate, 0), 100), text=f"Total Batch Completion: {aggregate}%")
    items = batch.get("items", [])
    if items:
        st.dataframe(items, use_container_width=True, hide_index=True)


def apply_theme() -> None:
    st.set_page_config(page_title="Aurora Command Center", page_icon="🎛", layout="wide")
    st.markdown(
        """
        <style>
        .stApp { background: #0b1220; color: #e5edf7; }
        [data-testid="stSidebar"] { background: #121a2b; }
        .stButton>button { background: #1f6feb; color: white; border-radius: 8px; }
        .stTextInput input, .stTextArea textarea { background: #111827; color: #e5edf7; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    apply_theme()
    st.title("Aurora Command Center")
    render_sidebar()
    creator_col, monitor_col = st.columns([1, 1])
    with creator_col:
        render_job_creator()
    with monitor_col:
        render_progress_monitor()
    render_jobs_table()
    render_asset_gallery()
    render_batch_status()


if __name__ == "__main__":
    main()
