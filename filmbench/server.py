from io import BytesIO

from flask import Blueprint, jsonify, redirect, render_template, request, send_file

from image_lab.validation import Invalid

from .demos import DEMOS, make_demo
from .frame import frame_size
from .scan import FORMATS, MAX_UPLOAD
from .settings import initial_settings
from .storage import Projects
from .workbench import Workbench


def register(app, data_dir):
    projects = Projects(data_dir)
    workbench = Workbench(projects)
    film = Blueprint('film', __name__, url_prefix='/film')

    @app.get('/')
    def root():
        return redirect('/film/')

    @film.get('/')
    def index():
        return render_template('filmbench.html')

    @film.get('/health')
    def health():
        return jsonify({'ok': True, 'workspace': 'FilmBench'})

    def info(project):
        return {**project, 'frame_size': frame_size(project['settings'], project['size'])}

    def uploaded(name, maximum, label):
        data = request.files.get(name)
        if not data or not data.filename:
            raise Invalid(f'请选择{label}')
        upload_name = data.filename.replace('\\', '/').split('/')[-1]
        raw = data.stream.read(maximum + 1)
        if len(raw) > maximum:
            raise Invalid(f'{label}超过大小限制')
        return raw, upload_name

    @film.get('/projects')
    def list_projects():
        return jsonify({'projects': [info(p) for p in projects.list()],
                        'demos': [dict(id=key, **demo) for key, demo in DEMOS.items()]})

    @film.post('/import')
    def upload():
        raw, filename = uploaded('scan', MAX_UPLOAD, '扫描文件')
        title = request.form.get('title', '').strip() or filename.rsplit('.', 1)[0][:100]
        settings = initial_settings(request.form.get('encoding', 'srgb'))
        return jsonify(info(projects.create(title, raw, settings, '导入扫描', filename))), 201

    @film.post('/demos/<key>')
    def demo(key):
        if key not in DEMOS:
            raise Invalid('无法识别所选演示')
        title, raw, settings = make_demo(key)
        return jsonify(info(projects.create(title, raw, settings, '合成演示', key + '.png'))), 201

    @film.post('/restore')
    def restore():
        raw, _ = uploaded('archive', MAX_UPLOAD + 128 * 1024, '工程ZIP')
        return jsonify(info(projects.restore(raw))), 201

    @film.get('/projects/<project_id>')
    def read(project_id):
        return jsonify(info(projects.load(project_id)))

    @film.patch('/projects/<project_id>')
    def edit(project_id):
        document = request.get_json(silent=True)
        if not isinstance(document, dict) or set(document) != {'version', 'changes'}:
            raise Invalid('更改需包含当前工程修订号和设置')
        project = projects.edit(project_id, document['version'], document['changes'],
                                lambda settings: workbench.check_setting(project_id, settings))
        return jsonify(info(project))

    @film.post('/projects/<project_id>/flat')
    def attach_flat(project_id):
        raw, filename = uploaded('flat', MAX_UPLOAD, '空拍')
        return jsonify(info(workbench.attach_flat(project_id, raw, filename)))

    @film.delete('/projects/<project_id>/flat')
    def remove_flat(project_id):
        return jsonify(info(projects.remove_flat(project_id)))

    def image(data, kind='image/png', download=None):
        response = send_file(BytesIO(data), mimetype=kind, as_attachment=download is not None,
                             download_name=download)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @film.get('/projects/<project_id>/preview/<view>.png')
    def preview(project_id, view):
        return image(workbench.preview(projects.load(project_id), view))

    @film.get('/projects/<project_id>/metrics')
    def metrics(project_id):
        return jsonify(workbench.metrics(projects.load(project_id)))

    def location():
        return [request.args.get(axis, type=int) for axis in ('x', 'y')]

    @film.get('/projects/<project_id>/probe')
    def probe(project_id):
        return jsonify(workbench.probe(projects.load(project_id), location()))

    @film.get('/projects/<project_id>/loupe.png')
    def loupe(project_id):
        return image(workbench.loupe(projects.load(project_id), location()))

    @film.get('/projects/<project_id>/export/<name>')
    def export(project_id, name):
        project = projects.load(project_id)
        if name == 'project.zip':
            return image(projects.archive(project), 'application/zip', 'FilmBench.zip')
        if name == 'original':
            return image(projects.raw(project_id), FORMATS[project['scan_format']][1], project['filename'])
        data, mimetype = workbench.download(project, name)
        return image(data, mimetype, name)

    app.register_blueprint(film)
