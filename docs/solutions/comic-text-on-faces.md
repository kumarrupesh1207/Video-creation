# Comic speech bubbles / narration boxes covered the characters' faces

Problem: bubbles and the narration box were drawn at fixed spots, so they often sat on faces, and the image model sometimes drew its own bubbles/lettering.
Fix: `aurora/comic_layout.py` finds faces (nagadomi `lbpcascade_animeface` in `assets/cascades/`, MIT; then OpenCV frontal Haar; then an edge-density subject box), maps them through the Ken-Burns zoom/pan into final 1080x1920 coordinates (union over the whole move), and `make_overlay(..., faces=...)` places the narration box at the emptier top/bottom edge and the bubble in the largest free area (>= 40 px from the frame edge, off faces and the narration box), with the tail aimed at the nearest face but stopping short of it. `render_comic(layout_aware=False)` keeps the old fixed layout for A/B comparisons.
Also: NIM ignores negative prompts, so the positive prompt says wordless/no lettering; `has_drawn_bubble()` (OpenCV: big pale rounded blob with dark marks inside) regenerates a panel once with a new seed (counted credit, max 2 per reel); narration is capped at 12 words so reels stay ~30 s; final encode crf 23, maxrate 6M.
How to recognise: bubble/tail overlapping a face in a reel frame; the log line `image-drawn speech bubble detected`.
Note: text drawn by the image model into the picture itself can't be moved, only avoided or regenerated.
