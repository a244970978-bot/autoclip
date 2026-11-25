"""Synthetic archive regressions; no packages, network or application data."""
import base64
import csv
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / 'local_first_package_audit.py'
SPEC = importlib.util.spec_from_file_location('local_first_package_audit', SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def wheel(path, extra=None, corrupt=False):
    files = {'demo/__init__.py': b'', 'demo-1.0.dist-info/METADATA': b'Name: demo\nVersion: 1.0\nLicense: MIT\n',
             'demo-1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
             'demo-1.0.dist-info/licenses/LICENSE': b'Synthetic MIT fixture'}
    files.update(extra or {})
    out = io.StringIO()
    writer = csv.writer(out)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + digest, len(data)])
    writer.writerow(['demo-1.0.dist-info/RECORD', '', ''])
    if corrupt:
        files['demo/__init__.py'] = b'changed after RECORD was generated'
    files['demo-1.0.dist-info/RECORD'] = out.getvalue().encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)


class PackageAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='autoclip-package-audit-test-')
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_valid_wheel_preserves_license_and_records_startup_hook(self):
        path = self.root / 'demo.whl'
        wheel(path, {'demo.pth': b'import demo\n', 'demo/tiny.onnx': b'synthetic fixture only'})
        report = audit.inspect_wheel(path, self.root / 'evidence')
        self.assertTrue(report['record_verified'])
        self.assertEqual(report['startup_pth'], ['demo.pth'])
        self.assertEqual(report['bundled_model_assets'], ['demo/tiny.onnx'])
        self.assertTrue((self.root / 'evidence/demo-1.0.dist-info/licenses/LICENSE').is_file())

    def test_path_traversal_is_rejected_without_writing(self):
        path = self.root / 'demo.whl'
        wheel(path, {'../outside.py': b'not executed'})
        with self.assertRaises(ValueError):
            audit.inspect_wheel(path, self.root / 'evidence')
        self.assertFalse((self.root / 'outside.py').exists())

    def test_record_tampering_is_rejected(self):
        path = self.root / 'demo.whl'
        wheel(path, corrupt=True)
        with self.assertRaises(ValueError):
            audit.inspect_wheel(path)

    def test_vendored_dist_info_is_payload_not_outer_metadata(self):
        path = self.root / 'demo.whl'
        wheel(path, {'demo/_vendor/other-2.0.dist-info/METADATA': b'Name: other\nVersion: 2.0\n',
                     'demo/_vendor/other-2.0.dist-info/WHEEL': b'Wheel-Version: 1.0\n',
                     'demo/_vendor/other-2.0.dist-info/RECORD': b''})
        report = audit.inspect_wheel(path)
        self.assertEqual(report['metadata']['Name'], ['demo'])
        self.assertTrue(report['record_verified'])

    def test_different_outer_metadata_roots_are_rejected(self):
        path = self.root / 'demo.whl'
        wheel(path)
        replacement = self.root / 'mismatched.whl'
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, 'w') as target:
            for item in source.infolist():
                name = item.filename.replace('demo-1.0.dist-info/WHEEL', 'other-1.0.dist-info/WHEEL')
                target.writestr(name, source.read(item.filename))
        with self.assertRaises(ValueError):
            audit.inspect_wheel(replacement)

    def test_fixed_archive_digest_is_checked(self):
        path = self.root / 'demo.whl'
        wheel(path)
        with self.assertRaises(ValueError):
            audit.inspect_fixed_file(path, {'filename': path.name, 'bytes': path.stat().st_size,
                'sha256': '0' * 64, 'name': 'demo', 'version': '1.0'})

    def test_source_symlink_is_rejected(self):
        path = self.root / 'demo.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo('demo/link')
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
            archive.addfile(member)
        with self.assertRaises(ValueError):
            audit.inspect_sdist(path)


if __name__ == '__main__':
    unittest.main()
