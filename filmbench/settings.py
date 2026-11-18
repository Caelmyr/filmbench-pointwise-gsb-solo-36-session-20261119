from math import isfinite

from image_lab.validation import Invalid


TONE_DEFAULT = {'black': 0.04, 'white': 1.25, 'exposure': 0.0, 'contrast': 1.0,
                'red': 0.0, 'green': 0.0, 'blue': 0.0}
TONE_RANGE = {'black': (0, .6), 'white': (.35, 3), 'exposure': (-3, 3),
              'contrast': (.35, 2.2), 'red': (-.35, .35),
              'green': (-.35, .35), 'blue': (-.35, .35)}
ENCODINGS = ('srgb', 'linear')


def initial_settings(encoding, base_region=None, crop=None):
    return validate({'encoding': encoding, 'base_region': base_region,
                     'crop': crop, 'turn': 0, 'mirror': False,
                     'tone': TONE_DEFAULT.copy(), 'retouch': None}, None)


def fields(value, allowed, label):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise Invalid(f'{label}含无效字段')


def region(value, size, label, minimum):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4 or any(type(i) is not int for i in value):
        raise Invalid(f'{label}需要四个整数坐标')
    left, top, right, bottom = value
    if left < 0 or top < 0 or right - left < minimum or bottom - top < minimum:
        raise Invalid(f'{label}过小或越出原片')
    if size and (right > size[0] or bottom > size[1]):
        raise Invalid(f'{label}越出原片')
    return list(value)


def point(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(i) is not int for i in value):
        raise Invalid(f'{label}需要两个整数坐标')
    if min(value) < 0 or (size and any(coord >= bound for coord, bound in zip(value, size))):
        raise Invalid(f'{label}越出原片')
    return list(value)


def direction(value):
    if value not in ('left', 'right'):
        raise Invalid('邻格方向应为左或右')
    return value


def clone_setting(value, size):
    if value is None:
        return None
    fields(value, {'source', 'target', 'radius'}, '修补')
    if set(value) != {'source', 'target', 'radius'}:
        raise Invalid('修补需要来源、目标和半径')
    radius = value['radius']
    if type(radius) is not int or not 2 <= radius <= 64:
        raise Invalid('修补半径应为2至64原片像素')
    source = point(value['source'], size, '修补来源')
    target = point(value['target'], size, '修补目标')
    if source == target:
        raise Invalid('修补来源和目标需要不同')
    return {'source': source, 'target': target, 'radius': radius}


def validate(settings, size):
    keys = {'encoding', 'base_region', 'crop', 'turn', 'mirror', 'tone', 'retouch'}
    fields(settings, keys, '扫描设置')
    if set(settings) != keys:
        raise Invalid('扫描设置不完整')
    if settings['encoding'] not in ENCODINGS:
        raise Invalid('扫描编码应为 sRGB 或线性 RGB')
    if type(settings['turn']) is not int or settings['turn'] not in range(4):
        raise Invalid('转向应为从0至3的整数')
    if type(settings['mirror']) is not bool:
        raise Invalid('镜像标记必须是布尔值')
    tone = settings['tone']
    fields(tone, TONE_RANGE.keys(), '显影')
    if tone.keys() != TONE_RANGE.keys():
        raise Invalid('显影设置不完整')
    for key, (low, high) in TONE_RANGE.items():
        value = tone[key]
        if type(value) not in (int, float) or not low <= value <= high or not isfinite(value):
            raise Invalid(f'显影参数 {key} 应在 {low:g} 到 {high:g} 之间')
    if tone['white'] - tone['black'] < .12:
        raise Invalid('正片黑白点至少相差 0.12 密度单位')
    return {**settings, 'tone': dict(tone),
            'base_region': region(settings['base_region'], size, '片基取样', 3),
            'crop': region(settings['crop'], size, '画格裁切', 8),
            'retouch': clone_setting(settings['retouch'], size)}


def apply_patch(settings, changes, size):
    fields(changes, settings.keys(), '更改')
    updated = {**settings, **changes}
    if 'tone' in changes:
        fields(changes['tone'], TONE_RANGE.keys(), '显影更改')
        updated['tone'] = {**settings['tone'], **changes['tone']}
    return validate(updated, size)
