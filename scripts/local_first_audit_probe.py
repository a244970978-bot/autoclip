#!/usr/bin/env python3
"""Dependency-free, isolated observations of the pinned upstream source.

This is an audit probe, NOT the product quality gate, a real-model test, a
renderer, or proof of offline operation. AST extraction avoids importing the
application (and therefore avoids its settings, SDKs and background services).
The timeline import is supplied only with the unmodified scene_duration
function extracted from the same source. All text fixtures are synthetic.
"""
from __future__ import annotations

import __future__
import argparse
import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OBSERVATIONS = {}
SOURCES = {}


def extract(relative, names, **bindings):
    path = ROOT / relative
    data = path.read_bytes()
    SOURCES[relative] = hashlib.sha256(data).hexdigest()
    tree = ast.parse(data, filename=relative)
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise ValueError('Source definitions changed: ' + relative)
    scope = {'__name__': __name__, **bindings}
    code = compile(ast.Module(body=nodes, type_ignores=[]), relative, 'exec',
                   flags=__future__.annotations.compiler_flag, dont_inherit=True)
    exec(code, scope)
    return scope


def load_pure(relative):
    data = (ROOT / relative).read_bytes()
    SOURCES[relative] = hashlib.sha256(data).hexdigest()
    return runpy.run_path(str(ROOT / relative))


