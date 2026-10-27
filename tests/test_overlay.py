import numpy as np
import pytest

from facerec import overlay as ov


def blank(h=240, w=320) -> np.ndarray:
    return np.full((h, w, 3), 80, np.uint8)


def changed(a, b) -> int:
    return int(np.any(a != b, axis=2).sum())


# --- text -------------------------------------------------------------------------------------


def test_accents_are_rendered_not_dropped():
    plain, accented = ov.text_sprite("ecran", 20, ov.WHITE), ov.text_sprite("écran", 20, ov.WHITE)
    assert accented.shape[0] >= plain.shape[0]
    n = min(plain.shape[1], accented.shape[1])
    assert np.any(plain[:, :n, 3] != accented[:plain.shape[0], :n, 3]) or accented.shape != plain.shape


def test_every_french_character_has_ink():
    for text in "éèêëàâîïôùûüçœ’«»…·":
        assert ov.text_sprite(text, 20, ov.WHITE)[:, :, 3].max() > 0, text


def test_text_is_drawn_in_the_requested_color():
    frame = blank()
    ov.put_text(frame, "HHHH", 10, 10, 30, ov.OK, bold=True)
    drawn = frame[np.any(frame != 80, axis=2)]
    assert len(drawn) > 50
    assert abs(int(drawn[:, 1].max()) - ov.OK[1]) < 40  # the green channel dominates where text is solid


def test_sprites_are_cached():
    assert ov.text_sprite("Porte fermée", 17, ov.WHITE, True) is ov.text_sprite("Porte fermée", 17, ov.WHITE, True)


def test_fit_text_shortens_with_an_ellipsis_and_keeps_short_text():
    assert ov.fit_text("Camille", 200) == "Camille"
    short = ov.fit_text("Une très longue phrase qui ne tient pas dans la largeur demandée", 120)
    assert short.endswith("…") and ov.text_width(short) <= 120


def test_empty_text_does_not_crash():
    ov.put_text(blank(), "", 5, 5)


# --- blending and clipping ---------------------------------------------------------------------


@pytest.mark.parametrize("x, y", [(-50, -10), (300, 220), (-500, 5), (5, 900), (0, 0)])
def test_drawing_partly_or_fully_outside_the_frame_is_safe(x, y):
    frame = blank()
    ov.put_text(frame, "Bonjour", x, y, 20)
    ov.rounded_rect(frame, x, y, x + 100, y + 40, ov.OK, 0.5)
    ov.chip(frame, x, y, "Autorisé", ov.OK)
    assert frame.shape == (240, 320, 3)


def test_translucent_rectangles_blend_with_the_background():
    frame = blank()
    ov.rounded_rect(frame, 50, 50, 150, 100, (0, 0, 0), 0.5)
    centre = frame[75, 100]
    assert 30 < centre[0] < 50  # halfway between 80 and 0
    assert (frame[10, 10] == 80).all()  # nothing outside


def test_blit_changes_only_its_own_area():
    frame, before = blank(), blank()
    ov.put_text(frame, "Test", 100, 100, 20)
    ys, xs = np.nonzero(np.any(frame != before, axis=2))
    assert ys.min() >= 98 and xs.min() >= 98 and xs.max() < 180


# --- components -----------------------------------------------------------------------------------


def test_chip_returns_a_size_and_respects_max_width():
    frame = blank()
    w, h = ov.chip(frame, 10, 10, "Autorisé", ov.OK)
    assert w > 40 and h > 15 and (frame[10 + h // 2, 10 + w // 2] != 80).any()
    narrow, _ = ov.chip(blank(), 10, 10, "Vérification de présence non réussie", ov.BAD, max_width=140)
    assert narrow <= 140


def test_meter_fills_proportionally():
    for fraction, expect in ((0.0, 0), (0.5, 100), (1.0, 200), (2.0, 200), (-1.0, 0)):
        frame = blank()
        ov.meter(frame, 20, 100, 200, fraction, (0, 255, 0))
        filled = int(((frame[103] == (0, 255, 0)).all(axis=1)).sum())
        assert abs(filled - expect) <= 8, (fraction, filled)


def test_ring_arc_grows_with_the_fraction():
    def lit(fraction):
        frame = blank()
        ov.ring(frame, (160, 120), 40, fraction, (0, 255, 0))
        return int((frame == (0, 255, 0)).all(axis=2).sum())

    assert lit(0.0) == 0 < lit(0.25) < lit(0.5) < lit(1.0)


def test_arrows_point_to_their_side():
    left, right = blank(), blank()
    ov.arrow(left, (160, 120), -1, ov.ACCENT)
    ov.arrow(right, (160, 120), +1, ov.ACCENT)
    ys, xs = np.nonzero(np.any(left != 80, axis=2))
    yr, xr = np.nonzero(np.any(right != 80, axis=2))
    tip_left = xs[ys == 120].min() if (ys == 120).any() else xs.min()
    tip_right = xr[yr == 120].max() if (yr == 120).any() else xr.max()
    assert tip_left < 160 < tip_right
    moved = blank()
    ov.arrow(moved, (160, 120), 1, ov.ACCENT, pulse=1.0)
    assert changed(moved, right) > 0  # the pulse slides it


def test_steps_show_progress():
    frame = blank()
    ov.steps(frame, 30, 30, 1, 3, ov.OK)
    assert (frame[30, 30] == ov.OK).all()  # first step done: filled
    assert (frame[30, 30 + 44] != ov.OK).any()  # third: not done


def test_door_banner_differs_when_open_and_closed():
    closed, opened = blank(), blank()
    ov.door_banner(closed, False, 0, 5, "Laboratoire")
    ov.door_banner(opened, True, 3.2, 5, "Laboratoire")
    assert changed(closed, opened) > 100
    assert changed(closed, blank()) > 500


def test_hud_and_enrollment_panel_draw_without_error_on_small_frames():
    for shape in ((120, 160), (480, 640)):
        frame = blank(*shape)
        ov.hud(frame, 24.7, "yunet + sface · défi")
        ov.enrollment_panel(frame, "Camille Martin-Lefèvre", 7, 20, "Bougez légèrement la tête")
        assert changed(frame, blank(*shape)) > 200
