@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo 🚀 Starting Anime Batch Production...

:: Video 1: Solo Leveling (Current/Redo)
python main.py --preset beach_run_720p --topic "Cinematic Anime style: solo leveling dungeon entrance, dark atmosphere, glowing blue runes" --frames 6 --motion 120 --skip-lipsync --skip-upscale --timeout 2700

:: Video 2: Naruto
python main.py --preset beach_run_720p --topic "Cinematic Anime style: Naruto standing atop Hokage rock, sunset background, flowing orange cape" --frames 6 --motion 120 --skip-lipsync --skip-upscale --timeout 2700

:: Video 3: One Piece
python main.py --preset beach_run_720p --topic "Cinematic Anime style: Luffy on the Thousand Sunny, vast blue ocean, straw hat blowing in wind" --frames 6 --motion 120 --skip-lipsync --skip-upscale --timeout 2700

:: Video 4: Dragon Ball Z
python main.py --preset beach_run_720p --topic "Cinematic Anime style: Goku powering up, yellow lightning aura, rocky wasteland" --frames 6 --motion 120 --skip-lipsync --skip-upscale --timeout 2700

echo ✅ All videos completed! Check outputs\final\
pause
