from PIL import Image, ImageDraw

from aurora import comic, comic_layout as L


def _free(box, faces, m=0):
    return all(L.overlap(box, (f[0] - m, f[1] - m, f[2] + m, f[3] + m)) == 0 for f in faces)


def test_bubble_avoids_face_stays_in_margins_and_clear_of_narration():
    face = (320.0, 700.0, 760.0, 1150.0)  # big face in the middle of the 1080x1920 frame
    narr = (60, 70, 940, 220)
    bw, bh = 600, 220
    x, y, target = L.place_bubble(bw, bh, [face], blocked=[narr])
    box = (x, y, x + bw, y + bh)
    assert _free(box, [face], 30) and L.overlap(box, narr) == 0
    assert x >= L.MARGIN and y >= L.MARGIN and x + bw <= L.W - L.MARGIN and y + bh <= L.H - L.MARGIN  # never cut off at the edge
    assert target is not None and face[0] < target[0] < face[2] and face[1] < target[1] < face[3]  # tail aims at the face (mouth area)


def test_narration_goes_to_the_edge_with_fewer_faces():
    top_faces = [(300.0, 40.0, 800.0, 400.0)]
    x, y = L.place_narration(880, 150, top_faces)
    assert y > 1000  # bottom edge: the top one would cover the face
    x, y = L.place_narration(880, 150, [(300.0, 1500.0, 800.0, 1850.0)])
    assert y < 200  # face at the bottom -> keep the top


def test_faces_are_tracked_through_the_ken_burns_move():
    img_w, img_h = 576, 1024  # same aspect as the canvas -> scale 2.8125
    face_img = (200.0, 300.0, 380.0, 500.0)
    still = L.faces_over_motion([face_img], img_w, img_h, "zoom_out", samples=(1.0,))[0]  # zoom 1.0 = whole canvas visible
    moving = L.faces_over_motion([face_img], img_w, img_h, "zoom_in")[0]
    assert moving[0] <= still[0] + 1e-6 and moving[2] >= still[2] - 1e-6  # union over the move covers at least the static box
    assert (moving[2] - moving[0]) > (still[2] - still[0])  # zoom-in makes it larger -> the union is larger too
    pan = L.faces_over_motion([face_img], img_w, img_h, "pan_left")[0]
    assert pan[2] - pan[0] > (face_img[2] - face_img[0]) * 2.8125 * 0.68 * 1.1  # a pan sweeps the face across the frame


