#!/usr/bin/env python3
"""Read-only audit of fixed dependency archives; never download or install them.

Integrity/path checks and inventories are not a security or legal guarantee.
Raw license, startup-hook and build-script evidence must stay in a local report
directory, not in the public repository.
"""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import zipfile


def safe_name(name: str) -> str:
    parts = PurePosixPath(name).parts
    if (not name or name.startswith('/') or '\\' in name or '\x00' in name
            or '..' in parts or re.match(r'^[A-Za-z]:', name)):
        raise ValueError('Unsafe archive member: ' + repr(name))
    return str(PurePosixPath(name))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def is_notice(name: str) -> bool:
    leaf = PurePosixPath(name).name.upper()
    return (any(leaf.startswith(prefix) for prefix in
                ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'OFL', 'COPYRIGHT', 'AUTHORS'))
            or ('THIRD' in leaf and any(word in leaf for word in ('NOTICE', 'LICENSE', 'LICENCE'))))


def save_evidence(directory: Path | None, name: str, data: bytes) -> None:
    if directory is None:
        return
    destination = directory / safe_name(name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def metadata_record(raw: bytes) -> dict:
    metadata = BytesParser().parsebytes(raw)
    return {key: metadata.get_all(key, []) for key in
            ('Name', 'Version', 'License', 'License-Expression', 'License-File', 'Classifier',
             'Requires-Python', 'Requires-Dist')}


def inspect_wheel(path: Path, evidence: Path | None = None) -> dict:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [safe_name(item.filename) for item in members if not item.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel file name')
        if sum(item.file_size for item in members) > path.stat().st_size * 30 + 64 * 1024**2:
            raise ValueError('Excessive uncompressed wheel size')
        for item in members:
            safe_name(item.filename)
            if stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK:
                raise ValueError('Wheel contains a symbolic link')
        # Nested dist-info belongs to vendored payload (e.g. setuptools), not
        # the outer wheel. It is still checked by the outer RECORD below.
        def outer_metadata(leaf: str) -> list[str]:
            return [name for name in names if name.count('/') == 1
                    and name.endswith('.dist-info/' + leaf)]
        records = outer_metadata('RECORD')
        metadata_names = outer_metadata('METADATA')
        wheel_names = outer_metadata('WHEEL')
        if len(records) != 1 or len(metadata_names) != 1 or len(wheel_names) != 1:
            raise ValueError('Missing or ambiguous wheel metadata')
        if len({name.split('/')[0] for name in records + metadata_names + wheel_names}) != 1:
            raise ValueError('Wheel metadata roots differ')
        rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode('utf-8'))))
        recorded = {}
        for row in rows:
            if len(row) != 3:
                raise ValueError('Invalid wheel RECORD row')
            name = safe_name(row[0])
            if name in recorded:
                raise ValueError('Duplicate RECORD row')
            recorded[name] = row[1:]
        unrecorded = set(names) - set(recorded)
        if unrecorded - {records[0] + '.jws', records[0] + '.p7s'}:
            raise ValueError('Wheel has unrecorded files')
        for name, (encoded, size) in recorded.items():
            if name not in names:
                raise ValueError('RECORD references a missing file')
            data = archive.read(name)  # Also checks ZIP CRC.
            if encoded:
                algorithm, value = encoded.split('=', 1)
                if algorithm not in {'sha256', 'sha384', 'sha512'}:
                    raise ValueError('Unsupported RECORD hash')
                actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b'=').decode()
                if actual != value:
                    raise ValueError('Wheel RECORD hash mismatch: ' + name)
            elif name not in {records[0], records[0] + '.jws', records[0] + '.p7s'}:
                raise ValueError('Wheel payload has no RECORD hash')
            if size and len(data) != int(size):
                raise ValueError('Wheel RECORD size mismatch')
        notices = [name for name in names if is_notice(name)]
        hooks = [name for name in names if name.endswith('.pth') and '/' not in name]
        scripts = [name for name in names if '.data/scripts/' in name]
        entrypoints = [name for name in names if name.endswith('.dist-info/entry_points.txt')]
        native = [name for name in names if name.endswith(('.so', '.dylib', '.dll', '.pyd'))]
        model_assets = [name for name in names if name.endswith(('.onnx', '.pt', '.gguf', '.safetensors'))]
        for name in sorted(set(notices + hooks + entrypoints + metadata_names + wheel_names)):
            save_evidence(evidence, name, archive.read(name))
        return {'format': 'wheel', 'entries': len(names), 'record_verified': True,
                'metadata': metadata_record(archive.read(metadata_names[0])),
                'notices': notices, 'startup_pth': hooks, 'scripts': scripts,
                'entrypoint_files': entrypoints, 'native_files': native, 'bundled_model_assets': model_assets,
                'uncompressed_bytes': sum(item.file_size for item in members)}


