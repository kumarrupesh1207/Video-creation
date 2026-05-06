"""
llm_manager.py
──────────────
Day 1: Ollama/Llama-3 script generation.

Generates a structured JSON video script via Ollama REST API.
Runs entirely on CPU/RAM — zero VRAM footprint.

Output format:
{
  "title": "...",
  "hook": "5-second opening line",
  "body_segments": [
    {"text": "...", "duration_sec": 8},
    ...
  ],
  "cta": "Call-to-action close"
}
"""

import json
import time
import requests
from pathlib import Path


class LLMManager:
    """Generates video scripts via Ollama REST API (CPU-only, no VRAM)."""

    FALLBACK_SCRIPT = {
        "title":         "Placeholder Script",
        "hook":          "The future of finance is being written in code.",
        "body_segments": [
            {"text": "Agile transformations in banking are not about sprints. "
                     "They are about shifting the entire culture of delivery.",
             "duration_sec": 8},
            {"text": "The banks winning today have one thing in common: "
                     "they treat software as a product, not a project.",
             "duration_sec": 8},
            {"text": "Cross-functional teams, continuous delivery, and customer "
                     "obsession — that is the new core banking stack.",
             "duration_sec": 8},
        ],
        "cta": "Follow for more fintech insights from the inside."
    }

    def __init__(self, config: dict):
        llm_cfg = config.get("models", {}).get("llm", {})
        self.endpoint   = llm_cfg.get("endpoint",
                          "http://127.0.0.1:11434/api/generate")
        self.model_name = llm_cfg.get("model_name", "llama3:8b")
        self.temperature= llm_cfg.get("temperature", 0.7)
        self.max_tokens = llm_cfg.get("max_tokens",  1024)
        self.out_dir    = Path(config.get("paths", {}).get(
            "scripts_dir", "outputs/scripts"))
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────
    def generate_script(self, topic: str, duration_sec: int = 30,
                        retries: int = 2) -> dict:
        """
        Generate a video script for the given topic.

        Args:
            topic:       What the video is about.
            duration_sec: Target video length (controls body segment count).
            retries:     Number of JSON parse retry attempts.

        Returns:
            Script dict with keys: title, hook, body_segments, cta.
        """
        print(f"\n[LLM] Generating script: \"{topic}\"")
        print(f"[LLM] Model: {self.model_name} | Target: {duration_sec}s")

        if not self._server_alive():
            print("[LLM] ⚠️  Ollama not reachable — using fallback script.")
            return self._save(self._apply_topic(self.FALLBACK_SCRIPT, topic), topic)

        prompt = self._build_prompt(topic, duration_sec)

        for attempt in range(retries + 1):
            try:
                script = self._call_ollama(prompt)
                self._validate(script)
                print(f"[LLM] ✅ Script generated: \"{script.get('title', '')}\"")
                return self._save(script, topic)
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                if attempt < retries:
                    print(f"[LLM] Parse error (attempt {attempt+1}): {e} — retrying...")
                    time.sleep(1)
                else:
                    print(f"[LLM] ⚠️  All attempts failed ({e}). Using fallback.")
                    return self._save(self._apply_topic(self.FALLBACK_SCRIPT, topic), topic)

    def get_full_text(self, script: dict) -> str:
        """Flatten all body segments into a single string for TTS."""
        parts = []
        hook = script.get("hook", "")
        if hook:
            parts.append(hook)
        for seg in script.get("body_segments", []):
            text = seg.get("text", "").strip()
            if text:
                parts.append(text)
        cta = script.get("cta", "")
        if cta:
            parts.append(cta)
        return " ".join(parts)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────
    def _server_alive(self) -> bool:
        try:
            base = self.endpoint.rsplit("/api/", 1)[0]
            r = requests.get(f"{base}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def _build_prompt(self, topic: str, duration: int) -> str:
        segments = max(2, duration // 8)
        return (
            f"Write a {duration}-second vertical short-form video script about: \"{topic}\".\n\n"
            "Return ONLY valid JSON (no markdown, no explanation) with this exact structure:\n"
            "{\n"
            '  "title": "Compelling video title",\n'
            '  "hook": "Single punchy opening sentence (5 seconds)",\n'
            f'  "body_segments": [\n'
            f'    {{"text": "Segment text", "duration_sec": 8}}\n'
            f'    // Include {segments} segments\n'
            '  ],\n'
            '  "cta": "Short call to action"\n'
            "}\n\n"
            "Rules:\n"
            "- Hook must grab attention in the first 3 words\n"
            "- Keep each segment under 25 words\n"
            "- Use active voice, present tense\n"
            "- No hashtags, emojis, or asterisks in the text"
        )

    def _call_ollama(self, prompt: str) -> dict:
        payload = {
            "model":  self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        }
        resp = requests.post(self.endpoint, json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("response", "")

        # Extract JSON block (handles models that wrap in ```json ... ```)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in LLM response")
        return json.loads(raw[start:end])

    def _validate(self, script: dict) -> None:
        required = {"title", "hook", "body_segments", "cta"}
        missing  = required - set(script.keys())
        if missing:
            raise KeyError(f"Missing keys: {missing}")
        if not isinstance(script["body_segments"], list):
            raise ValueError("body_segments must be a list")
        if len(script["body_segments"]) == 0:
            raise ValueError("body_segments is empty")

    def _apply_topic(self, template: dict, topic: str) -> dict:
        """Stamp the topic into the fallback template title."""
        import copy
        s = copy.deepcopy(template)
        s["title"] = f"{topic} (auto-generated)"
        return s

    def _save(self, script: dict, topic: str) -> dict:
        slug = "".join(c if c.isalnum() else "_" for c in topic)[:40].lower()
        path = self.out_dir / f"{slug}_script.json"
        path.write_text(json.dumps(script, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[LLM] Saved → {path}")
        return script
