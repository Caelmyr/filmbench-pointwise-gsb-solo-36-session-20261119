"""Locate the film cell immediately beside the current crop.

The strip is read along the scan's horizontal axis.  An inter-frame gap shows
up as a band of clear film base (close to the sampled base colour in every
channel).  Window mullions and scratches produce narrow clear bands too, so
the search is deliberately conservative:

* only columns that are clear over most of the scan height count as a gap,
  measured against the light table's own column-by-column brightness trend,
* fragments split by a scratch are re-joined, but only when each fragment is
  itself fully clear and the joint stays gap-narrow, so a mullion never merges
  into a real gap,
* the neighbouring cell must be bounded by two such gaps about one cell apart
  and must enclose picture content,
* the far gap must also be corroborated by the next gap in the lattice or by
  the end of the strip.

When no credible pair of borders is found the existing crop is left alone.
"""

import numpy as np
from scipy import ndimage as ndi

from image_lab.validation import Invalid


CLEAR_LOW = .90
CLEAR_HIGH = 1.12
DIVIDER_CLEAR = .82
SCRATCH_CLEAR = .90
SCRATCH_REACH = .22
GAP_LIMIT = .45
WIDTH_LOW = .62
WIDTH_HIGH = 1.55
REACH = .20
DARK_LEVEL = .82
WEIGHTS = np.array([.2126, .7152, .0722], dtype=np.float32)


