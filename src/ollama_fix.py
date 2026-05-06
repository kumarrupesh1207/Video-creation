"""
ollama_fix.py  — One-shot Ollama daemon launcher + health check
---------------------------------------------------------------
Run this FIRST if 'ollama run' times out.

Problem: Ollama 0.23.x on Windows starts a tray app that blocks
the CLI when no server socket is bound yet.

Fix: Launch the HTTP server directly, then verify it's up.
"""

import subprocess
import time
import sys
import requests


OLLAMA_HOST = "http://127.0.0.1:11434"
MODEL = "llama3:8b"


def wait_for_server(timeout=30) -> bool:
    print(f"[Ollama] Waiting for server at {OLLAMA_HOST} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
        print("  ...", end="", flush=True)
    print()
    return False


def start_server():
    """Launch 'ollama serve' as a background process."""
    print("[Ollama] Starting server via 'ollama serve'...")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows: detach
    )
    return proc


def list_models() -> list:
    r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
    return [m["name"] for m in r.json().get("models", [])]


def test_generate(model: str) -> str:
    payload = {
        "model": model,
        "prompt": "Write a 5-word test script.",
        "stream": False,
        "options": {"num_predict": 20}
    }
    r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["response"].strip()


def main():
    # 1. Check if already up
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
        if r.status_code == 200:
            print("[Ollama] ✅ Server already running.")
        else:
            raise Exception("bad status")
    except Exception:
        proc = start_server()
        if not wait_for_server(timeout=30):
            print("[Ollama] ❌ Server failed to start. Check Ollama installation.")
            sys.exit(1)
        print(f"\n[Ollama] ✅ Server started (PID={proc.pid})")

    # 2. List models
    models = list_models()
    print(f"[Ollama] Installed models: {models}")

    if MODEL not in models:
        print(f"\n[Ollama] ⚠️  '{MODEL}' not found. Pulling now (this takes a while)...")
        subprocess.run(["ollama", "pull", MODEL], check=True)
        print(f"[Ollama] ✅ '{MODEL}' pulled successfully.")

    # 3. Quick generation test
    print(f"\n[Ollama] Testing generation with '{MODEL}'...")
    response = test_generate(MODEL)
    print(f"[Ollama] Response: {response}")
    print("\n[Ollama] ✅ LLMManager is READY. You can now run main.py.\n")


if __name__ == "__main__":
    main()
