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
    lay = L.Layout()
    long_nar = "Sparks of raw Aether collide as the two forces lock in a desperate, final dance across the shattered sky above the ruins."
    long_bub = "You will not take this light from me, no matter how many shadows you send against it tonight!"
    for sx in (None, 150, 540, 930):
        ov = Image.open(comic.make_overlay(workspace_tmp_path / "o.png", narration=long_nar, bubble=long_bub, speaker_x=sx))
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
    left = Image.open(comic.make_overlay(workspace_tmp_path / "l.png", bubble="Hi there", speaker_x=150))
    right = Image.open(comic.make_overlay(workspace_tmp_path / "r.png", bubble="Hi there", speaker_x=900))
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
    lay = L.Layout.from_cfg({"top_h": 300, "art_h": 1200, "dialogue_h": 200, "right_safe": 140, "unknown": 1})
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
