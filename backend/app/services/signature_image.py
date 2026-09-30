"""Signature image clean-up: background removal, then crop to the ink.

Called from ``app.api.deps._normalise_signature_image`` *after* every
validation gate (size, format, bomb, ``verify()``) has passed, on the decoded
RGBA image. Nothing here parses client bytes.

Why it exists: a photographed or scanned signature arrives fully opaque, and
its paper -- grey, cream, shaded, never quite white -- was drawn into the final
PDF as a box covering the signature line. A fixed "brighter than 235" knockout
leaves 100% of a phone photo's background in place, so the paper is found per
image instead:

1. **Illumination.** On a copy reduced to ``ANALYSIS_EDGE``, a morphological
   closing (max then min filter) erases strokes thinner than the kernel while
   keeping large structures (paper, shading, shadow edges) where they are. A
   blur and an upscale give a per-pixel estimate of what the paper looks like
   *at that pixel*, so a lighting gradient or a hand shadow is not ink.
2. **Distance.** How much darker than its local paper each pixel is, on the
   worst channel (blue ink on white differs mostly in red/green).
3. **Thresholds.** The noise of the paper is measured on an 8-16 px edge ring
   (median + MAD, robust against a stroke that touches the edge); the split
   between paper and ink is Otsu's threshold on the distance histogram. Alpha
   ramps softly between a lower and an upper bound instead of a hard cut, so
   anti-aliased stroke edges stay smooth.
4. **Defringe.** A knocked-out pixel keeps its alpha but not its photographed
   colour: a stroke-edge pixel is a paper/ink mixture (grey-blue ~150 luma for
   dark ink), and composited with partial alpha over a document it *lightens*
   the text underneath -- a pale halo. Solving ``P = a*ink + (1-a)*paper`` with
   the knocked alpha does not work, because the ramp is not the true coverage:
   pixels at the top of the ramp are fully opaque yet half paper. Instead the
   ink's reflectance per channel (ink / paper, median over the darkest half of
   the opaque pixels) is applied to the local paper estimate, which gives the
   ink colour at every pixel under that pixel's lighting, and each pixel is
   made no lighter than that on any channel. Coloured ink keeps its colour; a
   pixel already darker (the stroke core) is untouched.
5. **Crop.** Row and column ink projections; a row or column must carry a
   minimum amount of ink to anchor the box, and the box then grows only through
   *contiguous* inked rows/columns. Thin stroke tails are kept, an isolated
   speck is not. The bottom edge of the result is the lowest ink row exactly
   (the placement maths rests the image bottom on the signature line); the
   other three sides keep a ``CROP_MARGIN`` px margin.

An image that already carries meaningful transparency (a drawing-pad capture, a
client-side knockout) is not re-thresholded, but it is still cropped.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter

# Mirrors pdf_builder.INK_ALPHA_THRESHOLD: below this alpha a pixel is padding.
INK_ALPHA_THRESHOLD = 8
# An image with at least this share of not-fully-opaque pixels already has a
# background the client chose; it is cropped but not re-thresholded.
TRANSPARENT_ALPHA_MAX = 250
SIGNIFICANT_TRANSPARENCY = 0.02
# Illumination is estimated on a copy whose long edge is at most this. Paper
# lighting is smooth, so 256 px is enough, and the rank filters of the closing
# are the single most expensive step (measured: 605 ms at 512 px vs 4000 px source).
ANALYSIS_EDGE = 256
# Closing radius as a share of the analysis long edge: strokes up to about
# twice this wide are erased from the paper estimate.
CLOSING_SHARE = 0.03
# Edge ring used for the paper-noise statistics.
RING_MIN_PX = 8
RING_MAX_PX = 16
# Lower alpha bound = paper median + NOISE_SIGMAS * robust sigma + NOISE_FLOOR.
NOISE_SIGMAS = 6.0
NOISE_FLOOR = 4.0
# Minimum width of the soft alpha ramp, in distance levels.
MIN_RAMP = 24
# More than this share of opaque "ink" means the image is not ink on paper
# (a photo of something else, a dark desk): leave it untouched.
MAX_PLAUSIBLE_INK = 0.5
# Median luma of the edge ring below this: not light paper (see remove_background).
MIN_PAPER_LUMA = 96
CROP_MARGIN = 2
# Anchor rows/columns need at least max(CORE_MIN_PX, CORE_SHARE * busiest).
CORE_MIN_PX = 4
CORE_SHARE = 0.05


def prepare_signature_image(rgba: Image.Image) -> Image.Image:
    """Return ``rgba`` with its background removed (if opaque) and cropped to ink."""
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    if not _has_significant_transparency(rgba):
        knocked = remove_background(rgba)
        if knocked is not None:
            rgba = knocked
    return crop_to_ink(rgba)


def _has_significant_transparency(rgba: Image.Image) -> bool:
    histogram = rgba.getchannel("A").histogram()
    total = rgba.width * rgba.height
    return sum(histogram[: TRANSPARENT_ALPHA_MAX + 1]) >= SIGNIFICANT_TRANSPARENCY * total


def remove_background(rgba: Image.Image) -> Image.Image | None:
    """Knock the paper out of an opaque image. ``None`` when no plausible ink is found."""
    rgb = rgba.convert("RGB")
    # Signatures are dark ink on light paper. If the edge of the image is not
    # light, the "paper" is a desk, a dark photo or a negative: leave it alone
    # rather than knock out the wrong thing.
    if _hist_median(_ring_histogram(rgb.convert("L"))) < MIN_PAPER_LUMA:
        return None
    distance, paper = _darkness_map(rgb)

    ring = _ring_histogram(distance)
    median = _hist_median(ring)
    mad = _hist_mad(ring, median)
    sigma = max(1.4826 * mad, 1.0)
    noise_high = median + NOISE_SIGMAS * sigma + NOISE_FLOOR

    histogram = distance.histogram()
    otsu = _otsu(histogram)
    low = max(noise_high, 0.5 * otsu)
    high = max(float(otsu), low + MIN_RAMP)
    if low >= 254:
        return None
    high = min(high, 255.0)

    lut = []
    for value in range(256):
        if value <= low:
            alpha = 0
        elif value >= high:
            alpha = 255
        else:
            alpha = round(255 * (value - low) / (high - low))
        lut.append(alpha if alpha >= INK_ALPHA_THRESHOLD else 0)
    alpha = distance.point(lut)

    alpha_histogram = alpha.histogram()
    total = rgba.width * rgba.height
    inked = sum(alpha_histogram[INK_ALPHA_THRESHOLD:])
    if inked == 0 or sum(alpha_histogram[128:]) > MAX_PLAUSIBLE_INK * total:
        return None
    result = _defringe(rgb, paper, distance, alpha)
    result.putalpha(alpha)
    return result


def _defringe(
    rgb: Image.Image, paper: Image.Image, distance: Image.Image, alpha: Image.Image
) -> Image.Image:
    """RGB no lighter than the ink colour under the local lighting (alpha untouched)."""
    box = alpha.getbbox()
    if box is None:
        return rgb.copy()
    if box != (0, 0, rgb.width, rgb.height):
        # Outside the alpha box everything is invisible (and crop_to_ink clears
        # it); on a photographed page that is most of the pixels.
        result = rgb.copy()
        result.paste(_defringe(*(im.crop(box) for im in (rgb, paper, distance, alpha))), box[:2])
        return result
    opaque = alpha.point(lambda v: 255 if v == 255 else 0)
    opaque_distance = distance.histogram(opaque)
    if sum(opaque_distance) == 0:
        return rgb.copy()
    # The darkest half of the opaque pixels is stroke core, not ramp-top edge.
    core_from = _hist_median(opaque_distance)
    core = ImageChops.darker(opaque, distance.point(lambda v: 255 if v >= core_from else 0))
    ink_histogram = rgb.histogram(core)
    paper_histogram = paper.histogram(core)
    lut: list[int] = []
    for band in range(3):
        ink = _hist_median(ink_histogram[256 * band : 256 * (band + 1)])
        lit = _hist_median(paper_histogram[256 * band : 256 * (band + 1)])
        # Reflectance of the ink on this channel, as a 0..255 factor.
        reflectance = min(255, round(255 * ink / lit)) if lit else 255
        lut.extend(value * reflectance // 255 for value in range(256))
    return ImageChops.darker(rgb, paper.point(lut))


def _darkness_map(rgb: Image.Image) -> tuple[Image.Image, Image.Image]:
    """(L distance, RGB paper): how much darker than its local paper each pixel is."""
    width, height = rgb.size
    scale = min(1.0, ANALYSIS_EDGE / max(width, height))
    if scale < 1.0:
        small = rgb.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.BOX
        )
    else:
        small = rgb
    radius = max(1, round(max(small.size) * CLOSING_SHARE))
    paper = small
    # Iterated 3x3 rank filters == one (2r+1)-square filter, at a fraction of the cost.
    for _ in range(radius):
        paper = paper.filter(ImageFilter.MaxFilter(3))
    for _ in range(radius):
        paper = paper.filter(ImageFilter.MinFilter(3))
    paper = paper.filter(ImageFilter.GaussianBlur(max(1.0, radius / 2)))
    if paper.size != rgb.size:
        paper = paper.resize(rgb.size, Image.Resampling.BILINEAR)
    # One multi-band subtract (clipped at 0: only darker-than-paper counts),
    # then the worst channel.
    red, green, blue = ImageChops.subtract(paper, rgb).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue), paper


def _ring_histogram(gray: Image.Image) -> list[int]:
    width, height = gray.size
    ring = max(RING_MIN_PX, min(RING_MAX_PX, round(min(width, height) * 0.02)))
    ring = max(1, min(ring, min(width, height) // 4 or 1))
    boxes = [
        (0, 0, width, ring),
        (0, height - ring, width, height),
        (0, ring, ring, height - ring),
        (width - ring, ring, width, height - ring),
    ]
    histogram = [0] * 256
    for box in boxes:
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        for index, count in enumerate(gray.crop(box).histogram()):
            histogram[index] += count
    return histogram


def _hist_median(histogram: list[int]) -> int:
    half = sum(histogram) / 2
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= half:
            return value
    return 0


def _hist_mad(histogram: list[int], median: int) -> int:
    deviations = [0] * 256
    for value, count in enumerate(histogram):
        deviations[abs(value - median)] += count
    return _hist_median(deviations)


def _otsu(histogram: list[int]) -> int:
    total = sum(histogram)
    if total == 0:
        return 0
    sum_all = sum(value * count for value, count in enumerate(histogram))
    weight_back = 0
    sum_back = 0.0
    best_value, best_threshold = -1.0, 0
    for value, count in enumerate(histogram):
        weight_back += count
        if weight_back == 0:
            continue
        weight_fore = total - weight_back
        if weight_fore == 0:
            break
        sum_back += value * count
        mean_back = sum_back / weight_back
        mean_fore = (sum_all - sum_back) / weight_fore
        between = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if between > best_value:
            best_value, best_threshold = between, value
    return best_threshold


def ink_box(rgba: Image.Image) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the ink, inclusive, ignoring isolated specks."""
    mask = rgba.getchannel("A").point(lambda v: 255 if v >= INK_ALPHA_THRESHOLD else 0)
    raw_box = mask.getbbox()
    if raw_box is None:
        return None
    # Everything below works inside the raw ink box (plus a 1 px ring so the
    # neighbour test sees true zeros at its border): identical results, a
    # fraction of the pixels on a large photo.
    ox = max(0, raw_box[0] - 1)
    oy = max(0, raw_box[1] - 1)
    region = mask.crop(
        (ox, oy, min(mask.width, raw_box[2] + 1), min(mask.height, raw_box[3] + 1))
    )
    # A pixel with no inked 8-neighbour is sensor/JPEG noise, not a stroke.
    neighbours = region.filter(ImageFilter.BoxBlur(1)).point(lambda v: 255 if v >= 42 else 0)
    clean = ImageChops.darker(region, neighbours)
    if clean.getbbox() is None:
        return None
    width, height = clean.size
    as_float = clean.convert("F")
    row_counts = [v * width / 255 for v in as_float.resize((1, height), Image.Resampling.BOX).getdata()]
    col_counts = [v * height / 255 for v in as_float.resize((width, 1), Image.Resampling.BOX).getdata()]
    top, bottom = _span(row_counts)
    left, right = _span(col_counts)
    return left + ox, top + oy, right + ox, bottom + oy


