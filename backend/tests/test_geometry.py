from app.services.geometry import BBox, rotate_for_page


def test_from_pdfplumber_converts_to_width_height():
    box = BBox.from_pdfplumber(10, 20, 110, 40)
    assert (box.x, box.y, box.width, box.height) == (10, 20, 100, 20)


def test_to_reportlab_flips_the_origin():
    """A box 20pt from the top of a 792pt page sits 752pt from the bottom.

    Getting this wrong mirrors every signature vertically.
    """
    box = BBox(x=100, y=20, width=150, height=20)
    assert box.to_reportlab(792.0) == (100, 752.0, 150, 20)


def test_reportlab_round_trip_is_stable():
    box = BBox(x=72, y=300, width=200, height=40)
    x, y, width, height = box.to_reportlab(792.0)
    back = BBox(x=x, y=792.0 - y - height, width=width, height=height)
    assert back == box


def test_from_dict_accepts_the_legacy_corner_form():
    assert BBox.from_dict({"x0": 10, "y0": 20, "x1": 110, "y1": 40}) == BBox(10, 20, 100, 20)


def test_rotation_90_swaps_axes():
    box = BBox(x=10, y=20, width=100, height=20)
    rotated = rotate_for_page(box, 612.0, 792.0, 90)
    assert rotated.width == 20 and rotated.height == 100


def test_overlaps():
    a = BBox(0, 0, 10, 10)
    assert a.overlaps(BBox(5, 5, 10, 10))
    assert not a.overlaps(BBox(20, 20, 5, 5))
