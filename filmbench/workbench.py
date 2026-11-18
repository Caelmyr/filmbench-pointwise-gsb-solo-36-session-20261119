from functools import lru_cache

import cv2
import numpy as np

from image_lab.validation import Invalid

from .density import check_region, density_view, develop, relative_density, sample_base
from .diagnostics import clipping, overlay, rounded, summary
from .flatfield import build_gain
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

    @lru_cache(maxsize=3)
    def flat_gain(self, project_id, sha, encoding):
        flat = decode(self.projects.flat_raw(project_id))
        return build_gain(flat, self.scan(project_id).size, encoding)

    def flat_for(self, project):
        meta = project.get('flat')
        if not meta:
            return None, None
        return self.flat_gain(project['id'], meta['sha256'], project['settings']['encoding'])

    def attach_flat(self, project_id, raw, filename):
        project = self.projects.load(project_id)
        flat = decode(raw)
        build_gain(flat, self.scan(project_id).size, project['settings']['encoding'])
        return self.projects.attach_flat(project_id, raw, filename, flat)

    def check_setting(self, project_id, settings):
        check_region(self.scan(project_id).codes, settings['base_region'])

    def develop(self, project):
        settings = project['settings']
        scan = self.scan(project['id'])
        source = scan.linear(settings['encoding'])
        gain, _ = self.flat_for(project)
        if gain is not None:
            source = np.clip(source / gain, 0, 1)
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
        report = summary(scan, base, source, density, positive)
        _, report['flat'] = self.flat_for(project)
        return report

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
