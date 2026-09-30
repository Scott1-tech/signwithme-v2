"""Signature image clean-up: background removal and crop-to-ink.

Every synthetic image is built from a *known* ink mask, so "how much paper is
left" and "how much ink survived" are measured, not eyeballed. The final PDF
rests the image's bottom edge on the signature line, so the crop's bottom edge
is checked against the true lowest ink row.
"""
from __future__ import annotations

import base64
import io
import random

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.services import signature_image as si
from tests.conftest import auth

W, H = 600, 220
INK = (25, 30, 90)


def _mask(seed: int = 5, width: int = 5) -> Image.Image:
    rnd = random.Random(seed)
    mask = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(mask)
    points = [
        (80 + i * 7, int(90 + 25 * ((i % 7) - 3) / 3 + rnd.randint(-4, 4))) for i in range(60)
    ]
    draw.line(points, fill=255, width=width, joint="curve")
    # The underline flourish is the lowest ink.
    draw.line([(100, 160), (480, 150)], fill=255, width=3)
    return mask


def _white(): return Image.new("RGB", (W, H), (255, 255, 255))


def _phone():
    """Grey paper 200..228 with a lighting gradient, vignette, soft hand shadow, noise."""
    ramp = Image.linear_gradient("L").rotate(90).resize((W, H)).point(lambda v: int(200 + v * 28 / 255))
    vignette = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * 20 / 255))
    base = ImageChops.subtract(ramp, vignette)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).ellipse((-150, 100, 250, 440), fill=40)
    base = ImageChops.subtract(base, shadow.filter(ImageFilter.GaussianBlur(40)))
    base = ImageChops.add(base, Image.effect_noise((W, H), 4), 1, -128)
    return Image.merge(
        "RGB", (base, base.point(lambda v: max(0, v - 3)), base.point(lambda v: max(0, v - 10)))
    )


def _cream():
    noise = Image.effect_noise((W, H), 2)
    return Image.merge(
        "RGB", [ImageChops.add(Image.new("L", (W, H), c), noise, 1, -128) for c in (230, 225, 210)]
    )


def _scan():
    base = ImageChops.add(Image.new("L", (W, H), 245), Image.effect_noise((W, H), 4), 1, -128)
    return Image.merge("RGB", (base, base, base))


def _compose(background: Image.Image, mask: Image.Image, ink=INK) -> Image.Image:
    return Image.composite(Image.new("RGB", (W, H), ink), background, mask.filter(ImageFilter.GaussianBlur(1)))


def _round_trip(image: Image.Image, fmt: str, **kwargs) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGBA")


def _count(image: Image.Image) -> int:
    return image.histogram()[255]


def _binary(image: Image.Image, threshold: int) -> Image.Image:
    return image.point(lambda v: 255 if v >= threshold else 0)


