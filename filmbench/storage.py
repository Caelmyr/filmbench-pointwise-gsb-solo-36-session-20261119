from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp, NamedTemporaryFile
from time import time_ns
from threading import RLock
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid

from .density import check_region
from .scan import FORMATS, MAX_UPLOAD, decode
from .settings import apply_patch, validate


def flat_meta(filename, raw, scan):
    if not isinstance(filename, str) or not filename or len(filename) > 150:
        raise Invalid('空拍文件名无效')
    return {'filename': filename, 'sha256': sha256(raw).hexdigest(),
            'format': scan.format, 'depth': scan.depth, 'size': list(scan.size)}


class Projects:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def location(self, project_id):
        try:
            if str(UUID(project_id)) != project_id:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise NotFound('扫描工程不存在') from error
        return self.root / project_id

    def load(self, project_id):
        target = self.location(project_id) / 'project.json'
        try:
            return json.loads(target.read_text(encoding='utf-8'))
        except FileNotFoundError as error:
            raise NotFound('扫描工程不存在') from error

    def list(self):
        return sorted((json.loads(path.read_text(encoding='utf-8'))
                       for path in self.root.glob('*/project.json') if not path.parent.name.startswith('.')),
                      key=lambda project: project['created_order'], reverse=True)

    def raw(self, project_id):
        return (self.location(project_id) / 'scan.bin').read_bytes()

    def flat_raw(self, project_id):
        try:
            return (self.location(project_id) / 'flat.bin').read_bytes()
        except FileNotFoundError as error:
            raise Invalid('空拍文件缺失，请重新上传') from error

    def create(self, title, raw, settings, origin, filename, scan=None, flat=None):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
            raise Invalid('工程名称需为1至100个字符')
        if not isinstance(filename, str) or len(filename) > 150 or not filename:
            raise Invalid('扫描文件名无效')
        if not isinstance(origin, str) or origin not in ('导入扫描', '合成演示', '恢复工程'):
            raise Invalid('扫描来源无效')
        scan = scan or decode(raw)
        settings = validate(settings, scan.size)
        check_region(scan.codes, settings['base_region'])
        project_id = str(uuid4())
        project = {
            'id': project_id, 'title': title.strip(), 'origin': origin, 'filename': filename,
            'scan_format': scan.format, 'size': list(scan.size), 'depth': scan.depth,
            'sha256': sha256(raw).hexdigest(), 'created_order': time_ns(), 'version': 1,
            'settings': settings, 'flat': None,
        }
        temp = Path(mkdtemp(prefix='.scan-', dir=self.root))
        try:
            (temp / 'scan.bin').write_bytes(raw)
            if flat:
                flat_raw, flat_name, flat_scan = flat
                project['flat'] = flat_meta(flat_name, flat_raw, flat_scan)
                (temp / 'flat.bin').write_bytes(flat_raw)
            self._write(temp, project)
            temp.rename(self.location(project_id))
        finally:
            if temp.exists():
                rmtree(temp)
        return project

    def attach_flat(self, project_id, raw, filename, scan):
        with self.lock:
            project = self.load(project_id)
            location = self.location(project_id)
            meta = flat_meta(filename, raw, scan)
            handle = NamedTemporaryFile(dir=location, delete=False)
            try:
                with handle:
                    handle.write(raw)
                Path(handle.name).replace(location / 'flat.bin')
            finally:
                Path(handle.name).unlink(missing_ok=True)
            project.update(flat=meta, version=project['version'] + 1)
            self._write(location, project)
            return project

    def remove_flat(self, project_id):
        with self.lock:
            project = self.load(project_id)
            if not project.get('flat'):
                raise Invalid('该工程尚未附加空拍')
            location = self.location(project_id)
            project.update(flat=None, version=project['version'] + 1)
            self._write(location, project)
            (location / 'flat.bin').unlink(missing_ok=True)
            return project

    @staticmethod
    def _write(location, project):
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=location, delete=False) as file:
            temp = Path(file.name)
            try:
                json.dump(project, file, ensure_ascii=False, indent=2)
                file.close()
                temp.replace(location / 'project.json')
            finally:
                temp.unlink(missing_ok=True)

    def edit(self, project_id, version, changes, validate_content):
        with self.lock:
            project = self.load(project_id)
            if type(version) is not int or version != project['version']:
                raise Conflict('此工程已更新，请刷新后重试')
            settings = apply_patch(project['settings'], changes, project['size'])
            validate_content(settings)
            project.update(settings=settings, version=version + 1)
            self._write(self.location(project_id), project)
            return project

    def archive(self, project):
        raw = self.raw(project['id'])
        buffer = BytesIO()
        name = 'scan.' + FORMATS[project['scan_format']][0]
        manifest = {k: project[k] for k in ('title', 'filename', 'sha256', 'settings')}
        manifest.update(schema='filmbench-project/1', scan=name)
        flat = project.get('flat')
        entries = [(name, raw)]
        if flat:
            stored = 'flat.' + FORMATS[flat['format']][0]
            manifest['flat'] = {'filename': flat['filename'], 'sha256': flat['sha256'],
                                'stored': stored}
            entries.append((stored, self.flat_raw(project['id'])))
        with ZipFile(buffer, 'w', ZIP_DEFLATED, compresslevel=2) as archive:
            archive.writestr('filmbench.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for entry, payload in entries:
                archive.writestr(entry, payload)
        return buffer.getvalue()

    def restore(self, data):
        try:
            with ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
                if 'filmbench.json' not in names:
                    raise Invalid('工程ZIP须含一份说明和一张扫描原档')
                manifest_info = archive.getinfo('filmbench.json')
                if manifest_info.file_size > 16000:
                    raise Invalid('工程说明过长')
                document = json.loads(archive.read(manifest_info))
                if not isinstance(document, dict) or document.get('schema') != 'filmbench-project/1':
                    raise Invalid('无法识别工程版本')
                flat_doc = document.get('flat')
                expected = 3 if flat_doc else 2
                if len(names) != expected or len(set(names)) != expected:
                    raise Invalid('工程ZIP须含一份说明、一张扫描原档，可带一张空拍')
                name = document.get('scan')
                if name not in ['scan.' + info[0] for info in FORMATS.values()] or name not in names:
                    raise Invalid('工程中的扫描文件名无效')
                info = archive.getinfo(name)
                if info.file_size > MAX_UPLOAD:
                    raise Invalid('工程中的扫描超过28MB')
                raw = archive.read(info)
                if sha256(raw).hexdigest() != document.get('sha256'):
                    raise Invalid('工程中的扫描与原档校验值不一致')
                scan = decode(raw)
                if name != 'scan.' + FORMATS[scan.format][0]:
                    raise Invalid('工程内扫描格式与说明不符')
                flat = self._restore_flat(archive, flat_doc, scan) if flat_doc else None
                return self.create(document.get('title'), raw, document.get('settings'), '恢复工程',
                                   document.get('filename'), scan, flat)
        except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, NotImplementedError, RuntimeError, zlib.error) as error:
            raise Invalid('工程ZIP无法读取或已损坏') from error

    @staticmethod
    def _restore_flat(archive, document, scan):
        if not isinstance(document, dict):
            raise Invalid('工程中的空拍说明无效')
        stored = document.get('stored')
        if stored not in archive.namelist():
            raise Invalid('工程中的空拍文件缺失')
        info = archive.getinfo(stored)
        if info.file_size > MAX_UPLOAD:
            raise Invalid('工程中的空拍超过28MB')
        raw = archive.read(info)
        if sha256(raw).hexdigest() != document.get('sha256'):
            raise Invalid('工程中的空拍与说明校验值不一致')
        flat = decode(raw)
        if stored != 'flat.' + FORMATS[flat.format][0]:
            raise Invalid('工程内空拍格式与说明不符')
        if flat.size != scan.size:
            raise Invalid('工程中的空拍与扫描尺寸不一致')
        return raw, document.get('filename'), flat
