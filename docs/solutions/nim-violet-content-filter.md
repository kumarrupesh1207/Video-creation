# NIM "CONTENT_FILTERED" on harmless prompts: the word "violet"

Problem: comic panels were blocked by NVIDIA NIM (`Generation blocked: CONTENT_FILTERED`) again and again, even after "softening" and new seeds; a reel's panel 0 failed 3 times, every run fell back to the slow ComfyUI path.
Root cause (bisected with single-credit raw calls, 2026-10-23): the filter is lexical, not about the picture. "a lone wanderer in a desert beneath a violet sky" -> blocked; the same with "purple" -> SUCCESS; plain photo prompts always passed. "violet" (looks like "violent") is the trigger; the model's own scripts love "violet sky".
Fix: `aurora/research.py::nim_safe()` rewrites known false-positive words (violet -> purple) in every panel prompt (`NIM_SAFE_WORDS`; extend it when a new one is confirmed). `soften_prompt` retries and a new seed per retry stay as a safety net.
How to recognise: CONTENT_FILTERED on a tame prompt; bisect by calling the raw API with parts of the prompt (1 credit each) instead of retrying the same text.
