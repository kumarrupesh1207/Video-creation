# Aurora Kaggle worker (runs INSIDE a Kaggle GPU notebook/script kernel; T4/P100 ~16 GB).
# The provider prepends `REQUEST = {...}`. To use standalone in a notebook, define REQUEST yourself:
#   mode "t2i": {"mode":"t2i","model":"stabilityai/stable-diffusion-xl-base-1.0" | "black-forest-labs/FLUX.1-schnell",
#                "prompt":"...","negative":"...","width":768,"height":1344,"steps":30,"seed":1}
#   mode "i2v": {"mode":"i2v","model":"Wan-AI/Wan2.2-TI2V-5B-Diffusers","prompt":"...","image_b64":"...",
#                "width":832,"height":480,"num_frames":49,"steps":30,"seed":1}
# Outputs land in /kaggle/working (.png for t2i, .mp4 for i2v). OFF until the user supplies Kaggle credentials.
# CAVEAT (docs/research/GPU_BACKENDS.md): TI2V-5B is officially a 24 GB model. On 16 GB Kaggle GPUs this uses
# CPU offload + 480p + ~2 s clips; expect slow or OOM-prone runs. Use a Kaggle T4x2/P100 and lower num_frames if it fails.
import os as _os
_os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import base64
import io
import os
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers>=0.35", "transformers", "accelerate",
                "sentencepiece", "imageio[ffmpeg]", "ftfy"], check=False)

import torch  # noqa: E402
from PIL import Image  # noqa: E402

OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
seed = REQUEST.get("seed") or 1234  # noqa: F821 - REQUEST is injected
gen = torch.Generator("cuda").manual_seed(int(seed))
mode = REQUEST["mode"]  # noqa: F821

ESRGAN_URLS = {  # anime model for cel-shaded prompts, general model otherwise (anime model cartoonifies photos)
    "anime": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
    "photo": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
}


def _enhance(src, dst, upscale=True, interpolate=2, long_side=1920):
    """Smooth (ffmpeg motion interpolation, played as slow-mo) then sharpen (Real-ESRGAN anime x4) a clip."""
    import shutil
    import urllib.request

    import imageio.v2 as imageio
    import imageio_ffmpeg
    import numpy as np

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cur = src
    if interpolate > 1:
        mid = os.path.join(OUT, "interp.mp4")
        vf = (f"minterpolate=fps={24 * interpolate}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
              f"setpts={interpolate}*PTS")
        subprocess.run([ff, "-y", "-v", "error", "-i", cur, "-vf", vf, "-r", "24", "-c:v", "libx264",
                        "-crf", "12", "-pix_fmt", "yuv420p", mid], check=True)
        cur = mid
        print("interpolated x", interpolate, flush=True)
    if not upscale:
        shutil.copyfile(cur, dst)
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "spandrel"], check=False)
    from spandrel import ModelLoader

    kind = "anime" if "anime" in str(REQUEST.get("prompt", "")).lower() else "photo"  # noqa: F821
    weights = f"/tmp/esrgan_{kind}.pth"
    if not os.path.exists(weights):
        urllib.request.urlretrieve(ESRGAN_URLS[kind], weights)
    print("upscaler:", kind, flush=True)
    model = ModelLoader().load_from_file(weights).cuda().eval().half()
    reader = imageio.get_reader(cur)
    writer = imageio.get_writer(dst, fps=24, codec="libx264", quality=None, macro_block_size=2,
                                ffmpeg_params=["-crf", "14", "-pix_fmt", "yuv420p"])
    n = 0
    for frame in reader:
        t = torch.from_numpy(np.ascontiguousarray(frame)).permute(2, 0, 1).unsqueeze(0).half().div(255).cuda()
        with torch.no_grad():
            up = model(t)
        img = Image.fromarray(up.clamp(0, 1).mul(255).byte().squeeze(0).permute(1, 2, 0).cpu().numpy())
        scale = long_side / max(img.size)
        if scale < 1:
            img = img.resize((round(img.width * scale) // 2 * 2, round(img.height * scale) // 2 * 2), Image.LANCZOS)
        writer.append_data(np.asarray(img))
        n += 1
    writer.close()
    reader.close()
    if cur != src:
        os.remove(cur)  # drop the intermediate so only out.mp4 is published
    print("upscaled frames:", n, "->", img.size, flush=True)


if mode == "t2i":
    model = REQUEST.get("model", "stabilityai/stable-diffusion-xl-base-1.0")  # noqa: F821
    if "FLUX" in model:
        from diffusers import FluxPipeline

        pipe = FluxPipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()
        image = pipe(REQUEST["prompt"], width=REQUEST.get("width", 768), height=REQUEST.get("height", 1344),  # noqa: F821
                     num_inference_steps=4, guidance_scale=0.0, generator=gen).images[0]
    else:
        from diffusers import StableDiffusionXLPipeline

        pipe = StableDiffusionXLPipeline.from_pretrained(model, torch_dtype=torch.float16, variant="fp16")
        pipe.enable_model_cpu_offload()
        image = pipe(REQUEST["prompt"], negative_prompt=REQUEST.get("negative", "lowres, bad anatomy, bad hands, extra fingers, deformed"),  # noqa: F821
                     width=REQUEST.get("width", 768), height=REQUEST.get("height", 1344),  # noqa: F821
                     num_inference_steps=REQUEST.get("steps", 30), guidance_scale=5.5, generator=gen).images[0]  # noqa: F821
    image.save(os.path.join(OUT, "out.png"))
elif mode == "i2v":
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
    from diffusers.utils import export_to_video

    vae = AutoencoderKLWan.from_pretrained(REQUEST["model"], subfolder="vae", torch_dtype=torch.float32)  # noqa: F821
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16  # T4/P100 have no native bf16
    pipe = WanImageToVideoPipeline.from_pretrained(REQUEST["model"], vae=vae, torch_dtype=dtype)  # noqa: F821
    pipe.enable_model_cpu_offload()
    print("loaded", torch.cuda.get_device_name(0), flush=True)
    pipe.vae.enable_tiling()
    image = Image.open(io.BytesIO(base64.b64decode(REQUEST["image_b64"]))).convert("RGB")  # noqa: F821
    w, h = REQUEST.get("width", 832), REQUEST.get("height", 480)  # noqa: F821
    frames = pipe(image=image.resize((w, h)), prompt=REQUEST["prompt"], width=w, height=h,  # noqa: F821
                  num_frames=REQUEST.get("num_frames", 49), num_inference_steps=REQUEST.get("steps", 30),  # noqa: F821
                  guidance_scale=5.0, generator=gen).frames[0]
    raw = os.path.join(OUT, "raw.mp4")
    export_to_video(frames, raw, fps=24)
    del pipe, vae, frames
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    final = os.path.join(OUT, "out.mp4")
    try:
        _enhance(raw, final, REQUEST.get("upscale", True), int(REQUEST.get("interpolate", 2)))  # noqa: F821
        os.remove(raw)
    except Exception as exc:  # noqa: BLE001 - enhancement is best effort; never lose the raw clip
        print("enhance failed, keeping raw clip:", repr(exc), flush=True)
        os.replace(raw, final)
else:
    raise SystemExit(f"unknown mode {mode}")
print("done", mode)
