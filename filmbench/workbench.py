from functools import lru_cache

import cv2
import numpy as np

from image_lab.validation import Invalid

from .adjacent import find_adjacent
from .density import check_region, density_view, develop, relative_density, sample_base
from .diagnostics import clipping, overlay, rounded, summary
from .frame import apply_retouch, prepared
from .scan import compact, decode, display_rgb, png_rgb, tiff_rgb
from .settings import point


VIEWS = ('source', 'reference', 'positive', 'density', 'clipping')
OUTPUTS = ('positive.png', 'positive.tif', 'density.png')


class Workbench:
    def __init__(self, projects):
        self.projects = projects

    @lru_cache(maxsize=3)
    def scan(self, project_id):
        return decode(self.projects.raw(project_id))

    def check_setting(self, project_id, settings):
        check_region(self.scan(project_id).codes, settings['base_region'])

    def neighbor(self, project, direction):
        settings = project['settings']
        if not settings['crop']:
            raise Invalid('请先框出一格画格，再沿胶片查找邻格。')
        scan = self.scan(project['id'])
        source = scan.linear(settings['encoding'])
        base = sample_base(source, scan.codes, scan.maximum, settings['base_region'])
        return find_adjacent(source, base['transmission'], settings['crop'],
                             self._step(settings, direction))

    @staticmethod
    def _step(settings, direction):
        """Map a direction seen in the displayed frame back to the scan axis."""
        if settings['turn'] % 2:
            raise Invalid('画格已旋转 90°，胶片在画面中沿上下延伸。请先恢复正向，再用左右邻格。')
        step = -1 if direction == 'left' else 1
        if settings['turn'] == 2:
            step = -step
        return -step if settings['mirror'] else step

    def develop(self, project):
        settings = project['settings']
        scan = self.scan(project['id'])
        source = scan.linear(settings['encoding'])
        base = sample_base(source, scan.codes, scan.maximum, settings['base_region'])
        density = relative_density(source, base['transmission'])
        positive = apply_retouch(develop(density, settings['tone']), settings['retouch'])
        return scan, source, base, density, positive

    def preview(self, project, view):
        settings = project['settings']
        scan = self.scan(project['id'])
        if view in ('source', 'reference', 'clipping'):
            source = scan.linear(settings['encoding'])
            if view == 'clipping':
                source = overlay(source, *clipping(scan.codes, scan.maximum))
            elif view == 'reference':
                source = prepared(source, settings, scan.size)
            return png_rgb(display_rgb(compact(source)))
        if view not in VIEWS:
            raise Invalid('无法识别预览模式')
        scan, _, _, density, positive = self.develop(project)
        if view == 'density':
            grey = compact(prepared(density_view(density), settings, scan.size))
            return png_rgb(display_rgb(np.repeat(grey[..., None], 3, axis=2)))
        return png_rgb(display_rgb(compact(prepared(positive, settings, scan.size))))

    def download(self, project, name):
        if name not in OUTPUTS:
            raise Invalid('无法识别下载格式')
        scan, _, _, density, positive = self.develop(project)
        settings = project['settings']
        if name == 'density.png':
            grey = prepared(density_view(density), settings, scan.size)
            density_codes = np.rint(grey * 65535).astype(np.uint16)
            success, data = cv2.imencode('.png', density_codes)
            if not success:
                raise Invalid('密度图无法编码')
            return data.tobytes(), 'image/png'
        framed = prepared(positive, settings, scan.size)
        if name == 'positive.tif':
            return tiff_rgb(display_rgb(framed, 16)), 'image/tiff'
        return png_rgb(display_rgb(framed)), 'image/png'

    def metrics(self, project):
        scan, source, base, density, positive = self.develop(project)
        positive = prepared(positive, project['settings'], scan.size)
        return summary(scan, base, source, density, positive)

    def probe(self, project, coords):
        x, y = point(coords, project['size'], '像素探针')
        scan, source, _, density, positive = self.develop(project)
        codes = scan.codes[y, x]
        return {'point': [x, y], 'codes': codes.tolist(), 'linear': rounded(source[y, x]),
                'density': rounded(density[y, x]),
                'positive': display_rgb(positive[y, x]).tolist(),
                'clipped_channels': np.any([codes == 0, codes == scan.maximum], axis=0).tolist()}

    def loupe(self, project, coords):
        x, y = point(coords, project['size'], '纹理放大镜')
        _, _, _, _, positive = self.develop(project)
        width, height = project['size']
        crop = positive[max(0, y - 16):min(y + 17, height), max(0, x - 16):min(x + 17, width)]
        view = np.repeat(np.repeat(display_rgb(crop), 6, axis=0), 6, axis=1)
        return png_rgb(view)
