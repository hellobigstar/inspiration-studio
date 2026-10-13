import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import download_models as dl


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / 'test.safetensors'
        self.content = b'model-fixture-for-tests'
        self.entry = {'bytes': len(self.content), 'sha256': hashlib.sha256(self.content).hexdigest()}

    def test_verified_existing_skips_network(self):
        self.target.write_bytes(self.content)
        with patch.object(dl.urllib.request, 'urlopen') as request:
            dl.download_file('https://example.invalid/model', self.target, self.entry)
            request.assert_not_called()

    def test_never_overwrites_mismatched_existing_model(self):
        self.target.write_bytes(b'user-model')
        with patch.object(dl.urllib.request, 'urlopen') as request:
            with self.assertRaises(ValueError):
                dl.download_file('https://example.invalid/model', self.target, self.entry)
            request.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'user-model')

    def test_resumes_and_verifies(self):
        part = self.target.with_name(self.target.name + '.part')
        part.write_bytes(self.content[:5])
        def respond(request, **kwargs):
            self.assertEqual(request.headers['Range'], 'bytes=5-')
            return Response(self.content[5:], 206, {'Content-Range': f'bytes 5-{len(self.content)-1}/{len(self.content)}'})
        with patch.object(dl.urllib.request, 'urlopen', side_effect=respond):
            dl.download_file('https://example.invalid/model', self.target, self.entry)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertFalse(part.exists())

    def test_bad_hash_not_installed(self):
        with patch.object(dl.urllib.request, 'urlopen', return_value=Response(b'x' * len(self.content))):
            with self.assertRaises(ValueError):
                dl.download_file('https://example.invalid/model', self.target, self.entry)
        self.assertFalse(self.target.exists())

    def test_wrong_resume_range_rejected(self):
        self.target.with_name(self.target.name + '.part').write_bytes(self.content[:5])
        with patch.object(dl.urllib.request, 'urlopen', return_value=Response(self.content, 206, {'Content-Range': 'bytes 0-21/22'})):
            with self.assertRaises(ValueError):
                dl.download_file('https://example.invalid/model', self.target, self.entry)
        self.assertFalse(self.target.exists())

    def test_manifest_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            dl.target_for(Path(self.tmp.name), {'path': '../outside.safetensors'})


if __name__ == '__main__':
    unittest.main()
