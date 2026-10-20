from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _preflight() -> None:
    if not os.getenv("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY is not set. Aborting unified launch.")


def _spawn_process(cmd: list[str]) -> subprocess.Popen:
    child_env = os.environ.copy()
    child_env.setdefault("API_BASE", "http://localhost:8000")
    return subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _terminate(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    _preflight()
    print("System Check: NVIDIA_API_KEY present")
    print("System Check: UI Secret Handling: Optimized for Environment Variables")
    print(f"System Check: API_BASE={os.environ.get('API_BASE', 'http://localhost:8000')}")

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "aurora.api:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    frontend_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "streamlit_app.py",
        "--server.port",
        "8501",
    ]

    print("Launching Aurora API on http://localhost:8000")
    backend = _spawn_process(backend_cmd)
    print("Launching Aurora Command Center on http://localhost:8501")
    frontend = _spawn_process(frontend_cmd)

    try:
        while True:
            if backend.poll() is not None:
                return backend.returncode or 1
            if frontend.poll() is not None:
                return frontend.returncode or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down Aurora services...")
        return 0
    finally:
        _terminate(frontend)
        _terminate(backend)


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    raise SystemExit(main())
