#!/usr/bin/env python3
"""Maintain/check the manifest for the canonical scaffold under assets/."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
# Files a project edits for itself: installed once, never overwritten by an upgrade.
PROJECT_OWNED = {'_system/verification.json', '.tink/pool.lock.json', '.tink/pool-overlay.json'}
PROJECT_OWNED_PATTERNS = [r'\.tink/skillsets/[^/]+\.json', r'stages/[^/]+/TRIGGERS(\.lock)?\.json']


def parse_version(text):
    if not isinstance(text, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', text):
        raise ValueError(f'Invalid package version {text!r}; expected numeric X.Y.Z.')
    return tuple(int(part) for part in text.split('.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check without writing')
    parser.add_argument('--version', help='Explicit release version for a manifest update')
    args = parser.parse_args()
    manifest_path = ASSETS / 'manifest.json'
    old = json.loads(manifest_path.read_text())
    version = old.get('version') if args.version is None else args.version
    try:
        current = parse_version(old.get('version'))
        requested = parse_version(version)
    except ValueError as error:
        parser.error(str(error))
    if requested < current:
        parser.error(f"Downgrade refused: manifest is {old['version']}; requested {version}.")
    files = {}
    for path in sorted(ASSETS.rglob('*')):
        if path.is_symlink():
            parser.exit(1, f'Symlink in package: {path}\n')
        if not path.is_file() or path == manifest_path or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        files[str(path.relative_to(ASSETS))] = hashlib.sha256(path.read_bytes()).hexdigest()
    owned = sorted(name for name in files if name in PROJECT_OWNED or any(re.fullmatch(p, name) for p in PROJECT_OWNED_PATTERNS))
    manifest = {'version': version, 'files': files, 'projectOwned': owned}
    if args.check:
        if manifest != old:
            parser.exit(1, 'Manifest does not match package contents. Update it for the release.\n')
        print('Package manifest matches all assets.')
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
