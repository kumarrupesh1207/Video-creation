# Kaggle chained AI-motion segments OOM / killed

Problem: chaining 2-3 Wan clips in one Kaggle kernel failed on segment 2 (host RAM "Killed", then CUDA OOM after trying to free the text encoder).
Root cause: with `enable_model_cpu_offload()`, models stay referenced between pipe calls; T5 (~10 GB) stayed resident on GPU/host even after `pipe.text_encoder = None`.
Fix: the worker renders each segment in a child process of itself (`AURORA_CHILD_REQUEST`), passing the previous segment's last frame; memory is fully released per segment (ae7cf81). Default 2 segments — the 3rd drifts badly.
How to recognise: Kaggle log shows `Killed` or `torch.OutOfMemoryError` right after `segment 1/N`; reel falls back to camera motion.
