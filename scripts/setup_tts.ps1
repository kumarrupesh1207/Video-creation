# One-time setup of the offline voice-cloning engine (Coqui XTTS-v2) in its own Python 3.11 venv.
# Aurora runs on Python 3.14, which XTTS does not support, so aurora/audio.py calls scripts/xtts_say.py
# through .venv-tts\Scripts\python.exe as a subprocess. Re-running is safe.
#   .\scripts\setup_tts.ps1
# Reuses the already-downloaded model in %LOCALAPPDATA%\tts (nothing is re-downloaded if it is there).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-tts"

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    $py = $null
    foreach ($tag in @("-V:Astral/CPython3.11.15", "-3.11", "-3.10")) {
        try { $exe = & py $tag -c "import sys;print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $exe) { $py = $exe; break } } catch {}
    }
    if (-not $py) { throw "No Python 3.10/3.11 found (py -0). Install one, e.g. 'uv python install 3.11'." }
    Write-Host "Creating venv with $py"
    & $py -m venv $venv
}
$pip = Join-Path $venv "Scripts\pip.exe"
& $pip install --upgrade pip
& $pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
& $pip install "coqui-tts[codec]" soundfile
Write-Host "Done. Test:  $venv\Scripts\python.exe scripts\xtts_say.py 'Hello there' en outputs\perf\xtts_test.wav"
