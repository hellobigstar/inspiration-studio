"""Portable server regression tests; no model download, GPU, or running ComfyUI needed."""
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.config import ConfigError, load_config, validate_backend_url
from studio import server


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'config.json'

    def write(self, **overrides):
        data = {'comfyui_dir': './ComfyUI'}
        data.update(overrides)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return self.path

    def test_relative_paths_follow_configuration_directory(self):
        config = load_config(self.write(data_dir='../history'), environ={})
        self.assertEqual(config.comfyui_dir, self.root / 'ComfyUI')
        self.assertEqual(config.data_dir, (self.root / '../history').resolve())
        self.assertEqual(config.port, 8190)
        self.assertEqual(config.input_dir, self.root / 'ComfyUI/input/qwen-studio')

    def test_cli_environment_default_precedence(self):
        self.write()
        other = self.root / 'other.json'
        other.write_text(json.dumps({'comfyui_dir': './elsewhere', 'port': 8200}), encoding='utf-8')
        env = {'QWEN_STUDIO_CONFIG': str(other)}
        self.assertEqual(load_config(self.path, environ=env).port, 8190)
        self.assertEqual(load_config(environ=env).port, 8200)
        self.assertEqual(load_config(environ={}, repo_root=self.root).port, 8190)

    def test_invalid_ports_and_sizes_rejected(self):
        for value in (0, -1, 65536, True, 8190.0, '8190', 8189):
            with self.subTest(port=value), self.assertRaises(ConfigError):
                load_config(self.write(port=value))
        for value in (0, 101, True, '20'):
            with self.subTest(max_upload_mb=value), self.assertRaises(ConfigError):
                load_config(self.write(max_upload_mb=value))

    def test_only_plain_http_loopback_backend_allowed(self):
        for url in ('http://127.0.0.1:8189/', 'http://localhost:8188', 'http://[::1]:8189'):
            with self.subTest(url=url):
                self.assertEqual(validate_backend_url(url), url.rstrip('/'))
        for url in ('https://127.0.0.1:8189', 'http://192.168.0.2:8189', 'http://example.com',
                    'http://user:pass@127.0.0.1:8189', 'http://127.0.0.1:8189/api',
                    'http://127.0.0.1:8189?x=1', 'http://127.0.0.1:0', 'http://127.0.0.1:99999',
                    'http://0.0.0.0:8189', 'http://localhost:', ' http://localhost:8189',
                    'http://local\nhost:8189', 'file:///tmp/backend', None):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate_backend_url(url)

    def test_missing_invalid_and_unknown_configuration_fields(self):
        for data in ([], {}, {'comfyui_dir': 42}, {'comfyui_dir': './ComfyUI', 'bind': '0.0.0.0'}):
            self.path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(data=data), self.assertRaises(ConfigError):
                load_config(self.path)
        with self.assertRaises(ConfigError):
            load_config(self.root / 'missing.json')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps({'comfyui_dir': './ComfyUI', 'port': 8290}), encoding='utf-8')
        server.configure(load_config(config_path))
        server.CORE.mkdir()
        (server.CORE / 'main.py').write_text('# temporary ComfyUI marker', encoding='utf-8')
        server.OUTPUT.mkdir()
        server.initialize_state()

    @staticmethod
    def image_bytes(mode='RGBA', size=(64, 48), color=(10, 20, 30, 0), fmt='PNG'):
        raw = io.BytesIO()
        Image.new(mode, size, color).save(raw, format=fmt)
        return raw.getvalue()

    @staticmethod
    def job(mode='text', images=None):
        return {'id': 'a' * 32, 'mode': mode, 'prompt': '把碗改成橙色', 'keep': '保留文字和背景',
                'images': images or [], 'resolution': 768, 'seed': 0, 'state': 'queued',
                '_prompt_id': 'test-prompt-id', '_created': time.time(), 'stage': '等待模型'}

    def test_workflow_modes_preserve_sampler_attention_and_alpha_links(self):
        for mode, count in (('text', 0), ('reference', 2), ('edit', 1)):
            refs = [{'id': str(i) * 32} for i in range(1, count + 1)]
            with self.subTest(mode=mode):
                api, ui = server.build_workflows(self.job(mode, refs))
                self.assertEqual(api['4']['inputs']['model'], ['11', 0])
                self.assertEqual(api['11']['inputs']['attention'], 'pytorch attention')
                self.assertEqual(api['8']['inputs']['cfg'], 1)
                self.assertEqual(api['8']['inputs']['noise_seed'], 0)
                self.assertEqual(len(api['7']['inputs']['sigmas'].split(',')), 9)
                self.assertEqual(api['5']['inputs']['resolution'], 768)
                nodes = {n['id']: n for n in ui['nodes']}
                self.assertEqual(nodes[5]['inputs'][2]['name'], 'vae')
                self.assertEqual(nodes[5]['inputs'][2]['link'], 3)
                image_keys = [key for key in api['5']['inputs'] if key.startswith('images.')]
                self.assertEqual(len(image_keys), count)
                for i in range(1, count + 1):
                    loader, alpha = str(11 + i), str(20 + i)
                    self.assertEqual(api['5']['inputs'][f'images.image_{i}'], [alpha, 0])
                    self.assertEqual(api[alpha]['class_type'], 'JoinImageWithAlpha')
                    self.assertEqual(api[alpha]['inputs']['alpha'], [loader, 1])
                for link_id, src, src_slot, dst, dst_slot, _ in ui['links']:
                    self.assertIn(link_id, nodes[src]['outputs'][src_slot]['links'])
                    self.assertEqual(nodes[dst]['inputs'][dst_slot]['link'], link_id)
                self.assertEqual(len({link[0] for link in ui['links']}), len(ui['links']))
                self.assertEqual('保留文字和背景' in api['5']['inputs']['prompt'], mode == 'edit')

    def test_upload_preserves_alpha_and_uses_only_owned_input_directory(self):
        item = server.put_image(self.image_bytes())
        path = server.input_file(item['id'])
        self.assertTrue(path.is_relative_to(server.CORE / 'input'))
        self.assertEqual((item['width'], item['height']), (64, 48))
        with Image.open(path) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertIn(item['id'], server.read_json(server.DATA / 'uploads.json'))
        for invalid in ('../outside', '/etc/passwd', 'z' * 32):
            with self.assertRaises(server.UserError):
                server.input_file(invalid)

    def test_upload_rejects_invalid_format_dimensions_and_payload_size(self):
        payloads = [b'not an image', b'', self.image_bytes(size=(31, 50)), self.image_bytes(fmt='GIF')]
        for raw in payloads:
            with self.subTest(length=len(raw)), self.assertRaises(server.UserError):
                server.put_image(raw)
        with patch.object(server, 'MAX_IMAGE', 10), self.assertRaises(server.UserError):
            server.put_image(self.image_bytes())
        self.assertEqual(server.UPLOADS, {})

    def test_post_timeout_keeps_preallocated_id_and_never_resubmits(self):
        submitted = []
        def fake_backend(path, payload=None, **kwargs):
            if path == '/queue':
                return {'queue_running': [], 'queue_pending': []}
            submitted.append(payload)
            raise TimeoutError('accepted but response lost')
        with patch.object(server, 'backend', side_effect=fake_backend), patch.object(server.threading, 'Thread') as watcher:
            result = server.generate({'mode': 'text', 'prompt': '测试', 'seed': 0})
        self.assertEqual(len(submitted), 1)
        job = server.JOBS[result['id']]
        self.assertEqual(job['_prompt_id'], submitted[0]['prompt_id'])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(server.read_json(server.DATA / (job['id'] + '.json'))['_prompt_id'], job['_prompt_id'])
        watcher.assert_called_once()
        watcher.return_value.start.assert_called_once()

    def test_active_job_blocks_a_second_submission(self):
        server.JOBS['a' * 32] = self.job()
        with patch.object(server, 'backend') as request:
            with self.assertRaises(server.UserError) as caught:
                server.generate({'mode': 'text', 'prompt': '第二张'})
        self.assertEqual(caught.exception.status, 409)
        request.assert_not_called()

    def test_missing_result_file_fails_without_waiting_for_queue(self):
        job = self.job()
        result = {job['_prompt_id']: {'status': {'completed': True}, 'outputs': {'10': {'images': [
            {'subfolder': 'generated', 'filename': 'missing.png', 'type': 'output'}]}}}}
        with patch.object(server, 'backend', return_value=result) as request, patch.object(server.time, 'sleep'):
            server.watch(job)
        self.assertEqual(job['state'], 'error')
        self.assertIn('不存在', job['error'])
        request.assert_called_once_with('/history/' + job['_prompt_id'])

    def test_finished_job_persists_dimensions_and_download_links(self):
        job = self.job()
        target = server.OUTPUT / 'result.png'
        target.write_bytes(self.image_bytes())
        result = {job['_prompt_id']: {'status': {'completed': True}, 'outputs': {'10': {'images': [
            {'subfolder': '', 'filename': target.name, 'type': 'output'}]}}}}
        with patch.object(server, 'backend', return_value=result), patch.object(server.time, 'sleep'):
            server.watch(job)
        self.assertEqual(job['state'], 'success')
        self.assertEqual((job['result']['width'], job['result']['height']), (64, 48))
        self.assertEqual(job['result']['workflow_url'], '/api/jobs/' + job['id'] + '/workflow')
        self.assertEqual(server.read_json(server.DATA / (job['id'] + '.json'))['state'], 'success')
        self.assertNotIn('_output', server.public(job))

    def test_persisted_output_path_cannot_escape_output_root(self):
        outside = self.root / 'private.png'
        outside.write_bytes(self.image_bytes())
        with self.assertRaises(server.UserError):
            server.result_file({'_output': str(outside)})

    def test_missing_node_and_model_diagnostics_do_not_expose_paths(self):
        api = server.read_json(ROOT / 'workflows/Qwen-Image-2.1-Turbo-1024.api.json')
        info = {node['class_type']: {} for node in api.values()}
        info['LoadImage'] = {}
        for name, field in (('UNETLoader', 'unet_name'), ('CLIPLoader', 'clip_name'), ('VAELoader', 'vae_name')):
            choices = [n['inputs'][field] for n in api.values() if n['class_type'] == name]
            if name == 'UNETLoader':
                choices = []
            info[name] = {'input': {'required': {field: [choices]}}}
        with patch.object(server, 'backend', side_effect=[{}, info]):
            result = server.health(force=True)
        self.assertFalse(result['ready'])
        self.assertEqual(result['missing_nodes'], ['JoinImageWithAlpha'])
        self.assertEqual(result['missing_models'], ['qwen_image_2.1_turbo_int8_convrot.safetensors'])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_host_and_origin_follow_configured_port(self):
        handler = object.__new__(server.Handler)
        handler.headers = {'Host': '127.0.0.1:8290', 'Origin': 'http://127.0.0.1:8290'}
        handler.allowed()
        for headers in ({'Host': '127.0.0.1:8190'}, {'Host': 'evil.test:8290'},
                        {'Host': '127.0.0.1:8290', 'Origin': 'https://evil.test'},
                        {'Host': '127.0.0.1:8290', 'Sec-Fetch-Site': 'cross-site'}):
            handler.headers = headers
            with self.assertRaises(server.UserError):
                handler.allowed()


if __name__ == '__main__':
    unittest.main()