def _column_trend(patch):
    """Smooth per-column brightness trend of the light table, as a base multiplier."""
    height, width = patch.shape[:2]
    bright = np.percentile(patch, 97, axis=0)
    bins = max(8, min(120, width // 40))
    edges = np.linspace(0, width, bins + 1).astype(int)
    centres, levels = [], []
    for start, end in zip(edges[:-1], edges[1:]):
        if end - start < 2:
            continue
        column = bright[start:end]
        keep = max(1, len(column) // 3)
        centres.append((start + end - 1) / 2)
        levels.append(float(np.mean(np.sort(column)[-keep:])))
    trend = np.polyval(np.polyfit(np.asarray(centres), np.asarray(levels), 2),
                       np.arange(width))
    return np.maximum(trend, 1e-6)


def _clear_fraction(patch, base):
    """Share of the film height that is clear base, column by column."""
    factor = _column_trend(patch) / max(float(base @ WEIGHTS), 1e-6)
    ratio = patch / factor[None, :, None] / np.maximum(base, 1e-6)
    clear = np.all((ratio >= CLEAR_LOW) & (ratio <= CLEAR_HIGH), axis=2)
    return clear.mean(axis=0, dtype=np.float32)


def _bands(clearance, occlusion, frame_width):
    """Full-height clear columns, re-joined across thin scratches."""
    mask = ndi.median_filter(clearance, size=3, mode='nearest') >= DIVIDER_CLEAR
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    bands = []
    for start, end in zip(edges[::2], edges[1::2]):
        columns = clearance[start:end]
        bands.append({'start': int(start), 'end': int(end),
                      'clearance': float(np.median(columns)),
                      'fill': float(np.mean(columns >= DIVIDER_CLEAR))})
    joined = []
    for band in bands:
        previous = joined[-1] if joined else None
        if previous is not None and band['start'] - previous['end'] <= occlusion:
            # A scratch only separates two otherwise fully clear fragments of one
            # narrow gap; picture content between two gaps is a whole cell wide.
            if (min(previous['clearance'], band['clearance']) >= SCRATCH_CLEAR
                    and band['end'] - previous['start'] <= GAP_LIMIT * frame_width):
                previous['end'] = band['end']
                previous['clearance'] = min(previous['clearance'], band['clearance'])
                previous['fill'] = min(previous['fill'], band['fill'])
                continue
        joined.append(band)
    for band in joined:
        band['width'] = band['end'] - band['start']
    return joined


def _profile(linear, base, crop):
    x0, y0, x1, y1 = crop
    height, width = linear.shape[:2]
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise Invalid('当前画格框过小，无法沿胶片查找邻格')
    full_step = max(1, height // 640)
    clearance = _clear_fraction(linear[::full_step], base)
    occlusion = max(3, round((x1 - x0) * SCRATCH_REACH))
    return _bands(clearance, occlusion, x1 - x0)


def _band_at(bands, edge, direction, frame_width):
    """Gap band touching the crop edge on the requested side, if any."""
    tolerance = max(6, round(frame_width * REACH))
    best = None
    for band in bands:
        if direction > 0:
            if band['start'] > edge + tolerance or band['end'] < edge - 2:
                continue
            distance = max(0, band['start'] - edge, edge - band['end'])
        else:
            if band['end'] < edge - tolerance or band['start'] > edge + 2:
                continue
            distance = max(0, edge - band['end'], band['start'] - edge)
        key = (distance, -band['clearance'])
        if best is None or key < best[0]:
            best = (key, band)
    return best[1] if best else None


def _inner_edge(band, direction):
    return band['end'] if direction > 0 else band['start']


def _content_ok(linear, base, left, right, y0, y1):
    width = right - left
    height = y1 - y0
    if width < 8 or height < 8:
        return False
    top = y0 + round(height * .14)
    bottom = y1 - round(height * .14)
    inset = max(1, round(width * .03))
    row_step = max(1, (bottom - top) // 90)
    column_step = max(1, (width - 2 * inset) // 120)
    patch = linear[top:bottom:row_step, left + inset:right - inset:column_step]
    if patch.size == 0:
        return False
    ratio = patch / np.maximum(base, 1e-6)
    clear = np.all((ratio >= CLEAR_LOW) & (ratio <= CLEAR_HIGH), axis=2)
    luminance = patch @ WEIGHTS
    base_luminance = float(base @ WEIGHTS)
    return float(clear.mean()) <= .55 and float(np.mean(luminance < DARK_LEVEL * base_luminance)) >= .03


def _lattice_ok(band, bands, direction, width, scan_width):
    """The gap beyond the cell, or the strip end, confirms this is a frame border."""
    remaining = scan_width - band['end'] if direction > 0 else band['start']
    if remaining <= max(8, round(width * REACH)):
        return 'scan-edge'
    later = [item for item in bands
             if (item['start'] >= band['end'] if direction > 0 else item['end'] <= band['start'])]
    if later:
        following = min(later, key=lambda item: item['start']) if direction > 0 \
            else max(later, key=lambda item: item['end'])
        span = following['start'] - band['end'] if direction > 0 else band['start'] - following['end']
        if WIDTH_LOW <= span / width <= WIDTH_HIGH:
            return 'next-gap'
    return None


def find_adjacent(linear, base, crop, direction):
    """Return the neighbouring cell bounds one cell to the ``direction`` side."""
    x0, y0, x1, y1 = crop
    scan_width = linear.shape[1]
    frame_width = x1 - x0
    bands = _profile(linear, base, crop)

    target_edge = x1 if direction > 0 else x0
    side = '右' if direction > 0 else '左'
    near = _band_at(bands, target_edge, direction, frame_width)
    if near is None:
        raise Invalid(f'当前框的{side}侧没有找到可信的格间边，已保留原框。')

    divider = _inner_edge(near, direction)
    ordered = sorted(bands, key=lambda item: item['start']) if direction > 0 \
        else sorted(bands, key=lambda item: -item['start'])
    for band in ordered:
        if direction > 0 and band['start'] <= divider:
            continue
        if direction < 0 and band['end'] >= divider:
            continue
        span = band['start'] - divider if direction > 0 else divider - band['end']
        ratio = span / frame_width
        if not WIDTH_LOW <= ratio <= WIDTH_HIGH or band['start'] == band['end']:
            continue
        right_edge = _inner_edge(band, -direction)
        left, right = (divider, right_edge) if direction > 0 else (right_edge, divider)
        if left < 0 or right > scan_width:
            continue
        if not _content_ok(linear, base, left, right, y0, y1):
            continue
        evidence = _lattice_ok(band, bands, direction, frame_width, scan_width)
        if evidence is None:
            continue
        return {
            'crop': [int(left), int(y0), int(right), int(y1)],
            'side': side, 'target_width': int(span), 'frame_width': int(frame_width),
            'ratio': round(float(ratio), 3), 'border_evidence': evidence,
            'divider': [int(near['start']), int(near['end'])],
            'border': [int(band['start']), int(band['end'])],
        }

    raise Invalid(f'{side}侧没有找到可信的一对格间边，已保留原框。')
