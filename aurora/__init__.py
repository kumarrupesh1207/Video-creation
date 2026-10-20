"""Aurora domain modules."""
import sys


def ensure_utf8_console() -> None:
    """Make stdout/stderr tolerate emoji/Unicode on Windows consoles (cp1252) without PYTHONUTF8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

