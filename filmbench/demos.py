
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .scan import png_rgb, srgb_to_linear
from .settings import initial_settings


DEMOS = {
    'harbor': {'title': '港湾日落', 'description': '16位线性 RGB · 正常橙色片边和暗部纹理'},
    'market': {'title': '雨夜市集', 'description': '8位 sRGB · 通道截断和一处灰尘'},
    'tears': {'title': '齿孔破口', 'description': '16位线性 RGB · 上排一个撕开的孔和一处连孔破口'},
}

WIDTH, HEIGHT = 1024, 638
CROP = [38, 38, WIDTH - 38, HEIGHT - 38]
BASE_REGION = [90, 10, 190, 29]


def gradient(width, height, colors):
    y = np.linspace(0, 1, height)
    positions = np.linspace(0, 1, len(colors))
    channels = [np.interp(y, positions, [color[c] for color in colors]) for c in range(3)]
    return np.tile(np.stack(channels, axis=1)[:, None, :], (1, width, 1)).astype(np.uint8)


def harbor(width, height):
    sky = gradient(width, height, [(44, 65, 105), (149, 111, 143), (247, 164, 113), (45, 72, 74)])
    image = Image.fromarray(sky)
    d = ImageDraw.Draw(image, 'RGBA')
    rng = np.random.default_rng(207)
    horizon = round(height * .62)
    d.ellipse((661, 229, 732, 300), (251, 183, 111, 255))
    for _ in range(42):
        x, y = int(rng.integers(0, width)), int(rng.integers(75, horizon))
        d.line((x, y, x + int(rng.integers(15, 310)), y + int(rng.integers(-2, 3))),
               fill=(244, 174, 121, int(rng.integers(18, 78))), width=int(rng.integers(1, 5)))
    for index, color in enumerate(((92, 76, 110), (61, 61, 90), (43, 59, 69))):
        edges = [(0, horizon + 14 * index)]
        for x in range(0, width + 110, 76):
            y = horizon - (20 - index * 3) - (48 - index * 8) * abs(np.sin((x + index * 54) / 89))
            edges.append((x, round(y + index * 13 + rng.integers(-10, 6))))
        d.polygon([*edges, (width, horizon + 28), (0, horizon + 28)], color + (255,))
    for y in range(horizon, height):
        strength = (y - horizon) / (height - horizon)
        c = (round(73 - 44 * strength), round(88 - 29 * strength), round(100 - 23 * strength))
        d.line((0, y, width, y), c + (255,))
    for _ in range(1200):
        y = int(rng.integers(horizon, height))
        distance = (y - horizon) / (height - horizon)
        x = int(rng.integers(0, width))
        length = int(rng.integers(3, int(13 + 95 * distance)))
        sun = abs(x - 685) < 20 + 200 * distance and rng.random() < .65
        color = (214, 145, 98, int(rng.integers(16, 90))) if sun else (126, 155, 153, int(rng.integers(8, 79)))
        d.line((x, y, x + length, y), color, width=1 + int(distance > .48))
    for bx, by, scale in [(285, 470, 1.0), (505, 422, .43), (775, 446, .60)]:
        d.polygon([(bx - 105 * scale, by), (bx + 99 * scale, by - 5 * scale),
                   (bx + 73 * scale, by + 16 * scale), (bx - 78 * scale, by + 21 * scale)],
                  (23, 31, 44, 255))
        d.line((bx, by, bx - 5 * scale, by - 211 * scale), (28, 39, 53, 255), width=max(2, round(scale * 4)))
        d.polygon([(bx + 7 * scale, by - 17 * scale), (bx - 3 * scale, by - 202 * scale),
                   (bx + 72 * scale, by - 29 * scale)], (232, 195, 153, 255))
        d.polygon([(bx - 12 * scale, by - 21 * scale), (bx - 13 * scale, by - 158 * scale),
                   (bx - 73 * scale, by - 38 * scale)], (202, 186, 167, 255))
        for i in range(5):
            y = by + 20 + i * 4
            d.line((bx - 83 * scale + i * 2, y, bx + 80 * scale + i * 3, y),
                   (20, 34, 46, max(12, 76 - i * 13)), width=2)
    return np.asarray(image)


