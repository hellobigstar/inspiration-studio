"""Local-only Inspiration Studio, connected to a separately installed ComfyUI."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
import base64
import argparse
import copy
import io
import json
import re
import sys
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from PIL import Image, ImageOps
if __package__:
    from .config import ConfigError, load_config
else:
    # Windows embedded Python may omit the script directory from sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import ConfigError, load_config

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = None
CORE = DATA = INPUT = OUTPUT = None
BACKEND = 'http://127.0.0.1:8189'
PORT = 8190
LOCK = threading.RLock()
JOBS = {}
UPLOADS = {}
MAX_IMAGE = 20 * 1024 * 1024
HEALTH_LOCK = threading.Lock()
HEALTH_CACHE = {'time': 0, 'missing_nodes': [], 'missing_models': []}
REQUIRED_IMAGE_NODES = {'LoadImage', 'JoinImageWithAlpha'}
Image.MAX_IMAGE_PIXELS = 24_000_000


class UserError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def configure(config):
    """Apply validated configuration without writing files or contacting ComfyUI."""
    global CONFIG, CORE, DATA, INPUT, OUTPUT, BACKEND, PORT, MAX_IMAGE
    CONFIG = config
    CORE, DATA = config.comfyui_dir, config.data_dir
    INPUT, OUTPUT = config.input_dir, config.output_dir
    BACKEND, PORT = config.backend_url, config.port
    MAX_IMAGE = config.max_upload_mb * 1024 * 1024
    with HEALTH_LOCK:
        HEALTH_CACHE['time'] = 0


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def input_file(uid):
    if not valid_id(uid):
        raise UserError('未找到图片。', 404)
    root = INPUT.resolve()
    target = (INPUT / (uid + '.png')).resolve()
    if not root.is_relative_to((CORE / 'input').resolve()) or not target.is_relative_to(root):
        raise UserError('图片路径无效。', 403)
    return target


def result_file(job):
    path = Path(job.get('_output', '')).resolve()
    if not path.is_relative_to(OUTPUT.resolve()) or not path.is_file():
        raise UserError('结果文件已移动或不存在。', 404)
    return path


def local_problems():
    problems = []
    if not CORE.is_dir() or not (CORE / 'main.py').is_file():
        problems.append(f'comfyui_dir does not contain ComfyUI/main.py: {CORE}')
    # Never follow an input subdirectory symlink out of the configured input root.
    if not INPUT.resolve().is_relative_to((CORE / 'input').resolve()):
        problems.append('The qwen-studio input directory resolves outside ComfyUI/input.')
    for name in ('Qwen-Image-2.1-Turbo-1024.api.json', 'Qwen-Image-2.1-Turbo-1024.workflow.json'):
        if not (ROOT / 'workflows' / name).is_file():
            problems.append(f'Missing bundled workflow: {name}')
    return problems


def compatibility(info):
    api = read_json(ROOT / 'workflows' / 'Qwen-Image-2.1-Turbo-1024.api.json')
    required = {node['class_type'] for node in api.values()} | REQUIRED_IMAGE_NODES
    missing_nodes = sorted(required - info.keys())
    missing_models = []
    for loader, field in (('UNETLoader', 'unet_name'), ('CLIPLoader', 'clip_name'), ('VAELoader', 'vae_name')):
        if loader not in info:
            continue
        schema = info[loader].get('input', {}).get('required', {}).get(field, [])
        choices = schema[0] if schema and isinstance(schema[0], list) else []
        for node in api.values():
            if node['class_type'] == loader and node['inputs'][field] not in choices:
                missing_models.append(node['inputs'][field])
    return {'missing_nodes': missing_nodes, 'missing_models': missing_models}


def health(force=False):
    """Safe diagnostics for the browser: no filesystem paths or backend tracebacks."""
    response = {'ready': False, 'backend': BACKEND, 'busy': active(), 'service': 'inspiration-studio',
                'missing_nodes': [], 'missing_models': [], 'max_upload_mb': MAX_IMAGE // (1024 * 1024)}
    try:
        q = backend('/queue', timeout=3)
        response['busy'] = response['busy'] or bool(q.get('queue_running') or q.get('queue_pending'))
        with HEALTH_LOCK:
            if force or time.monotonic() - HEALTH_CACHE['time'] > 10:
                issues = compatibility(backend('/object_info', timeout=10))
                HEALTH_CACHE.update(issues, time=time.monotonic())
            response.update({k: HEALTH_CACHE[k] for k in ('missing_nodes', 'missing_models')})
        if response['missing_nodes']:
            response['message'] = 'ComfyUI 缺少必要节点，请按 README 更新到受支持版本。'
        elif response['missing_models']:
            response['message'] = '缺少模型文件，请完成模型下载并重启 ComfyUI。'
        else:
            response.update(ready=True, message='本机模型服务已就绪')
    except Exception:
        response['message'] = '本机模型服务尚未就绪，请检查 ComfyUI 是否已启动及 backend_url 配置。'
    response['issues'] = [] if response['ready'] else [response['message']]
    return response


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class NoBackendRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError('ComfyUI redirects are not allowed.')


def backend(path, payload=None, timeout=15):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(BACKEND + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    # Loopback requests must not pass through a configured HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoBackendRedirect()).open(req, timeout=timeout) as r:
        return json.load(r)


def active():
    with LOCK:
        return any(j['state'] in ('queued', 'running') for j in JOBS.values())


def public(job):
    j = {k: copy.deepcopy(v) for k, v in job.items() if not k.startswith('_')}
    j['elapsed_seconds'] = round(max(0, job.get('_finished', time.time()) - job['_created']), 1)
    return j


def persist(job):
    save_json(DATA / (job['id'] + '.json'), job)


def put_image(raw):
    if not raw or len(raw) > MAX_IMAGE:
        raise UserError(f'图片大小须在 {MAX_IMAGE // (1024 * 1024)} MB 以内。')
    try:
        with Image.open(io.BytesIO(raw)) as src:
            if src.format not in ('PNG', 'JPEG', 'WEBP'):
                raise UserError('请使用 PNG、JPG 或 WebP 图片。')
            if src.width * src.height > 24_000_000 or min(src.size) < 32:
                raise UserError('图片至少 32 像素，且不超过 2400 万像素。')
            # Normalize orientation; do not propagate GPS or other EXIF metadata.
            im = ImageOps.exif_transpose(src).convert('RGBA')
            im.load()
    except UserError:
        raise
    except Exception:
        raise UserError('无法读取这张图片，请重新选择 PNG、JPG 或 WebP。')
    uid = uuid.uuid4().hex
    im.save(input_file(uid))
    item = {'id': uid, 'url': '/media/input/' + uid, 'width': im.width, 'height': im.height}
    with LOCK:
        UPLOADS[uid] = item
        save_json(DATA / 'uploads.json', UPLOADS)
    return item


def build_workflows(job):
    api = read_json(ROOT / 'workflows' / 'Qwen-Image-2.1-Turbo-1024.api.json')
    ui = read_json(ROOT / 'workflows' / 'Qwen-Image-2.1-Turbo-1024.workflow.json')
    prompt = job['prompt']
    if job['mode'] == 'edit':
        prompt = '编辑提供的图片。修改要求：' + prompt
        if job['keep']:
            prompt += '\n请尽量保持以下内容不变：' + job['keep']
    elif job['mode'] == 'reference':
        prompt = '根据提供的参考图片创作一张新图。图片按上传顺序编号为图1、图2。\n创作要求：' + prompt
    api['5']['inputs'].update(prompt=prompt, resolution=job['resolution'])
    api['8']['inputs']['noise_seed'] = job['seed']
    prefix = 'QwenStudio/' + job['mode'] + '/' + job['id']
    api['10']['inputs']['filename_prefix'] = prefix
    ui['id'] = str(uuid.uuid4())
    nodes = {n['id']: n for n in ui['nodes']}
    nodes[5]['widgets_values'] = [prompt, '', job['resolution']]
    nodes[5]['widgets_values_named'].update(prompt=prompt, resolution=job['resolution'])
    nodes[8]['widgets_values'][1] = job['seed']
    nodes[8]['widgets_values_named']['noise_seed'] = job['seed']
    nodes[10]['widgets_values'] = [prefix]
    nodes[10]['widgets_values_named']['filename_prefix'] = prefix
    nodes[5]['title'] = {'text': '画面描述', 'reference': '参考图创作', 'edit': '图片修改'}[job['mode']]
    # Keep the VAE socket in slot 2; extra dynamic image sockets append after it.
    for i, item in enumerate(job['images'], 1):
        nid, lid = 11 + i, 13 + i
        alpha_id = 20 + i
        name = 'qwen-studio/' + item['id'] + '.png'
        api[str(nid)] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        api[str(alpha_id)] = {'class_type': 'JoinImageWithAlpha', 'inputs': {'image': [str(nid), 0], 'alpha': [str(nid), 1]}}
        api['5']['inputs']['images.image_' + str(i)] = [str(alpha_id), 0]
        if i == 1:
            nodes[5]['inputs'][1]['link'] = lid
            slot = 1
        else:
            slot = len(nodes[5]['inputs'])
            nodes[5]['inputs'].append({'name': 'images.image_' + str(i), 'type': 'IMAGE', 'link': lid, 'shape': 7})
        ui['nodes'].append({'id': nid, 'type': 'LoadImage', 'title': '参考图 ' + str(i),
            'pos': [40, 630 + (i - 1) * 350], 'size': [410, 310], 'flags': {}, 'order': 0, 'mode': 0,
            'inputs': [], 'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [30 + i * 2]},
                                     {'name': 'MASK', 'type': 'MASK', 'links': [31 + i * 2]}],
            'properties': {'Node name for S&R': 'LoadImage', 'cnr_id': 'comfy-core'},
            'widgets_values': [name, 'image']})
        ui['nodes'].append({'id': alpha_id, 'type': 'JoinImageWithAlpha', 'title': '保留参考图透明度',
            'pos': [510, 820 + (i - 1) * 200], 'size': [280, 100], 'flags': {}, 'order': 1, 'mode': 0,
            'inputs': [{'name': 'image', 'type': 'IMAGE', 'link': 30 + i * 2},
                       {'name': 'alpha', 'type': 'MASK', 'link': 31 + i * 2}],
            'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [lid]}],
            'properties': {'Node name for S&R': 'JoinImageWithAlpha', 'cnr_id': 'comfy-core'}, 'widgets_values': []})
        ui['links'].extend([[lid, alpha_id, 0, 5, slot, 'IMAGE'],
                            [30 + i * 2, nid, 0, alpha_id, 0, 'IMAGE'],
                            [31 + i * 2, nid, 1, alpha_id, 1, 'MASK']])
    ui['last_node_id'] = max(n['id'] for n in ui['nodes'])
    ui['last_link_id'] = max(link[0] for link in ui['links'])
    ui.get('extra', {}).pop('linearData', None)
    ui.setdefault('extra', {})['qwenStudio'] = {k: job[k] for k in ('mode', 'prompt', 'keep', 'seed', 'resolution')}
    return api, ui


def fail(job, message):
    with LOCK:
        job.update(state='error', stage='生成未完成', error=message, _finished=time.time())
        persist(job)


def watch(job):
    missing = 0
    while True:
        time.sleep(1.5)
        try:
            history = backend('/history/' + job['_prompt_id'])
            result = history.get(job['_prompt_id'])
            if result:
                status = result.get('status', {})
                if status.get('status_str') == 'error':
                    messages = status.get('messages', [])
                    errors = [m[1] for m in messages if m[0] in ('execution_error', 'execution_interrupted')]
                    detail = str(errors[-1].get('exception_message', '任务被中断')) if errors else '请查看后台日志。'
                    (DATA / (job['id'] + '.execution-error.txt')).write_text(detail, encoding='utf-8')
                    if 'out of memory' in detail.lower():
                        detail = '显存不足。请关闭其他生图任务，改用 768 档再试。'
                    else:
                        detail = '模型执行失败或任务被中断，请查看终端和画室日志。'
                    fail(job, detail)
                    return
                pictures = result.get('outputs', {}).get('10', {}).get('images', [])
                if pictures:
                    pic = pictures[0]
                    file = (OUTPUT / pic.get('subfolder', '') / pic['filename']).resolve()
                    if pic.get('type', 'output') != 'output' or not file.is_relative_to(OUTPUT.resolve()) or not file.is_file():
                        fail(job, '生成已结束，但结果文件已移动或不存在。')
                        return
                    try:
                        with Image.open(file) as im:
                            im.load()
                            width, height = im.size
                    except Exception:
                        fail(job, '生成已结束，但结果图片无法读取。请查看模型日志。')
                        return
                    with LOCK:
                        job.update(state='success', stage='已完成', _finished=time.time(), _output=str(file),
                            result={'url': '/media/output/' + job['id'], 'filename': pic['filename'],
                                    'width': width, 'height': height,
                                    'workflow_url': '/api/jobs/' + job['id'] + '/workflow',
                                    'api_workflow_url': '/api/jobs/' + job['id'] + '/api-workflow'})
                        persist(job)
                    return
                if status.get('completed'):
                    fail(job, '任务结束，但没有返回图片。请查看后台日志。')
                    return
            q = backend('/queue')
            running = {row[1] for row in q.get('queue_running', [])}
            pending = {row[1] for row in q.get('queue_pending', [])}
            if job['_prompt_id'] in running:
                with LOCK:
                    job.update(state='running', stage='正在生成，首次加载模型可能较慢')
                missing = 0
            elif job['_prompt_id'] in pending:
                with LOCK:
                    job.update(state='queued', stage='等待模型空闲')
                missing = 0
            else:
                missing += 1
                if missing >= 8:
                    fail(job, '任务已离开队列，未找到结果。可能在专业界面被取消。')
                    return
        except Exception:
            # Keep the pending task: transient network failures are not generation failures.
            with LOCK:
                job['stage'] = '正在重新连接本机模型服务'
            if time.time() - job['_created'] > 3600:
                fail(job, '等待模型服务超过一小时，请检查后台日志。')
                return


def generate(body):
    mode = body.get('mode', 'text')
    prompt = body.get('prompt', '')
    keep = body.get('keep', '')
    ids = body.get('images', [])
    res = body.get('resolution', 1024)
    seed = body.get('seed')
    if mode not in ('text', 'reference', 'edit') or not isinstance(prompt, str) or not prompt.strip():
        raise UserError('请填写画面描述或修改要求。')
    if not isinstance(keep, str) or len(prompt) > 8000 or len(keep) > 4000:
        raise UserError('描述过长，请将画面描述控制在 8000 字以内，保留要求在 4000 字以内。')
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise UserError('参考图参数不正确。')
    if (mode == 'text' and ids) or (mode == 'reference' and not 1 <= len(ids) <= 2) or (mode == 'edit' and len(ids) != 1):
        raise UserError('参考图创作需要 1–2 张图片，图片修改需要 1 张图片。')
    if res not in (768, 1024) or isinstance(res, bool):
        raise UserError('请选择 768 或 1024 分辨率。')
    if seed is None:
        seed = secrets.randbelow(2**32)
    if type(seed) is not int or not 0 <= seed <= 2**53 - 1:
        raise UserError('种子需为 0 到 9007199254740991 之间的整数。')
    with LOCK:
        if active():
            raise UserError('已有图片正在生成，完成后再开始下一张。', 409)
        try:
            q = backend('/queue')
        except Exception:
            raise UserError('模型服务还未就绪，请先启动 ComfyUI 并检查配置。', 503)
        if q.get('queue_running') or q.get('queue_pending'):
            raise UserError('模型正在处理其他任务，请稍后再生成。', 409)
        if any(i not in UPLOADS or not input_file(i).is_file() for i in ids):
            raise UserError('参考图已不存在，请重新上传。')
        job = {'id': uuid.uuid4().hex, 'mode': mode, 'prompt': prompt.strip(), 'keep': keep.strip() if mode == 'edit' else '',
               'images': [UPLOADS[i] for i in ids], 'resolution': res, 'seed': seed,
               'state': 'queued', 'stage': '等待模型开始', '_created': time.time()}
        api, ui = build_workflows(job)
        save_json(DATA / (job['id'] + '.workflow.json'), ui)
        save_json(DATA / (job['id'] + '.api.json'), api)
        job['_prompt_id'] = str(uuid.uuid4())
        JOBS[job['id']] = job
        persist(job)
        try:
            answer = backend('/prompt', {'prompt': api, 'prompt_id': job['_prompt_id'], 'client_id': 'inspiration-studio',
                                        'extra_data': {'extra_pnginfo': {'workflow': ui}}}, timeout=30)
        except urllib.error.HTTPError as e:
            details = e.read().decode('utf-8', errors='replace')[:3000]
            (DATA / (job['id'] + '.submit-error.txt')).write_text(details, encoding='utf-8')
            fail(job, '模型工作流校验失败，详情已保存到画室日志目录。')
            raise UserError('模型工作流校验失败，详情已保存到画室日志目录。', 502)
        except (urllib.error.URLError, TimeoutError, OSError):
            # The server may have accepted it. Recover by the preassigned ID; do not resubmit.
            job['stage'] = '正在确认模型是否已接收任务'
        else:
            if not answer.get('prompt_id') or answer.get('node_errors'):
                fail(job, '模型未接受任务，请检查工作流。')
                raise UserError('模型未接受任务，请检查工作流。', 502)
            job['_prompt_id'] = answer['prompt_id']
        persist(job)
        threading.Thread(target=watch, args=(job,), daemon=True).start()
        return {'id': job['id']}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith(('GET /api/status', 'GET /api/jobs')):
            return
        super().log_message(fmt, *args)

    def send(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_bytes(raw, 'application/json; charset=utf-8', status)

    def send_bytes(self, raw, mime, status=200, download=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        hosts = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
        if PORT == 80:
            hosts.update(('127.0.0.1', 'localhost'))
        if self.headers.get('Host') not in hosts:
            raise UserError('仅允许从本机地址访问。', 403)
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            raise UserError('不允许跨站请求。', 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise UserError('不允许跨站请求。', 403)

    def do_GET(self):
        try:
            self.allowed()
            path = unquote(urlparse(self.path).path)
            if path == '/':
                return self.send_bytes((HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if path == '/favicon.ico':
                return self.send_bytes(b'', 'image/x-icon', 204)
            if path == '/api/status':
                return self.send(health())
            if path == '/api/jobs':
                with LOCK:
                    return self.send({'jobs': [public(j) for j in sorted(JOBS.values(), key=lambda j: j['_created'], reverse=True)[:50]]})
            if path == '/api/seed-image':
                existing = DATA / 'seed-image.json'
                if existing.exists():
                    item = read_json(existing)
                    if item.get('id') in UPLOADS and input_file(item['id']).is_file():
                        return self.send(item)
                sample = ROOT / 'assets' / 'example.png'
                if not sample.exists():
                    raise UserError('示例图片不可用，请上传自己的图片。', 404)
                item = put_image(sample.read_bytes())
                save_json(existing, item)
                return self.send(item)
            if path.startswith('/api/jobs/'):
                parts = path.split('/')
                with LOCK:
                    job = JOBS.get(parts[3])
                    if not job:
                        raise UserError('未找到这次生成记录。', 404)
                    if len(parts) == 4:
                        return self.send(public(job))
                    if len(parts) == 5 and parts[4] in ('workflow', 'api-workflow'):
                        suffix = '.workflow.json' if parts[4] == 'workflow' else '.api.json'
                        return self.send_bytes((DATA / (job['id'] + suffix)).read_bytes(), 'application/json', download='qwen-' + job['id'][:8] + suffix)
            if path.startswith('/media/input/'):
                uid = path.rsplit('/', 1)[-1]
                if uid not in UPLOADS:
                    raise UserError('未找到图片。', 404)
                return self.send_bytes(input_file(uid).read_bytes(), 'image/png')
            if path.startswith('/media/output/'):
                job = JOBS.get(path.rsplit('/', 1)[-1])
                if not job or job['state'] != 'success':
                    raise UserError('未找到结果。', 404)
                return self.send_bytes(result_file(job).read_bytes(), 'image/png', download='inspiration-' + job['id'][:8] + '.png' if 'download' in urlparse(self.path).query else None)
            raise UserError('页面不存在。', 404)
        except UserError as e:
            self.send({'error': str(e)}, e.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            self.send({'error': '本地服务读取失败，请查看 studio 日志。'}, 500)

    def do_POST(self):
        try:
            self.allowed()
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                raise UserError('请求格式不正确。', 415)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= ((MAX_IMAGE + 2) // 3) * 4 + 65536:
                raise UserError('请求过大。', 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise UserError('请求格式不正确。')
            path = urlparse(self.path).path
            if path == '/api/upload':
                try:
                    raw = base64.b64decode(body.get('data', ''), validate=True)
                except Exception:
                    raise UserError('图片数据不正确。')
                return self.send(put_image(raw))
            if path == '/api/generate':
                return self.send(generate(body), 202)
            raise UserError('接口不存在。', 404)
        except UserError as e:
            self.send({'error': str(e)}, e.status)
        except (ValueError, TypeError):
            self.send({'error': '请求格式不正确。'}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.send({'error': '本地服务处理失败，请查看 studio 日志。'}, 500)


def initialize_state():
    DATA.mkdir(parents=True, exist_ok=True)
    INPUT.mkdir(parents=True, exist_ok=True)
    JOBS.clear()
    UPLOADS.clear()
    if (DATA / 'uploads.json').exists():
        try:
            stored = read_json(DATA / 'uploads.json')
            for uid, item in stored.items():
                if valid_id(uid) and isinstance(item, dict) and input_file(uid).is_file():
                    with Image.open(input_file(uid)) as im:
                        UPLOADS[uid] = {'id': uid, 'url': '/media/input/' + uid, 'width': im.width, 'height': im.height}
        except (ValueError, OSError, AttributeError):
            print('Warning: some saved upload records could not be restored.', file=sys.stderr)
    for path in DATA.glob('*.json'):
        if not valid_id(path.stem):
            continue
        try:
            job = read_json(path)
            if (job.get('id') == path.stem and job.get('state') in ('queued', 'running', 'success', 'error')
                    and isinstance(job.get('_created'), (int, float))):
                job['images'] = [UPLOADS[item['id']] for item in job.get('images', []) if item.get('id') in UPLOADS]
                JOBS[job['id']] = job
                if job['state'] in ('queued', 'running') and not job.get('_prompt_id'):
                    fail(job, '保存的任务缺少模型编号，请重新生成。')
        except Exception:
            print(f'Warning: skipped invalid job record {path.name}.', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspiration Studio: a local web interface for Qwen-Image on ComfyUI.')
    parser.add_argument('--config', help='Configuration JSON path (otherwise QWEN_STUDIO_CONFIG or repo/config.json).')
    parser.add_argument('--check', action='store_true', help='Check configuration, backend, native nodes, and model choices; generate nothing.')
    parser.add_argument('--open-browser', action='store_true', help='Open the local studio after its listening socket is ready.')
    args = parser.parse_args(argv)
    try:
        configure(load_config(args.config))
    except ConfigError as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 2
    problems = local_problems()
    if problems:
        for issue in problems:
            print('ERROR: ' + issue, file=sys.stderr)
        return 1
    if args.check:
        report = health(force=True)
        print(f'Configuration: {CONFIG.config_path}')
        print(f'ComfyUI: {CORE}')
        print(f'Backend: {BACKEND}')
        print(f'Studio: http://127.0.0.1:{PORT}')
        if report['missing_nodes']:
            print('Missing native nodes: ' + ', '.join(report['missing_nodes']))
        if report['missing_models']:
            print('Missing model files in ComfyUI loader choices: ' + ', '.join(report['missing_models']))
        print(('PASS: ' if report['ready'] else 'FAIL: ') + report['message'])
        return 0 if report['ready'] else 1
    initialize_state()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    for job in JOBS.values():
        if job['state'] in ('queued', 'running'):
            threading.Thread(target=watch, args=(job,), daemon=True).start()
    print(f'Inspiration Studio ready: http://127.0.0.1:{PORT}', flush=True)
    if args.open_browser:
        webbrowser.open(f'http://127.0.0.1:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStudio stopped. ComfyUI and any generation already submitted continue running.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