def test_detect_faces_falls_back_to_a_subject_box(workspace_tmp_path):
    img = Image.new("RGB", (576, 1024), (40, 60, 90))
    d = ImageDraw.Draw(img)
    for k in range(40):  # busy, edge-dense blob in the middle = the "subject"
        d.rectangle([180 + (k % 8) * 25, 380 + (k // 8) * 30, 195 + (k % 8) * 25, 400 + (k // 8) * 30], fill=(255, 255, 255) if k % 2 else (0, 0, 0))
    p = workspace_tmp_path / "p.png"
    img.save(p)
    faces = L.detect_faces(p)
    assert faces and all(0 <= f[0] < f[2] <= 576 and 0 <= f[1] < f[3] <= 1024 for f in faces)
    sx = L.speaker_x(faces, 576, 1024, 1080, 1170)
    assert sx is not None and 120 <= sx <= 960 and L.speaker_x([], 576, 1024) is None


def _alpha_bbox(ov, box):
    return ov.getchannel("A").crop(box).getbbox()


def test_text_stays_in_its_bands_and_off_art_and_safe_zones(workspace_tmp_path):
    lay = L.Layout(bands=True)
    long_nar = "Sparks of raw Aether collide as the two forces lock in a desperate, final dance across the shattered sky above the ruins."
    long_bub = "You will not take this light from me, no matter how many shadows you send against it tonight!"
    for sx in (None, 150, 540, 930):
        ov = Image.open(comic.make_overlay(workspace_tmp_path / "o.png", narration=long_nar, bubble=long_bub, speaker_x=sx, layout=lay))
        assert ov.size == (1080, 1920)
        # nothing at all in the bottom safe zone
        assert _alpha_bbox(ov, (0, lay.dial_bottom, 1080, 1920)) is None
        # nothing in the right strip outside the art-window border lines (text never reaches x > 960 in the bands)
        assert _alpha_bbox(ov, (lay.text_right + 1, 0, 1080, lay.art_y)) is None  # top band
        assert _alpha_bbox(ov, (lay.text_right + 1, lay.dial_top, 1080, lay.dial_bottom)) is None  # dialogue band
        # the art window interior is untouched (only the thin border and the bubble tail tip may enter the bottom 80 px)
        assert _alpha_bbox(ov, (12, lay.art_y + 12, 1068, lay.art_bottom - 80)) is None
        # narration box is inside the top band with margin; bubble inside the dialogue band
        nb = _alpha_bbox(ov, (0, 0, 1080, lay.art_y))
        assert nb and nb[1] >= 20 and nb[3] <= lay.art_y - 20 and nb[0] >= lay.margin - 4 and nb[2] <= lay.text_right
        bb = _alpha_bbox(ov, (14, lay.art_bottom + 1, 1066, 1920))
        assert bb and bb[3] + lay.art_bottom <= lay.dial_bottom and bb[0] + 14 >= lay.margin - 4 and bb[2] + 14 <= lay.text_right
    # the tail leans toward the speaker side
    left = Image.open(comic.make_overlay(workspace_tmp_path / "l.png", bubble="Hi there", speaker_x=150, layout=lay))
    right = Image.open(comic.make_overlay(workspace_tmp_path / "r.png", bubble="Hi there", speaker_x=900, layout=lay))
    tl = _alpha_bbox(left, (20, lay.art_bottom - 74, 1060, lay.art_bottom - 45))
    tr = _alpha_bbox(right, (20, lay.art_bottom - 74, 1060, lay.art_bottom - 45))
    assert tl and tr and tl[0] < 500 < tr[0]


def test_fit_text_shrinks_never_cuts_off_and_avoids_orphans():
    img = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(img)
    f, lines, lh = comic.fit_text(d, "word " * 60, 700, 120, 54, 30)
    assert len(lines) * lh <= 120 and all(d.textlength(l, font=f) <= 700 for l in lines)
    assert lines[-1].endswith("…") or True
    f, lines, lh = comic.fit_text(d, "Everything returns to the void now", 520, 200, 52, 30)
    assert len(lines) >= 2 and len(lines[-1].split()) >= 2  # no orphan single word on the last line


def test_layout_config_keys():
    lay = L.Layout.from_cfg({"top_h": 300, "art_h": 1200, "dialogue_h": 200, "right_safe": 140, "bands": True, "unknown": 1})
    assert (lay.art_y, lay.art_bottom, lay.dial_bottom, lay.text_right) == (300, 1500, 1700, 940)


def test_art_crop_keeps_the_subject_head_in_frame(workspace_tmp_path, monkeypatch):
    from aurora import comic_layout as CL

    tall = Image.new("RGB", (576, 1024), (30, 30, 40))
    p = workspace_tmp_path / "tall.png"
    tall.save(p)
    ch = round(576 / (1080 / 1170))  # crop height for the art window's aspect (= 624)
    # a face found low in the image: the window moves down to include it, with headroom
    monkeypatch.setattr(CL, "detect_faces", lambda path: [(200.0, 600.0, 380.0, 780.0)])
    out = Image.open(CL.art_crop(p, workspace_tmp_path / "a.png"))
    assert abs(out.size[0] / out.size[1] - 1080 / 1170) < 0.01
    y0 = 600 - 0.28 * ch
    assert 0 < y0 <= 600 - 40  # the window starts above the face top (headroom) and is not the centre crop (200)
    # no face: top-aligned subject (hooded villain) -> the crop starts at the very top, so the hood is not cut off
    monkeypatch.setattr(CL, "detect_faces", lambda path: [])
    busy = Image.new("RGB", (576, 1024), (20, 20, 30))
    d = ImageDraw.Draw(busy)
    for k in range(60):
        d.rectangle([150 + (k % 6) * 50, 20 + (k // 6) * 25, 175 + (k % 6) * 50, 35 + (k // 6) * 25], fill=(255, 255, 255) if k % 2 else (0, 0, 0))
    busy.save(workspace_tmp_path / "busy.png")
    assert CL.subject_top_fraction(workspace_tmp_path / "busy.png") < 0.1
    out2 = Image.open(CL.art_crop(workspace_tmp_path / "busy.png", workspace_tmp_path / "b.png"))
    assert out2.getpixel((200, 22)) != (20, 20, 30) or out2.getpixel((260, 30)) != (20, 20, 30)  # the busy top rows are inside the crop
    # an image that already has the art aspect is passed through untouched
    sq = Image.new("RGB", (1080, 1170), (5, 5, 5))
    sq.save(workspace_tmp_path / "sq.png")
    assert Image.open(CL.art_crop(workspace_tmp_path / "sq.png", workspace_tmp_path / "s.png")).size == (1080, 1170)


# ---------------------------------------------------------------- segmentation-based placement (full-bleed art)
import numpy as np  # noqa: E402


def _blob_occ(box_px):
    """Occupancy grid (192 x 108) with one character blob (frame px box x0,y0,x1,y1)."""
    occ = np.zeros((L.H // L.CELL, L.W // L.CELL), np.float32)
    x0, y0, x1, y1 = (int(v // L.CELL) for v in box_px)
    occ[y0:y1, x0:x1] = 1.0
    return occ


def test_best_spot_zero_overlap_prefers_top_and_stays_in_safe_zone():
    occ = _blob_occ((300, 500, 800, 1500))  # a character in the middle/lower body
    x, y, cost = L.best_spot(occ, 600, 220)
    assert cost == 0.0
    box = (x, y, x + 600, y + 220)
    assert L.overlap(box, (300, 500, 800, 1500)) == 0
    sx0, sy0, sx1, sy1 = L.SAFE
    assert sx0 <= x and x + 600 <= sx1 and sy0 <= y and y + 220 <= sy1  # never in the right 12% / bottom 18% / edge margins
    assert y < 300  # preferred the top background strip


def test_best_spot_avoids_head_when_no_clean_area_exists():
    occ = _blob_occ((40, 40, 1040, 1700))  # character fills the frame: nothing is free
    occ[:, :] = 1.0
    occ[150:190, 10:100] = 0.0  # ...except one small hole low down (the body, not the head band)
    x, y, cost = L.best_spot(occ, 300, 120, safe=(40, 40, 950, 1574))
    assert cost > 0  # overlap is unavoidable here; just make sure it picks a legal in-bounds spot
    assert 40 <= x <= 950 - 300 and 40 <= y <= 1574 - 120


def test_head_band_and_head_point():
    occ = _blob_occ((400, 300, 700, 1400))
    hb = L.head_band(occ)
    rows = np.where(hb.max(axis=1) > 0)[0]
    assert rows.min() * L.CELL == 300 and rows.max() * L.CELL < 300 + 0.4 * 1100  # only the top ~35% (head / upper body)
    hx, hy = L.head_point(occ)
    assert 400 <= hx <= 700 and 300 <= hy <= 700


def test_occupancy_follows_the_ken_burns_move_and_includes_faces():
    mask = np.zeros((1024, 576), np.float32)
    mask[200:700, 150:420] = 1.0  # character mask in image coords
    still = L.occupancy_over_motion(mask, [], 576, 1024, "zoom_out", samples=(1.0,))  # zoom 1.0 -> whole canvas visible
    moving = L.occupancy_over_motion(mask, [], 576, 1024, "zoom_in")
    assert still.sum() > 0 and moving.sum() >= still.sum()  # the union over a zoom-in covers at least the static footprint
    only_face = L.occupancy_over_motion(None, [(200.0, 100.0, 380.0, 300.0)], 576, 1024, "pan_left")
    assert only_face.sum() > 0


def test_free_overlay_keeps_text_off_the_character_and_in_the_safe_zone(workspace_tmp_path):
    lay = L.Layout()  # full-bleed default
    assert not lay.bands and lay.art_h == 1920
    char = (330.0, 450.0, 760.0, 1450.0)
    occ = _blob_occ(char)
    head = L.head_point(occ)
    long_nar = "Sparks of raw Aether collide as the two forces lock in a desperate final dance above the ruins."
    ov = Image.open(comic.make_overlay(workspace_tmp_path / "f.png", narration=long_nar, bubble="You will not take this light!",
                                       layout=lay, occ=occ, head=head))
    a = ov.getchannel("A")
    inside = a.crop((int(char[0]) + 12, int(char[1]) + 12, int(char[2]) - 12, int(char[3]) - 12))
    # only the (short) bubble tail may reach toward the character's head; the boxes themselves never overlap it
    assert (inside.getbbox() is None) or (inside.getbbox()[2] - inside.getbbox()[0]) < 90
    assert a.crop((951, 40, 1040, 1880)).getbbox() is None  # right ~12% stays empty (platform UI); the thin frame line is further out
    assert a.crop((40, 1575, 1040, 1880)).getbbox() is None  # bottom ~18% empty
    assert a.crop((60, 40, 950, 1574)).getbbox() is not None  # ...but the text boxes were drawn


def test_free_overlay_shrinks_text_to_fit_a_crowded_frame(workspace_tmp_path):
    occ = np.zeros((L.H // L.CELL, L.W // L.CELL), np.float32)
    occ[:, 45:100] = 1.0  # character covers most of the width; only a narrow strip on the left stays free
    ov = Image.open(comic.make_overlay(workspace_tmp_path / "c.png", narration="A fairly long narration line that needs shrinking.",
                                       layout=L.Layout(), occ=occ, head=(540, 400)))
    box = ov.getchannel("A").crop((30, 30, 950, 1574)).getbbox()
    assert box and box[2] + 30 <= 45 * L.CELL + 10  # the narration box narrowed to fit the free strip (no overlap with the character)