def market(width, height):
    image = Image.fromarray(gradient(width, height, [(18, 24, 43), (43, 23, 40), (23, 32, 43)]))
    d = ImageDraw.Draw(image, 'RGBA')
    rng = np.random.default_rng(3839)
    d.polygon([(0, 510), (380, 309), (594, 313), (width, 507), (width, height), (0, height)],
              (39, 44, 59, 255))
    swatches = [(243, 141, 69), (228, 64, 99), (79, 213, 199), (229, 185, 114)]
    for edge, sign in [(0, 1), (width, -1)]:
        for k in range(5, -1, -1):
            x0 = edge + sign * (50 + k * 57)
            x1 = x0 + sign * 75
            y0, roof = 397 - 17 * k, 79 + 38 * k
            left, right = sorted((x0, x1))
            body = (53 - 2 * k, 57 - 2 * k, 76 - 4 * k)
            d.rectangle((left, roof, right, y0), body + (255,))
            color = swatches[k % len(swatches)]
            for floor in range(roof + 22, y0 - 9, 30):
                for x in range(left + 9, right - 9, 17):
                    d.rectangle((x, floor, x + 6, floor + 15),
                                color + (int(rng.choice([27, 52, 116, 202])),))
            d.rectangle((left + 4, y0 - 25, right - 4, y0 - 8), color + (255,))
            d.line((left, y0 + 2, right, y0 + 2), color + (150,), width=3)
            for _ in range(23):
                x = int(rng.integers(left - 11, right + 11))
                y = min(height - 1, y0 + int(rng.integers(9, 167)))
                d.line((x, y, x + int(rng.integers(4, 34)), y + 1), color + (int(rng.integers(15, 80)),))
    for _ in range(180):
        x = int(rng.integers(0, width))
        y = int(rng.integers(0, height))
        d.line((x, y, x - 4, y + 11), (177, 211, 235, int(rng.integers(14, 85))))
    for x, y, s in [(439, 433, 1), (616, 392, .67), (516, 363, .46)]:
        d.pieslice((x - 44*s, y - 78*s, x + 44*s, y - 11*s), 180, 360, (169, 65, 92, 255))
        d.ellipse((x - 9*s, y - 44*s, x + 9*s, y - 26*s), (29, 25, 41, 255))
        d.line((x, y - 23*s, x, y + 17*s), (27, 26, 41, 255), width=max(3, round(17*s)))
        d.line((x - 5*s, y + 6*s, x - 12*s, y + 40*s), (26, 24, 40, 255), width=max(2, round(7*s)))
        d.line((x + 4*s, y + 6*s, x + 12*s, y + 40*s), (26, 24, 40, 255), width=max(2, round(7*s)))
    return np.asarray(image)


def make_demo(key):
    rng = np.random.default_rng(25 if key == 'harbor' else 142)
    base = np.array([.79, .35, .137], dtype=np.float32)
    if key == 'market':
        base = np.array([.73, .29, .12], dtype=np.float32)
    trans = np.broadcast_to(base, (HEIGHT, WIDTH, 3)).copy()
    x0, y0, x1, y1 = CROP
    scene = (harbor if key == 'harbor' else market)(x1 - x0, y1 - y0)
    density = .04 + 1.21 * srgb_to_linear(scene.astype(np.float32) / 255)
    density += rng.normal(0, .006, density.shape[:2])[..., None]
    trans[y0:y1, x0:x1] = base * np.float32(10) ** -density

    border = Image.fromarray(np.full((HEIGHT, WIDTH), 255, np.uint8))
    draw = ImageDraw.Draw(border)
    for x in range(55, WIDTH - 45, 75):
        draw.rounded_rectangle((x, 3, x + 26, 7), radius=2, fill=35)
        draw.rounded_rectangle((x, HEIGHT - 8, x + 26, HEIGHT - 4), radius=2, fill=28)
    for x, label in [(70, 'FILMBENCH'), (412, '01A'), (705, 'SYNTHETIC')]:
        draw.text((x, HEIGHT - 27), label, font=ImageFont.load_default(), fill=62)
    trans *= np.asarray(border, dtype=np.float32)[..., None] / 255
    if key == 'market':
        trans[236:252, 135:168, 0] = 1
        trans[413:437, 736:755, 2] = 0
        trans[474:477, 671:674] *= .09
        values = np.where(trans <= .0031308, trans * 12.92,
                          1.055 * trans ** (1 / 2.4) - .055)
        scan = np.rint(values * 255).astype(np.uint8)
        encoding = 'srgb'
    else:
        scan = np.rint(trans * 65535).astype(np.uint16)
        encoding = 'linear'
    settings = initial_settings(encoding, BASE_REGION, CROP)
    return DEMOS[key]['title'], png_rgb(scan), settings