def _span(counts: list[float]) -> tuple[int, int]:
    busiest = max(counts)
    minimum = max(CORE_MIN_PX, CORE_SHARE * busiest)
    core = [i for i, c in enumerate(counts) if c >= minimum - 1e-6]
    if not core:
        core = [i for i, c in enumerate(counts) if c >= 0.5]
    low, high = core[0], core[-1]
    while low > 0 and counts[low - 1] >= 0.5:
        low -= 1
    while high < len(counts) - 1 and counts[high + 1] >= 0.5:
        high += 1
    return low, high


def crop_to_ink(rgba: Image.Image) -> Image.Image:
    """Crop to the ink box; bottom edge = lowest ink row. Clears RGB under alpha 0."""
    box = ink_box(rgba)
    if box is not None:
        left, top, right, bottom = box
        rgba = rgba.crop(
            (
                max(0, left - CROP_MARGIN),
                max(0, top - CROP_MARGIN),
                min(rgba.width, right + CROP_MARGIN + 1),
                bottom + 1,
            )
        )
    # Whatever sits under fully transparent pixels (the rest of a photographed
    # page, say) is invisible but still stored; do not keep it.
    visible = rgba.getchannel("A").point(lambda v: 255 if v else 0)
    return Image.composite(rgba, Image.new("RGBA", rgba.size, (0, 0, 0, 0)), visible)


__all__ = ["prepare_signature_image", "remove_background", "crop_to_ink", "ink_box"]
