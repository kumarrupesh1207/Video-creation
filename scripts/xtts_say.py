"""XTTS-v2 CLI (runs inside .venv-tts, Python 3.11):  xtts_say.py "text" lang out.wav [speaker_wav] [speaker_name]

speaker_wav clones a voice from a ~6-30 s sample; otherwise a built-in XTTS speaker is used.
"""
import os
import sys

os.environ.setdefault("COQUI_TOS_AGREED", "1")  # licence already accepted by the existing model download

text, lang, out = sys.argv[1], sys.argv[2], sys.argv[3]
speaker_wav = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] not in ("", "-") else None
speaker = sys.argv[5] if len(sys.argv) > 5 else "Claribel Dervla"

import torch  # noqa: E402
from TTS.api import TTS  # noqa: E402

torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))  # leave CPU for the user's PC
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
kwargs = {"speaker_wav": speaker_wav} if speaker_wav else {"speaker": speaker}
tts.tts_to_file(text=text, language=lang, file_path=out, **kwargs)
print("ok", out)