def sprocket_strip(width, height, holes, pitch, size, curve, seed):
    """Drawn row of perforations for the sprocket demo; returned on a 0..255 mask."""
    mask = np.zeros((height, width), np.float32)
    rng = np.random.default_rng(seed)
    hole_width, hole_height = size
    for index, cx in enumerate(holes):
        cy = 31 + curve * np.sin((cx / width) * np.pi * 1.7 + .4)
        left = cx - hole_width / 2
        top = cy - hole_height / 2
        mask[max(0, int(top)):int(top + hole_height),
             max(0, int(left)):int(left + hole_width)] = 1
        if index == 6:                        # single torn hole
            mask[max(0, int(top) - 5):int(top + hole_height + 2),
                 max(0, int(left) - 3):int(left + hole_width + 4)] = 1
        if index == 9:                         # two holes joined by a torn bridge
            mask[max(0, int(top)):int(top + hole_height),
                 int(left):int(cx + pitch * 1.0 + hole_width / 2)] = 1
    return mask


TEAR_WIDTH, TEAR_HEIGHT = 1024, 420
TEAR_PITCH, TEAR_HOLE, TEAR_CURVE = 47.0, (26, 19), 7.0
TEAR_HOLES = [-12 + index * TEAR_PITCH for index in range(23)]
TEAR_CROP = [30, 92, TEAR_WIDTH - 30, TEAR_HEIGHT - 30]
TEAR_BASE = [96, 12, 210, 52]
TEAR_REFERENCE = [8, 6, 205, 62]


def tear_scan():
    """16-bit scan whose top sprocket row has one torn hole and one joined opening."""
    rng = np.random.default_rng(64)
    base = np.array([.78, .34, .132], dtype=np.float32)
    trans = np.broadcast_to(base, (TEAR_HEIGHT, TEAR_WIDTH, 3)).copy()
    x0, y0, x1, y1 = TEAR_CROP
    scene = harbor(x1 - x0, y1 - y0)
    density = .04 + 1.21 * srgb_to_linear(scene.astype(np.float32) / 255)
    density += rng.normal(0, .006, density.shape[:2])[..., None]
    trans[y0:y1, x0:x1] = base * np.float32(10) ** -density
    # Clear film where the perforations sit, so the holes read as bright openings.
    holes = sprocket_strip(TEAR_WIDTH, TEAR_HEIGHT, TEAR_HOLES, TEAR_PITCH,
                           TEAR_HOLE, TEAR_CURVE, 64)
    trans[:72] = np.maximum(trans[:72], holes[:72, :, None] * np.float32([.94, .96, .97]))
    # Edge print on the film base, away from the perforation row.
    border = Image.fromarray(np.full((TEAR_HEIGHT, TEAR_WIDTH), 255, np.uint8))
    draw = ImageDraw.Draw(border)
    draw.text((40, 80), 'FILMBENCH 35mm 01A', font=ImageFont.load_default(), fill=58)
    draw.text((TEAR_WIDTH - 190, TEAR_HEIGHT - 24), 'SYNTHETIC', font=ImageFont.load_default(), fill=58)
    trans *= np.asarray(border, dtype=np.float32)[..., None] / 255
    scan = np.rint(trans * 65535).astype(np.uint16)
    settings = initial_settings('linear', TEAR_BASE, TEAR_CROP)
    settings['sprocket'] = {'region': TEAR_REFERENCE, 'side': 'top'}
    return DEMOS['tears']['title'], png_rgb(scan), settings