class BaselineObservations(unittest.TestCase):
    def test_local_url_includes_non_loopback_addresses(self):
        check = extract('backend/core/llm_providers.py', ['is_local_url'])['is_local_url']
        urls = {
            'http://127.0.0.1:11434/v1': True,
            'http://[::1]:1234/v1': True,
            'http://192.168.50.2:11434/v1': True,
            'http://169.254.10.2:1234/v1': True,
            'http://0.0.0.0:11434/v1': True,
            'http://workstation.local:1234/v1': True,
            'https://example.com/v1': False,
            'http://127.0.0.1.example.com/v1': False,
        }
        for url, expected in urls.items():
            with self.subTest(url=url):
                self.assertIs(check(url), expected)
        OBSERVATIONS['local_url_is_not_strict_loopback'] = True

    def test_unset_diagnostics_allows_excerpts(self):
        scope = extract('backend/services/diagnostics/consent.py', ['Consent', '_unset'],
                        dataclass=dataclass, CONSENT_TEXT_VERSION='isolated-probe')
        consent = scope['_unset']()
        self.assertTrue(consent.diagnostics)
        self.assertTrue(consent.media)
        self.assertFalse(consent.full_original)
        OBSERVATIONS['unset_diagnostics'] = {
            'diagnostics': consent.diagnostics, 'media': consent.media,
            'full_original': consent.full_original,
        }

    def test_whisper_defaults_to_cpu_int8(self):
        resolve = extract('backend/utils/speech_recognizer.py',
                          ['resolve_local_whisper_backend'], os=os)['resolve_local_whisper_backend']
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve(), ('cpu', 'int8'))
        self.assertEqual(resolve('cuda'), ('cuda', 'float16'))
        self.assertEqual(resolve('auto'), ('auto', 'default'))
        OBSERVATIONS['default_whisper_backend'] = ['cpu', 'int8']

    def test_sensevoice_preserves_aligned_words(self):
        align = load_pure('backend/services/sensevoice_alignment.py')['aligned_cues']
        result = [{'text': '只有确认后，才可以导出。',
                   'words': ['只有', '确认', '后', '才', '可以', '导出'],
                   'timestamp': [[0, 200], [200, 400], [400, 600],
                                 [700, 800], [800, 1000], [1000, 1200]]}]
        cues = align(result, 1400)
        self.assertEqual(''.join(cue['text'] for cue in cues), result[0]['text'])
        self.assertAlmostEqual(cues[-1]['end'], 1.2)
        OBSERVATIONS['sensevoice_alignment_fixture'] = 'preserved_synthetic_text_and_times'

    def test_sensevoice_rejects_missing_or_invalid_times(self):
        align = load_pure('backend/services/sensevoice_alignment.py')['aligned_cues']
        bad = [
            [{'text': '合成测试'}],
            [{'text': '合成', 'words': ['合', '成'], 'timestamp': [[0, 500], [400, 600]]}],
            [{'text': '合', 'words': ['合'], 'timestamp': [[0, float('nan')]]}],
            [{'text': '合', 'words': ['合'], 'timestamp': [[0, 2000]]}],
        ]
        for fixture in bad:
            with self.subTest(fixture=repr(fixture)), self.assertRaises(ValueError):
                align(fixture, 1000)

    def test_word_sidecar_invalidates_when_subtitle_changes(self):
        timing = load_pure('backend/utils/word_timing.py')
        # No user files, application data or existing subtitles are opened.
        with tempfile.TemporaryDirectory(prefix='autoclip-audit-fixture-') as directory:
            srt = Path(directory) / 'synthetic.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n合成测试\n', encoding='utf-8')
            segment = {'start': 0.0, 'end': 1.0, 'text': '合成测试',
                       'words': [{'start': 0.0, 'end': 1.0, 'text': '合成测试'}]}
            timing['write_word_timing'](srt, [segment], 'zh')
            self.assertEqual(timing['load_word_timing'](srt), [segment])
            srt.write_text(srt.read_text(encoding='utf-8').replace('合成测试', '修订测试'), encoding='utf-8')
            self.assertIsNone(timing['load_word_timing'](srt))
        OBSERVATIONS['subtitle_edits_invalidate_word_sidecar'] = True

    def test_word_validation_rejects_nan_and_overlap(self):
        valid = load_pure('backend/utils/word_timing.py')['valid_words']
        self.assertFalse(valid({'start': 0, 'end': 1, 'text': '甲乙', 'words': [
            {'start': 0, 'end': .7, 'text': '甲'}, {'start': .6, 'end': 1, 'text': '乙'}]}))
        self.assertFalse(valid({'start': 0, 'end': 1, 'text': '甲', 'words': [
            {'start': 0, 'end': float('nan'), 'text': '甲'}]}))

    def test_reordered_scene_output_offsets(self):
        duration = extract('backend/services/studio/audio.py', ['scene_duration'])['scene_duration']
        audio = ModuleType('backend.services.studio.audio')
        audio.scene_duration = duration
        timeline = extract('backend/services/studio/packaging_render.py', ['timeline'])['timeline']
        scenes = [SimpleNamespace(start=a, end=b) for a, b in [(40, 44), (2, 8), (20, 24)]]
        # Only the import dependency is shimmed; neither source function is rewritten.
        with patch.dict(sys.modules, {'backend.services.studio.audio': audio}):
            rows = timeline(scenes)
        self.assertEqual(rows, [(40, 44, 0.0), (2, 8, 4.0), (20, 24, 10.0)])
        self.assertAlmostEqual(sum(duration(scene) for scene in scenes), 14.0)
        OBSERVATIONS['three_scene_offset_fixture'] = rows

    def test_scene_bounds_helper_on_validated_scene_fixture(self):
        validate = extract('backend/services/studio/intelligence.py', ['validate_scenes'],
                           UserFacingError=ValueError)['validate_scenes']
        # This does not exercise Pydantic Scene, the API, ffprobe, or persistence.
        validate([SimpleNamespace(start=40, end=44), SimpleNamespace(start=2, end=8),
                  SimpleNamespace(start=20, end=24)], 50)
        with self.assertRaises(ValueError):
            validate([], 50)
        with self.assertRaises(ValueError):
            validate([SimpleNamespace(start=40, end=60)], 50)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Optional local-only JSON evidence path')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BaselineObservations)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
                         capture_output=True, check=True).stdout.strip()
    report = {
        'schema_version': 1, 'kind': 'isolated_source_audit_not_product_acceptance',
        'executed_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'source_commit': sha, 'source_sha256': SOURCES,
        'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'python_version': sys.version.split()[0],
        'tests_run': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'observations_confirmed': result.wasSuccessful(),
        'observations': OBSERVATIONS,
        'not_tested': ['real_asr', 'real_local_llm', 'ffmpeg_export', 'ui',
                       'application_network_capture', 'end_to_end_offline', 'semantic_quality'],
    }
    raw = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(raw, encoding='utf-8')
    print(raw, end='')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
