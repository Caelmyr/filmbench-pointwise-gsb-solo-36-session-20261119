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

    def create(self, title, raw, settings, origin, filename, scan=None):
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
            'settings': settings,
        }
        temp = Path(mkdtemp(prefix='.scan-', dir=self.root))
        try:
            (temp / 'scan.bin').write_bytes(raw)
            self._write(temp, project)
            temp.rename(self.location(project_id))
        finally:
            if temp.exists():
                rmtree(temp)
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

    def find_neighbor(self, project_id, version, direction, locate):
        """Locate the cell beside the current crop and adopt it, if it is credible."""
        with self.lock:
            project = self.load(project_id)
            if type(version) is not int or version != project['version']:
                raise Conflict('此工程已更新，请刷新后重试')
            found = locate(project, direction)
            settings = validate({**project['settings'], 'crop': found['crop']}, project['size'])
            project.update(settings=settings, version=version + 1)
            self._write(self.location(project_id), project)
            return project, found

    def archive(self, project):
        raw = self.raw(project['id'])
        buffer = BytesIO()
        name = 'scan.' + FORMATS[project['scan_format']][0]
        manifest = {k: project[k] for k in ('title', 'filename', 'sha256', 'settings')}
        manifest.update(schema='filmbench-project/1', scan=name)
        with ZipFile(buffer, 'w', ZIP_DEFLATED, compresslevel=2) as archive:
            archive.writestr('filmbench.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr(name, raw)
        return buffer.getvalue()

    def restore(self, data):
        try:
            with ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
                if len(names) != 2 or len(set(names)) != 2 or 'filmbench.json' not in names:
                    raise Invalid('工程ZIP须含一份说明和一张扫描原档')
                manifest_info = archive.getinfo('filmbench.json')
                if manifest_info.file_size > 16000:
                    raise Invalid('工程说明过长')
                document = json.loads(archive.read(manifest_info))
                if not isinstance(document, dict) or document.get('schema') != 'filmbench-project/1':
                    raise Invalid('无法识别工程版本')
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
                return self.create(document.get('title'), raw, document.get('settings'), '恢复工程',
                                   document.get('filename'), scan)
        except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, NotImplementedError, RuntimeError, zlib.error) as error:
            raise Invalid('工程ZIP无法读取或已损坏') from error