def inspect_sdist(path: Path, evidence: Path | None = None) -> dict:
    with tarfile.open(path, 'r:*') as archive:
        members = archive.getmembers()
        seen = set()
        total = 0
        for item in members:
            name = safe_name(item.name)
            if not item.isfile() and not item.isdir():
                raise ValueError('Source archive has a link/device/special member')
            if item.isfile():
                if name in seen:
                    raise ValueError('Duplicate source archive member')
                seen.add(name)
                total += item.size
        if total > path.stat().st_size * 30 + 64 * 1024**2:
            raise ValueError('Excessive source archive size')
        notices = [name for name in seen if is_notice(name)]
        build_files = [name for name in seen if PurePosixPath(name).name in
                       {'setup.py', 'setup.cfg', 'pyproject.toml', 'PKG-INFO', 'MANIFEST.in'}]
        for name in sorted(set(notices + build_files)):
            file = archive.extractfile(name)
            if file is None:
                raise ValueError('Source evidence file missing')
            save_evidence(evidence, name, file.read())
        return {'format': 'sdist', 'entries': len(seen), 'uncompressed_bytes': total,
                'notices': notices, 'build_files': build_files,
                'build_executed': False, 'manual_build_review_required': True}


def inspect_fixed_file(path: Path, expected: dict, evidence: Path | None = None) -> dict:
    if path.name != expected['filename'] or path.stat().st_size != expected['bytes']:
        raise ValueError('Archive name or size differs from the fixed proposal')
    digest = sha256(path)
    if digest != expected['sha256']:
        raise ValueError('Archive SHA256 differs from the fixed proposal')
    result = inspect_wheel(path, evidence) if path.name.endswith('.whl') else inspect_sdist(path, evidence)
    metadata = result.get('metadata', {})
    if metadata:
        normalize = lambda value: re.sub(r'[-_.]+', '-', value).lower()
        if metadata.get('Name') != [] and normalize(metadata['Name'][0]) != normalize(expected['name']):
            raise ValueError('Wheel metadata package name differs')
        if metadata.get('Version') and metadata['Version'][0] != expected['version']:
            raise ValueError('Wheel metadata package version differs')
    return {'name': expected['name'], 'version': expected['version'], 'filename': path.name,
            'bytes': path.stat().st_size, 'sha256': digest, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.report_dir.resolve().is_relative_to((root / '.local').resolve()):
        parser.error('Raw inspection evidence must be kept inside the ignored .local directory')
    proposal = json.loads(args.manifest.read_text(encoding='utf-8'))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    records, failures = [], []
    for expected in proposal['files']:
        try:
            if Path(expected['filename']).name != expected['filename']:
                raise ValueError('Unsafe proposed archive name')
            record = inspect_fixed_file(args.archives / expected['filename'], expected,
                                        args.report_dir / 'evidence' / expected['filename'])
            records.append(record)
        except Exception as error:
            failures.append({'filename': expected['filename'], 'error_type': type(error).__name__, 'reason': str(error)})
    report = {'status': 'archive_checks_passed_pending_manual_license_and_behavior_review' if not failures else 'failed',
              'archives_checked': len(records), 'failures': failures, 'archives': records,
              'installed': False, 'security_or_legal_acceptance': False}
    (args.report_dir / 'archive-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'archives_checked': len(records), 'failures': failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
