"""Portable configuration for the local-only Qwen Studio server."""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    """A configuration problem that can be shown directly in the terminal."""


def _path(value, name, base):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{name} must be a non-empty path string.')
    candidate = Path(os.path.expandvars(value)).expanduser()
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def validate_backend_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ConfigError('backend_url must be an HTTP loopback URL.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError('backend_url contains an invalid host or port.') from exc
    host = parsed.hostname
    try:
        loopback = host == 'localhost' or ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        loopback = False
    if (parsed.scheme != 'http' or not loopback or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or parsed.netloc.endswith(':')
            or (port is not None and not 1 <= port <= 65535)):
        raise ConfigError('backend_url must use HTTP and a loopback host, without credentials or a URL path.')
    return value.rstrip('/')


@dataclass(frozen=True)
class StudioConfig:
    config_path: Path
    comfyui_dir: Path
    backend_url: str
    port: int
    data_dir: Path
    max_upload_mb: int

    @property
    def input_dir(self):
        return self.comfyui_dir / 'input' / 'qwen-studio'

    @property
    def output_dir(self):
        return self.comfyui_dir / 'output'


def load_config(path=None, environ=None, repo_root=None):
    environ = os.environ if environ is None else environ
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    selected = path or environ.get('QWEN_STUDIO_CONFIG') or repo_root / 'config.json'
    filename = Path(selected).expanduser().resolve()
    try:
        values = json.loads(filename.read_text(encoding='utf-8-sig'))
    except FileNotFoundError as exc:
        raise ConfigError(f'Configuration not found: {filename}. Copy config.example.json to config.json first.') from exc
    except (OSError, ValueError) as exc:
        raise ConfigError(f'Cannot read configuration {filename}: {exc}') from exc
    if not isinstance(values, dict):
        raise ConfigError('Configuration must be a JSON object.')
    known = {'comfyui_dir', 'backend_url', 'port', 'data_dir', 'max_upload_mb'}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError('Unknown configuration fields: ' + ', '.join(unknown))
    core = _path(values.get('comfyui_dir'), 'comfyui_dir', filename.parent)
    data = _path(values.get('data_dir', './data'), 'data_dir', filename.parent)
    backend_url = validate_backend_url(values.get('backend_url', 'http://127.0.0.1:8189'))
    port = values.get('port', 8190)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError('port must be an integer from 1 to 65535.')
    backend_port = urlsplit(backend_url).port or 80
    if port == backend_port:
        raise ConfigError('Studio port must differ from the ComfyUI backend port.')
    maximum = values.get('max_upload_mb', 20)
    if type(maximum) is not int or not 1 <= maximum <= 100:
        raise ConfigError('max_upload_mb must be an integer from 1 to 100.')
    return StudioConfig(filename, core, backend_url, port, data, maximum)
