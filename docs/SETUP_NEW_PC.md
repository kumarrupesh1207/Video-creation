# Move Aurora to a new PC

GitHub holds the code, configuration and story packs. The private backup holds everything else: `.env`, YouTube and Meta tokens,
publish queue, series progress, rendered videos and Claude's project memory.
The repo is public, so the backup must never be committed or shared.

## On the old PC
**Do step 1 first.** Two PCs with one queue would post every video twice.

1. Stop the scheduled tasks and the servers:

```powershell
.\scripts\autopilot_task.ps1 -Unregister
.\stop_aurora.bat
```

2. Make the backup. It goes to `%USERPROFILE%\AuroraBackup`, about 400 MB:

```powershell
python scripts/transfer_state.py backup
```

3. Copy the zip to a private pendrive or drive. Do not send it through a public link.

Uncommitted work is not in the backup. Run `git status` first and commit what you want to keep.

## On the new PC
1. Install **Python 3.14**, **Git**, **Node.js** and **FFmpeg**. Check that `ffmpeg -version` works in a terminal.
2. Get the code. Use the same folder, `C:\Rupesh\App\Video creation`, if you can, because a few paths in `config.yaml` assume it:

```powershell
git clone https://github.com/kumarrupesh1207/Video-creation.git "C:\Rupesh\App\Video creation"
cd "C:\Rupesh\App\Video creation"
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd ui; npm install; cd ..
```

3. Restore the backup. If the folder changed, the saved paths are fixed for you:

```powershell
.venv\Scripts\python scripts\transfer_state.py restore "D:\aurora_backup_YYYYMMDD_HHMM.zip"
```

4. Update `paths.ffmpeg_bin` in `config.yaml` if FFmpeg is not at `C:/Rupesh/App/ffmpeg/bin/ffmpeg.exe`.
5. Check before anything can post. This prints what would go out and sends nothing:

```powershell
.venv\Scripts\python -m aurora.story lint echoes-of-the-aether
.venv\Scripts\python -m aurora.autopilot status
.venv\Scripts\python -m aurora.autopilot publish-due --dry-run
```

6. Start the schedule, only after the old PC's tasks are removed:

```powershell
.\scripts\autopilot_task.ps1 -Register
```

## Notes
- ComfyUI is optional: `comic.image_mode` is `nim`. Without it, the supervisor reports ComfyUI down, and rendering still works.
- Offline voices (`scripts/setup_tts.ps1`) and Wav2Lip are optional and are not restored.
- The first music or background-removal run on a new PC downloads its AI model once, which needs a few GB of disk and some time. Cached music comes with the backup.
- Meta data access lasts about 3 months from the last consent. If Facebook or Instagram posting stops, renew the token (docs/AUTOPILOT.md).
- `requirements.txt` is a tested pin set for Python 3.14 on Windows. Run the suite after changing it: `python -m pytest -q -p no:cacheprovider`.
