# img2img in the ComfyUI browser UI
1. Drag `src/workflows/img2img.json` onto the ComfyUI canvas (http://127.0.0.1:8188). Use `img2img_facelock.json` instead to keep the person's face (needs the IPAdapter Plus node + models, already installed here).
2. In **Load Image** pick your photo (the face-lock version also reads the face from this same image); edit the positive prompt (what to change).
3. In **KSampler** set `denoise` (0.35 = keep the person/composition, 0.45 = default, 0.75 = big changes); in **IPAdapter Advanced** `weight` ~0.8 = face lock strength. Press Queue.
(Aurora uses the same files: `LocalComfyUIProvider.generate_from_image(..., face_lock=True)`.)
Note: these files are ComfyUI "API format"; the current frontend loads them by drag-and-drop. If yours refuses, use Workflow > Open.
