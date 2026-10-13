"""Explicit, resumable downloads of the three official model files. No GPU imports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def valid(path, entry):
    return path.is_file() and path.stat().st_size == entry['bytes'] and sha256(path) == entry['sha256']


def download_file(url, target, entry, attempts=5):
    """Never replace an existing model that fails verification; stage in a .part file."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if valid(target, entry):
            print('Verified existing: ' + target.name, flush=True)
            return
        raise ValueError('Existing file differs from the manifest. Move it aside yourself before retrying: ' + str(target))
    part = target.with_name(target.name + '.part')
    if part.is_symlink() or part.resolve().parent != target.parent.resolve():
        raise ValueError('Unsafe partial-download path: ' + str(part))
    size = entry['bytes']
    for attempt in range(attempts):
        offset = part.stat().st_size if part.exists() else 0
        if offset == size:
            break
        if offset > size:
            raise ValueError('Partial download is larger than expected: ' + str(part))
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'InspirationStudio/1.0', **({'Range': f'bytes={offset}-'} if offset else {})})
            with urllib.request.urlopen(req, timeout=90) as response:
                if response.status == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-') or not content_range.endswith('/' + str(size)):
                        raise ValueError('Server returned an unexpected Content-Range.')
                elif response.status == 200:
                    offset = 0
                else:
                    raise ValueError('Unexpected HTTP response: ' + str(response.status))
                written = offset
                last = time.monotonic()
                with part.open('ab' if offset else 'wb') as stream:
                    while True:
                        chunk = response.read(2 * 1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > size:
                            raise ValueError('Download exceeded the expected file size.')
                        stream.write(chunk)
                        if time.monotonic() - last >= 5:
                            print(f'{target.name}: {written / size:.1%} ({written / 1e9:.2f}/{size / 1e9:.2f} GB)', flush=True)
                            last = time.monotonic()
            if part.stat().st_size != size:
                raise OSError('Incomplete download; resuming on retry.')
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt + 1 == attempts:
                raise
            print(f'Retry {attempt + 1}/{attempts}: {type(exc).__name__}', flush=True)
            time.sleep(min(attempt + 1, 5))
    if not valid(part, entry):
        raise ValueError('SHA-256 verification failed. No model was installed. Move aside the .part file to retry: ' + str(part))
    os.replace(part, target)
    print('Verified and installed: ' + target.name, flush=True)


def model_directory(config_path):
    from studio.config import load_config
    directory = load_config(config_path).comfyui_dir
    if not (directory / 'main.py').is_file():
        raise ValueError('comfyui_dir must be the ComfyUI folder containing main.py.')
    return directory / 'models'


def target_for(directory, entry):
    target = (directory / entry['path']).resolve()
    if not target.is_relative_to(directory.resolve()) or not target.name.endswith('.safetensors'):
        raise ValueError('Unsafe path in model manifest.')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=os.environ.get('QWEN_STUDIO_CONFIG', str(ROOT / 'config.json')))
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--download', action='store_true', help='Download after showing the model license notice.')
    action.add_argument('--check-only', action='store_true', help='Verify local sizes and SHA-256; no network.')
    parser.add_argument('--yes', action='store_true', help='Skip the download size/usage confirmation; read the upstream license yourself first.')
    args = parser.parse_args(argv)
    manifest = json.loads((ROOT / 'models.json').read_text(encoding='utf-8'))
    files = manifest['files']
    total = sum(item['bytes'] for item in files)
    print('Official source: https://huggingface.co/' + manifest['source'])
    print('Model license (separate from the studio MIT license): ' + manifest['license_url'])
    print(f'{len(files)} model files, {total:,} bytes ({total / 1e9:.2f} GB). Research/evaluation use; commercial use needs separate authorization.')
    for item in files:
        print(f"  {item['path']}  {item['bytes'] / 1e9:.2f} GB")
    if not args.download and not args.check_only:
        print('Plan only. No files downloaded. Use --download or --check-only after configuring config.json.')
        return 0
    directory = model_directory(args.config)
    targets = [(target_for(directory, entry), entry) for entry in files]
    if args.check_only:
        success = True
        for target, entry in targets:
            ok = valid(target, entry)
            print(('PASS ' if ok else 'MISSING / MISMATCH ') + entry['path'])
            success &= ok
        return 0 if success else 1
    if not args.yes and input('Download these model files to your ComfyUI models folder? Type yes: ').strip().lower() != 'yes':
        print('No download started.')
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    needed = 0
    for target, entry in targets:
        if target.exists():
            if not valid(target, entry):
                raise ValueError('An existing model differs from the manifest; no download started: ' + str(target))
        else:
            part = target.with_name(target.name + '.part')
            needed += max(0, entry['bytes'] - (part.stat().st_size if part.exists() else 0))
    if shutil.disk_usage(directory).free < needed + 1024**3:
        raise ValueError('Not enough free disk space for the remaining download plus 1 GiB reserve.')
    for target, entry in targets:
        url = f"https://huggingface.co/{manifest['source']}/resolve/{manifest['source_revision']}/{quote(entry['path'], safe='/')}"
        download_file(url, target, entry)
    print('All three files verified. Restart or refresh ComfyUI, then run studio/server.py --check.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nCancelled. Partial files are kept for the next download.', file=sys.stderr)
        sys.exit(130)
