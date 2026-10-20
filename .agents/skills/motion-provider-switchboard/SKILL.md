Remote LivePortrait jobs must poll for a response clip for at most 120 seconds before falling back to ffmpeg_static.
All talking_motion requests must go through the MotionSwitchboard and preserve audit metadata for provider, fallback, and review status.
Remote GPU workers should emit a sidecar response JSON with status, latency, and GPU metrics; `PROVIDER_CRASH` must trigger immediate local fallback.
