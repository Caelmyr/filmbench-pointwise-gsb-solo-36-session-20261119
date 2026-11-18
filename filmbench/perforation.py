import warnings

import cv2
import numpy as np

from image_lab.validation import Invalid


SIDES = ('top', 'bottom')


def reference_box(value, size):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(item) is not int for item in value)):
        raise Invalid('齿孔参照需要四个整数坐标')
    x0, y0, x1, y1 = value
    width, height = size
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        raise Invalid('齿孔参照越出原片')
    if x1 - x0 < 12 or y1 - y0 < 12:
        raise Invalid('齿孔参照过小，请框住几个完整孔和周围片边')
    return [x0, y0, x1, y1]


def side_value(value, reference, size):
    if value not in ('auto', *SIDES):
        raise Invalid('齿孔所在片边只能为上边、下边或自动')
    if value == 'auto':
        return 'top' if (reference[1] + reference[3]) / 2 < size[1] / 2 else 'bottom'
    return value


def gray_field(codes, maximum):
    return (codes.astype(np.float32) @ np.array([.2126, .7152, .0722], np.float32)
            / float(maximum))


def _otsu(patch):
    codes = np.rint(np.clip(patch, 0, 1) * 255).astype(np.uint8)
    level, _ = cv2.threshold(codes, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(level) / 255.0


def _tone_mask(patch, level, bright):
    return patch > level if bright else patch < level


def _holes_in_patch(mask, minimum, margin=0):
    height, width = mask.shape
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    holes = []
    for index in range(1, count):
        left, top, w, h, area = (int(value) for value in stats[index])
        if area < minimum or w < 3 or h < 3:
            continue
        # Only complete holes inside the reference box define the normal shape; a hole
        # clipped by the box edge, or an opening running out of it, is excluded.
        if (left <= margin or top <= margin
                or left + w >= width - margin or top + h >= height - margin):
            continue
        if w > width * .62 or h > height * .88 or not .2 <= w / h <= 5:
            continue
        holes.append({'w': w, 'h': h, 'area': area,
                      'cx': float(centroids[index][0]), 'cy': float(centroids[index][1])})
    return holes


def _median_pitch(holes, width):
    """Spacing between hole centres, robust to one or two holes hidden in the reference."""
    ordered = np.sort(np.array([hole['cx'] for hole in holes], np.float32))
    gaps = np.diff(ordered)
    if not len(gaps):
        return None
    gaps = gaps[gaps > .45 * np.median(gaps)]
    if not len(gaps):
        return None
    smallest = float(np.min(gaps))
    # A gap may span a missing neighbour; fold multiples of the smallest gap back.
    multiples = np.maximum(1, np.rint(gaps / smallest))
    folded = gaps / multiples
    pitch = float(np.median(folded))
    if not 1.05 * width <= pitch <= 5 * width:
        pitch = width * 1.7
    return pitch


def read_reference(patch):
    """Measure the normal hole size, spacing, tone and fill fraction of one row."""
    if min(patch.shape) < 8:
        raise Invalid('齿孔参照过小，请框住几个完整孔和周围片边')
    level = _otsu(patch)
    best = None
    for bright in (True, False):
        mask = _tone_mask(patch, level, bright)
        holes = _holes_in_patch(mask, max(16, patch.size * .0004),
                                max(1, min(patch.shape) // 40))
        if len(holes) < 2:
            continue
        widths = np.array([hole['w'] for hole in holes], np.float32)
        heights = np.array([hole['h'] for hole in holes], np.float32)
        keep = ((widths > .55 * np.median(widths)) & (widths < 1.8 * np.median(widths))
                & (heights > .55 * np.median(heights))
                & (heights < 1.8 * np.median(heights)))
        holes = [hole for hole, good in zip(holes, keep) if good]
        if len(holes) < 2:
            continue
        median_width = float(np.median([hole['w'] for hole in holes]))
        pitch = _median_pitch(holes, median_width)
        if pitch is None:
            continue
        fill = float(np.median([hole['area'] / (hole['w'] * hole['h']) for hole in holes]))
        score = len(holes) * fill - abs(pitch - 1.7 * median_width) / max(pitch, 1)
        if best is None or score > best['score']:
            best = {'bright': bright, 'holes': holes, 'fill': fill,
                    'pitch': pitch, 'score': score}
    if best is None or best['fill'] < .58:
        raise Invalid('参照框里没有稳定的完整齿孔，请框住一排中几个完整孔和周围片边')

    mask = _tone_mask(patch, level, best['bright'])
    inside = patch[mask]
    outside = patch[~mask]
    contrast = float(abs(np.median(inside) - np.median(outside))) \
        if inside.size and outside.size else 0.0
    if contrast < .022:
        raise Invalid('参照框里的孔和片基差别太小，请换一段更清楚的齿孔作参照')
    widths = np.array([hole['w'] for hole in best['holes']], np.float32)
    heights = np.array([hole['h'] for hole in best['holes']], np.float32)
    areas = np.array([hole['area'] for hole in best['holes']], np.float32)
    return {'bright': best['bright'], 'level': level, 'contrast': contrast,
            'base': float(np.median(outside)), 'hole': float(np.median(inside)),
            'count': len(best['holes']), 'holes': best['holes'],
            'width': float(np.median(widths)), 'height': float(np.median(heights)),
            'area': float(np.median(areas)), 'fill': float(best['fill']),
            'pitch': float(best['pitch'])}


def _base_line(region, base_tone, span):
    """Per-column film-base level; rows matching hole tone (holes, edge print) are ignored."""
    close = np.abs(region - base_tone) < .55 * span
    values = np.where(close, region, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        line = np.nanmedian(values, axis=0)
    line = np.where(np.isfinite(line), line, base_tone).astype(np.float32)
    if line.size >= 5:
        line = cv2.medianBlur(line.reshape(1, -1), 5).ravel()
    return line


def _row_curve(comps, reference_centers, hole_height):
    """Fit the curved row from normal holes plus the reference holes themselves."""
    points = [((c['left'] + c['right']) / 2, (c['top'] + c['bottom']) / 2) for c in comps]
    points += reference_centers
    fallback = float(np.median([point[1] for point in reference_centers])) \
        if reference_centers else 0.0
    if len(points) < 3:
        return np.poly1d([fallback]), 0.0
    degree = 2 if len(points) >= 6 else 1
    xs = np.array([point[0] for point in points], np.float32)
    ys = np.array([point[1] for point in points], np.float32)
    if np.ptp(xs) < 1:
        return np.poly1d([fallback]), 0.0
    coefficient = np.polyfit(xs, ys, degree)
    keep = np.ones(len(xs), bool)
    for _ in range(3):
        residual = ys - np.polyval(coefficient, xs)
        keep = np.abs(residual) < max(1.1 * hole_height, 6)
        if keep.sum() < max(3, degree + 1):
            return np.poly1d([fallback]), 0.0
        coefficient = np.polyfit(xs[keep], ys[keep], degree)
    residual = np.abs(ys[keep] - np.polyval(coefficient, xs[keep]))
    return np.poly1d(coefficient), float(np.percentile(residual, 90))


def _adjacent(first, second, pitch):
    gap = second['bounds'][0] - first['bounds'][2]
    overlap = (min(first['bounds'][3], second['bounds'][3])
               - max(first['bounds'][1], second['bounds'][1]))
    shortest = min(first['bounds'][3] - first['bounds'][1],
                   second['bounds'][3] - second['bounds'][1])
    return gap < .28 * pitch and overlap > .45 * shortest


def _group(items, pitch):
    """Join damaged pieces of one continuous break, including a ragged bridge."""
    items = sorted(items, key=lambda item: item['bounds'][0])
    grouped = []
    for item in items:
        if grouped and _adjacent(grouped[-1], item, pitch):
            last = grouped[-1]
            last['bounds'] = [min(last['bounds'][0], item['bounds'][0]),
                              min(last['bounds'][1], item['bounds'][1]),
                              max(last['bounds'][2], item['bounds'][2]),
                              max(last['bounds'][3], item['bounds'][3])]
            last['holes'] += item['holes']
            last['kind'] = 'merged' if 'merged' in (last['kind'], item['kind']) else 'torn'
            last['width_ratio'] = max(last['width_ratio'], item['width_ratio'])
            last['height_ratio'] = max(last['height_ratio'], item['height_ratio'])
            last['area_ratio'] += item['area_ratio']
            last['fill_ratio'] = min(last['fill_ratio'], item['fill_ratio'])
        else:
            grouped.append(dict(item))
    for item in grouped:
        if item['holes'] >= 2:
            item['kind'] = 'merged'
    return grouped


def _touch_border(bounds, size):
    left, top, right, bottom = bounds
    width, height = size
    return left <= 1 or top <= 1 or right >= width - 1 or bottom >= height - 1


def inspect(codes, maximum, reference, side='auto'):
    size = (codes.shape[1], codes.shape[0])
    x0, y0, x1, y1 = reference_box(reference, size)
    gray = gray_field(codes, maximum)
    stats = read_reference(gray[y0:y1, x0:x1])
    side = side_value(side, [x0, y0, x1, y1], size)

    hole_width = stats['width']
    hole_height = stats['height']
    hole_area = stats['area']
    pitch = stats['pitch']
    span = abs(stats['hole'] - stats['base'])
    if span < .022:
        raise Invalid('参照框里的孔和片基差别太小，请换一段更清楚的齿孔作参照')

    reach = int(round(hole_height * 2.2))
    region_top = 0 if side == 'top' else max(0, y0 - reach)
    region_bottom = min(size[1], y1 + reach) if side == 'top' else size[1]
    region = gray[region_top:region_bottom]
    if region.shape[0] < 4 or region.shape[1] < 4:
        raise Invalid('齿孔所在片边没有足够的扫描范围')

    line = _base_line(region, stats['base'], span)
    offset = (region - line[None, :]) if stats['bright'] else (line[None, :] - region)
    # Keep only tones between film base and clear film; the black scanner background and
    # specular highlights far past hole tone cannot be perforation openings.
    normalized = offset / span
    mask = ((normalized > .55) & (normalized < 1.6)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats_c, centroids = cv2.connectedComponentsWithStats(mask, 8)

    minimum_area = max(8, hole_area * .14)
    comps, clipped = [], []
    for index in range(1, count):
        left, top, w, h, area = (int(value) for value in stats_c[index])
        if area < minimum_area or w < 3 or h < 3:
            continue
        if h < hole_height * .45 or w > hole_width * 7.5 or h > hole_height * 4.2:
            continue
        bounds = [left, top + region_top, left + w, top + h + region_top]
        # A hole the scan cut off keeps an open side; it is partial, not a break.
        if _touch_border(bounds, size):
            clipped.append({'bounds': bounds})
            continue
        comps.append({'bounds': bounds, 'left': left, 'right': left + w,
                      'top': top + region_top, 'bottom': top + h + region_top,
                      'w': w, 'h': h, 'area': area,
                      'cx': float(centroids[index][0]),
                      'cy': float(centroids[index][1] + region_top),
                      'fill': area / float(w * h)})

    plausible = [c for c in comps
                 if .58 * hole_width <= c['w'] <= 1.42 * hole_width
                 and .62 * hole_height <= c['h'] <= 1.38 * hole_height
                 and c['fill'] >= .72 * stats['fill']]
    reference_centers = [(hole['cx'] + x0, hole['cy'] + y0) for hole in stats['holes']]
    curve, spread = _row_curve(plausible, reference_centers, hole_height)

    damaged, normal = [], 0
    for c in comps:
        if abs(c['cy'] - float(curve(c['cx']))) > max(1.22 * hole_height, 7):
            continue
        holes = max(1, int(round(c['w'] / pitch)))
        width_ratio = c['w'] / hole_width
        height_ratio = c['h'] / hole_height
        area_ratio = c['area'] / hole_area
        fill_ratio = c['fill'] / stats['fill']
        # A merged opening may keep thin remnants of the divider, so its height stays
        # close to one hole while its width spans two or more holes.
        if width_ratio >= 2.15 and area_ratio >= 1.3:
            kind = 'merged'
        elif (width_ratio > 1.25 or height_ratio > 1.26
              or area_ratio > 1.3 or fill_ratio < .87):
            # A break opens the hole wider or ragged. A hole merely smaller than the
            # reference may just be cut off by the film edge or lit differently, so
            # reduced size alone is not treated as damage.
            kind = 'torn'
        else:
            normal += 1
            continue
        damaged.append({**c, 'holes': holes if kind == 'merged' else 1, 'kind': kind,
                        'width_ratio': width_ratio, 'height_ratio': height_ratio,
                        'area_ratio': area_ratio, 'fill_ratio': fill_ratio})

    grouped = _group(damaged, pitch)
    for number, item in enumerate(grouped, 1):
        item['index'] = number
        item['bounds'] = [int(value) for value in item['bounds']]
        item['width'] = item['bounds'][2] - item['bounds'][0]
        item['height'] = item['bounds'][3] - item['bounds'][1]
        item['reason'] = ('相邻齿孔连成了一个口' if item['kind'] == 'merged'
                          else '孔形撕开，与参照孔的宽高和面积不符')
        for key in ('width_ratio', 'height_ratio', 'area_ratio', 'fill_ratio'):
            item[key] = round(float(item[key]), 2)
        for key in ('left', 'right', 'top', 'bottom', 'cx', 'cy', 'w', 'h', 'area', 'fill'):
            item.pop(key, None)

    notes = [f'参照框读到{stats["count"]}个完整孔，孔距约{pitch:.1f}像素。']
    if spread > 2:
        notes.append(f'整排齿孔沿片边有弯曲，按整排拟合后残差约{spread:.1f}像素。')
    if clipped:
        notes.append(f'扫描边缘没拍全的孔{len(clipped)}个，未算破口。')
    notes.append(f'沿这一排检查的{normal}个孔没有异常。' if not grouped
                 else f'沿这一排另有{normal}个孔形状正常。')
    return {
        'reference': {'bounds': [x0, y0, x1, y1], 'side': side,
                      'holes': stats['count'],
                      'hole_width': round(hole_width, 1),
                      'hole_height': round(hole_height, 1),
                      'pitch': round(pitch, 1),
                      'contrast': round(stats['contrast'], 3),
                      'tone': 'bright' if stats['bright'] else 'dark'},
        'inspected': normal,
        'damage': grouped,
        'clipped': {'count': len(clipped),
                    'left': sum(1 for item in clipped if item['bounds'][0] <= 1),
                    'right': sum(1 for item in clipped if item['bounds'][2] >= size[0] - 1)},
        'notes': notes,
    }


def annotate(display, report, reference):
    """Draw the detected breaks on a display-ready 8-bit copy of the original scan."""
    view = display.copy()
    height, width = view.shape[:2]
    scale = max(height, width)
    line = max(2, int(round(scale / 700)))
    thickness = line + 1
    x0, y0, x1, y1 = reference
    cv2.rectangle(view, (x0, y0), (x1 - 1, y1 - 1), (96, 184, 116), line)
    for item in report['damage']:
        left, top, right, bottom = item['bounds']
        pad = max(2, line)
        cv2.rectangle(view, (max(0, left - pad), max(0, top - pad)),
                      (min(width - 1, right + pad), min(height - 1, bottom + pad)),
                      (240, 61, 41), thickness)
        # Number above the box when there is room, inside its top-left corner otherwise.
        font = max(.5, scale / 1600)
        thickness_text = max(1, line // 2)
        baseline = top - pad - 6
        if baseline < 10 + thickness_text * 4:
            baseline = min(height - 2, bottom + pad + int(8 + font * 14))
        cv2.putText(view, str(item['index']), (max(0, left - pad), baseline),
                    cv2.FONT_HERSHEY_SIMPLEX, font, (246, 107, 61), thickness_text,
                    cv2.LINE_AA)
    return view
