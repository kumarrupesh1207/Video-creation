# img2img in the ComfyUI browser UI
1. Drag `src/workflows/img2img.json` onto the ComfyUI canvas (http://127.0.0.1:8188).
2. In **Load Image** pick your photo; edit the positive prompt (what to change) in the top text box.
3. In **KSampler** set `denoise` (0.35 = keep the person/composition, 0.55 = default, 0.75 = big changes) and press Queue.
(Aurora uses the same file: `LocalComfyUIProvider.generate_from_image(...)`.)
Note: this file is ComfyUI "API format"; the current ComfyUI frontend loads it by drag-and-drop. If yours refuses, use Workflow > Open instead.