CASES = {
    "white_png": (_white, "PNG", {}),
    "phone_gradient_shadow_jpeg": (_phone, "JPEG", {"quality": 88}),
    "cream_jpeg": (_cream, "JPEG", {"quality": 85}),
    "scan_noise_jpeg_q70": (_scan, "JPEG", {"quality": 70}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_background_is_removed_and_ink_is_kept(case):
    make_background, fmt, options = CASES[case]
    mask = _mask()
    source = _round_trip(_compose(make_background(), mask), fmt, **options)
    assert source.getchannel("A").getextrema() == (255, 255), "precondition: fully opaque input"

    knocked = si.remove_background(source)
    assert knocked is not None
    alpha = knocked.getchannel("A")

    background = mask.filter(ImageFilter.MaxFilter(9)).point(lambda v: 0 if v else 255)
    residual = _count(ImageChops.darker(_binary(alpha, 1), background)) / _count(background)
    assert residual <= 0.01, f"{residual:.2%} of the paper is still opaque"

    core = _binary(mask, 128)
    opaque = _binary(alpha, 128)
    recall = _count(ImageChops.darker(opaque, core)) / _count(core)
    assert recall >= 0.95, f"ink recall {recall:.2%}"

    near_ink = _binary(mask.filter(ImageFilter.MaxFilter(5)), 1)
    precision = _count(ImageChops.darker(opaque, near_ink)) / max(1, _count(opaque))
    assert precision >= 0.95, f"ink precision {precision:.2%}"


@pytest.mark.parametrize("case", sorted(CASES))
def test_crop_bottom_edge_is_the_ink_bottom(case):
    make_background, fmt, options = CASES[case]
    mask = _mask()
    true_left, true_top, true_right, true_bottom = mask.getbbox()
    true_bottom -= 1  # getbbox is exclusive

    source = _round_trip(_compose(make_background(), mask), fmt, **options)
    knocked = si.remove_background(source)
    box = si.ink_box(knocked)
    assert box is not None
    assert abs(box[3] - true_bottom) <= 2
    assert abs(box[1] - true_top) <= 2

    result = si.prepare_signature_image(source)
    # Bottom row of the result carries ink: no padding under the strokes.
    last_row = result.getchannel("A").crop((0, result.height - 1, result.width, result.height))
    assert last_row.getextrema()[1] >= si.INK_ALPHA_THRESHOLD
    # Small margin on the other sides, and far smaller than the source.
    expected_height = box[3] - max(0, box[1] - si.CROP_MARGIN) + 1
    assert result.height == expected_height
    assert result.width <= (true_right - true_left) + 2 * si.CROP_MARGIN + 6
    assert result.mode == "RGBA"


def test_isolated_specks_do_not_stretch_the_crop():
    mask = _mask()
    clean = _round_trip(_compose(_white(), mask), "PNG")
    dusty = _compose(_white(), mask)
    draw = ImageDraw.Draw(dusty)
    for x, y in ((590, 212), (5, 205), (560, 8), (300, 215), (20, 20)):
        draw.rectangle((x, y, x + 2, y + 2), fill=(30, 30, 30))
    dusty = _round_trip(dusty, "PNG")

    clean_box = si.ink_box(si.remove_background(clean))
    dusty_box = si.ink_box(si.remove_background(dusty))
    # Two specks sit in the edge ring the paper-noise statistics come from, so
    # the soft threshold may move by a level and an anti-aliased edge row by
    # one pixel. What must not happen is the box reaching out to a speck
    # (which would move an edge by 5-190 px here).
    assert all(abs(d - c) <= 1 for d, c in zip(dusty_box, clean_box)), (dusty_box, clean_box)


def test_a_thin_descender_tail_is_not_cut_off():
    """A 2 px pen stroke is the lowest ink; the projection must follow it down."""
    mask = _mask(width=3)
    ImageDraw.Draw(mask).line([(300, 110), (318, 200)], fill=255, width=2)
    true_bottom = mask.getbbox()[3] - 1
    source = _round_trip(_compose(_phone(), mask, ink=(40, 40, 40)), "JPEG", quality=88)
    box = si.ink_box(si.remove_background(source))
    assert abs(box[3] - true_bottom) <= 2


def test_an_image_with_transparency_is_cropped_but_not_rethresholded(monkeypatch):
    mask = _mask()
    pad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pad.paste((20, 20, 20, 255), (0, 0), mask.filter(ImageFilter.GaussianBlur(1)))

    def must_not_run(_image):
        raise AssertionError("background removal ran on an already transparent image")

    monkeypatch.setattr(si, "remove_background", must_not_run)
    result = si.prepare_signature_image(pad)

    box = si.ink_box(pad)
    left, top = max(0, box[0] - si.CROP_MARGIN), max(0, box[1] - si.CROP_MARGIN)
    expected = pad.crop((left, top, min(W, box[2] + si.CROP_MARGIN + 1), box[3] + 1))
    # Alpha (the client's own anti-aliasing) is preserved exactly.
    assert list(result.getchannel("A").getdata()) == list(expected.getchannel("A").getdata())


def test_blank_paper_is_left_as_it_was():
    """No ink found: nothing is knocked out or cropped (no silent empty image)."""
    blank = _round_trip(_cream(), "JPEG", quality=85)
    result = si.prepare_signature_image(blank)
    assert result.size == blank.size
    assert result.getchannel("A").getextrema() == (255, 255)


def test_an_image_that_is_mostly_dark_is_not_treated_as_ink_on_paper():
    dark = Image.new("RGB", (W, H), (20, 20, 20))
    ImageDraw.Draw(dark).rectangle((0, 0, 120, H), fill=(240, 240, 240))
    result = si.prepare_signature_image(_round_trip(dark, "PNG"))
    assert result.size == (W, H)
    assert result.getchannel("A").getextrema() == (255, 255)


def test_colour_under_fully_transparent_pixels_is_not_stored():
    """The photographed page around a signature is invisible after knockout;
    it must not survive in the RGB channels either."""
    source = _round_trip(_compose(_phone(), _mask()), "JPEG", quality=88)
    result = si.prepare_signature_image(source)
    hidden = [px for px in result.getdata() if px[3] == 0]
    assert hidden, "precondition: some transparent pixels"
    assert all(px == (0, 0, 0, 0) for px in hidden)


def _luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def test_knocked_out_ink_leaves_no_pale_halo_over_black():
    """Stroke-edge pixels once kept the paper-mixed colour (~150 luma); with
    partial alpha they lightened black text under the signature. Over black,
    no pixel may show brighter than the ink itself would at that alpha."""
    mask = _mask()
    source = _round_trip(_compose(_phone(), mask), "JPEG", quality=88)
    knocked = si.remove_background(source)
    ring = mask.filter(ImageFilter.MaxFilter(7)).getdata()  # ink + 3 px
    ink_luma = _luma(*INK)
    fringe, excess = [], []
    for (r, g, b, a), near in zip(knocked.getdata(), ring):
        if a == 0 or not near:
            continue
        excess.append((_luma(r, g, b) - ink_luma) * a / 255)
        if a < 255:
            fringe.append(_luma(r, g, b))
    assert len(fringe) > 500, "precondition: a soft alpha edge exists"
    assert max(excess) <= 15, f"pixel {max(excess):.0f} levels brighter than ink over black"
    fringe.sort()
    assert fringe[len(fringe) // 2] <= ink_luma + 10, f"fringe median luma {fringe[len(fringe) // 2]:.0f}"


def test_coloured_ink_keeps_its_colour_at_the_edges():
    ink = (90, 110, 190)  # light blue on cream must not turn grey or black
    source = _round_trip(_compose(_cream(), _mask(), ink=ink), "JPEG", quality=85)
    knocked = si.remove_background(source)
    fringe = [px[:3] for px in knocked.getdata() if 0 < px[3] < 255]
    assert len(fringe) > 500
    for channel in range(3):
        values = sorted(px[channel] for px in fringe)
        median = values[len(values) // 2]
        assert abs(median - ink[channel]) <= 12, (channel, median, ink)


def test_defringe_does_not_change_alpha(monkeypatch):
    source = _round_trip(_compose(_phone(), _mask()), "JPEG", quality=88)
    defringed = si.remove_background(source)
    monkeypatch.setattr(si, "_defringe", lambda rgb, paper, distance, alpha: rgb)
    raw = si.remove_background(source)
    assert list(defringed.getchannel("A").getdata()) == list(raw.getchannel("A").getdata())
    assert defringed.tobytes() != raw.tobytes(), "precondition: the colour did change"


def test_a_drawn_transparent_pad_signature_is_not_recoloured():
    """Pad captures already store ink colour under their soft edges; they are
    cropped only, every RGBA value of the crop is kept."""
    mask = _mask()
    pad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pad.paste((20, 20, 20, 255), (0, 0), mask.filter(ImageFilter.GaussianBlur(1)))
    pad = _round_trip(pad, "PNG")
    result = si.prepare_signature_image(pad)
    box = si.ink_box(pad)
    left, top = max(0, box[0] - si.CROP_MARGIN), max(0, box[1] - si.CROP_MARGIN)
    expected = pad.crop((left, top, min(W, box[2] + si.CROP_MARGIN + 1), box[3] + 1))
    assert result.tobytes() == expected.tobytes()


def test_a_phone_photo_upload_is_stored_transparent_and_cropped(api, contractor_token):
    source = _compose(_phone(), _mask())
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=88)
    response = api.post(
        "/api/signatures",
        headers=auth(contractor_token),
        json={
            "image_data": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(),
            "is_default": False,
        },
    )
    assert response.status_code == 201, response.text
    stored = Image.open(
        io.BytesIO(
            api.get(
                f"/api/signatures/{response.json()['id']}/image", headers=auth(contractor_token)
            ).content
        )
    )
    assert stored.format == "PNG" and stored.mode == "RGBA"
    assert stored.width < W and stored.height < H
    alpha = stored.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    transparent_share = alpha.histogram()[0] / (stored.width * stored.height)
    assert transparent_share > 0.5
