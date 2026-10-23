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


def test_detect_faces_falls_back_to_a_subject_box_and_overlay_avoids_it(workspace_tmp_path):
    img = Image.new("RGB", (576, 1024), (40, 60, 90))
    d = ImageDraw.Draw(img)
    for k in range(40):  # busy, edge-dense blob in the middle = the "subject"
        d.rectangle([180 + (k % 8) * 25, 380 + (k // 8) * 30, 195 + (k % 8) * 25, 400 + (k // 8) * 30], fill=(255, 255, 255) if k % 2 else (0, 0, 0))
    p = workspace_tmp_path / "p.png"
    img.save(p)
    faces = L.detect_faces(p)
    assert faces and all(0 <= f[0] < f[2] <= 576 and 0 <= f[1] < f[3] <= 1024 for f in faces)
    face = (300.0, 700.0, 780.0, 1180.0)  # explicit face box in final-frame coordinates
    ov = Image.open(comic.make_overlay(workspace_tmp_path / "o.png", narration="A short narration line here.",
                                       bubble="Hello there!", faces=[face]))
    assert ov.size == (1080, 1920)
    inside = ov.crop((310, 710, 770, 1170)).getchannel("A")  # nothing opaque (bubble/tail/narration) on the face
    assert max(inside.getdata()) < 10
    assert max(ov.getchannel("A").crop((100, 100, 980, 600)).getdata()) > 200  # ...but the text boxes were drawn elsewhere
