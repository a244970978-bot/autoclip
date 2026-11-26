#!/usr/bin/env python3
"""Start/stop the prepared macOS local caption workspace. Never installs/downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shlex
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / 'backend/assets/local_captions/runtime-pins.json').read_text())
FF = PINS['ffmpeg']


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def owned(pid, data):
    p = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return 'backend.local_captions_app' in p.stdout and '--data-dir ' + str(data) + ' ' in p.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.local/caption-workspace')
    parser.add_argument('--port', type=int, default=19784)
    args = parser.parse_args()
    data = args.data_dir.resolve()
    try:
        data.relative_to(ROOT / '.local')
    except ValueError:
        parser.error('Private runtime data must remain under the ignored project .local directory')
    runtime = data / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    statefile = runtime / 'server.json'
    state = json.loads(statefile.read_text()) if statefile.exists() else {}
    alive = bool(state.get('pid') and owned(state['pid'], data))
    if args.action == 'status':
        print(json.dumps({'status': 'running' if alive else 'stopped', 'url': state.get('url')}))
        return 0
    if args.action == 'stop':
        if alive:
            os.kill(state['pid'], signal.SIGTERM)
            for _ in range(200):
                if not owned(state['pid'], data):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Owned workspace did not stop; no unrelated process was killed')
        for broker in filter(None, (state.get('broker_pid'), state.get('analysis_pid'))):
            command = subprocess.run(['/bin/ps', '-p', str(broker), '-o', 'command='], capture_output=True, text=True).stdout
            if 'backend.local_captions_broker' in command and '--data-dir ' + str(data) + ' ' in command:
                try:
                    os.kill(broker, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        directory = state.get('socket_directory')
        if directory and Path(directory).parent == Path('/private/tmp') and Path(directory).name.startswith('ac-model-'):
            try:
                Path(directory).rmdir()
            except OSError:
                pass  # Don't remove live sockets/credentials or unrelated files.
        statefile.write_text(json.dumps({**state, 'status': 'stopped'}) + '\n')
        print('stopped')
        return 0
    if alive:
        print(state['url'])
        return 0
    if sys.platform != 'darwin':
        parser.error('This launcher has only been implemented for macOS sandbox-exec')
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid port')
    probe = socket.socket()
    try:
        # Match the HTTP listener: old loopback connections in TIME_WAIT
        # must not block an immediate controlled restart after a code change.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', args.port))
        probe.listen(1)  # Still reject a live listener; do not use SO_REUSEPORT.
    finally:
        probe.close()
    python = ROOT / '.local/environments/autoclip-b-2026-11-25/bin/python'
    if not python.is_file():
        raise RuntimeError('Prepared Python environment missing; no installation was attempted')
    binaries = ROOT / '.local/prepared/ffmpeg-9.0.2'
    for name, expected in FF.items():
        if not (binaries / name).is_file() or sha(binaries / name) != expected:
            raise RuntimeError('Approved FFmpeg missing/changed; no download was attempted')
    # Parent-side metadata probes may read files/pipes, never HTTP (including a local proxy).
    probe_wrapper = runtime / 'ffprobe-files-only'
    probe_wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binaries / 'ffprobe'))
                             + ' -protocol_whitelist file,pipe "$@"\n')
    probe_wrapper.chmod(0o755)
    # Project reads only, home/keychain denied; only the browser-facing parent gets loopback.
    home = Path.home()
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    base = '(version 1)\n(allow default)\n(deny network*)\n'
    base += '(deny file-read* file-write* (subpath ' + q(home) + '))\n'
    base += '(allow file-read* file-write* (subpath ' + q(ROOT) + '))\n'
    base += '(deny file-read* file-write* (subpath "/Library/Keychains"))\n'
    base += '(deny mach-lookup (global-name "com.apple.securityd") (global-name "com.apple.securityd.xpc"))\n'
    worker_profile, parent_profile = runtime / 'worker.sb', runtime / 'server.sb'
    worker_profile.write_text(base)
    parent_profile.write_text(base + '(allow network-bind (local ip "localhost:*"))\n'
                              '(allow network-inbound (local ip "localhost:*"))\n'
                              '(allow network-outbound (remote ip "localhost:*"))\n')
    isolated_home = runtime / 'home'
    isolated_home.mkdir(exist_ok=True)
    (data / 'tmp').mkdir(exist_ok=True)
    # Only a verified existing local ASR cache is linked. Missing weights fail inside the UI.
    model = ROOT / '.local/downloads/whisper'
    if (model / 'model.bin').is_file():
        if not all((model / name).is_file() and sha(model / name) == expected for name, expected in PINS['whisper_files'].items()):
            raise RuntimeError('Approved ASR files changed; no download or cloud fallback was attempted')
        snapshot = data / 'whisper-models/hub/models--Systran--faster-whisper-small/snapshots/536b0662742c02347bc0e980a01041f333bce120'
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in ['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt']:
            if not (model / name).is_file():
                raise RuntimeError('Approved ASR cache incomplete; no download was attempted')
            link = snapshot / name
            if not link.exists():
                link.symlink_to(model / name)
            if link.resolve() != (model / name).resolve():
                raise RuntimeError('Unexpected ASR cache link')
        refs = snapshot.parent.parent / 'refs'
        refs.mkdir(exist_ok=True)
        (refs / 'main').write_text(snapshot.name)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin', 'HOME': str(isolated_home), 'TMPDIR': str(data / 'tmp'),
           'AUTOCLIP_DATA_DIR': str(data), 'AUTOCLIP_APP_DIR': str(data), 'APP_DIR': str(data),
           'LOG_FILE': str(runtime / 'application.log'), 'AUTOCLIP_FFMPEG_PATH': str(binaries / 'ffmpeg'),
           'AUTOCLIP_FFPROBE_PATH': str(probe_wrapper), 'AUTOCLIP_VIDEO_ENCODER': 'x264',
           'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'REDIS_URL': 'redis://127.0.0.1:1/0'}
    cmd = ['/usr/bin/sandbox-exec', '-f', str(parent_profile), str(python), '-B', '-m',
           'backend.local_captions_app', '--data-dir', str(data), '--worker-profile', str(worker_profile),
           '--port', str(args.port)]
    with (runtime / 'server.stdout').open('ab') as out, (runtime / 'server.stderr').open('ab') as err:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=err, start_new_session=True)
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError('Workspace startup failed; inspect private runtime logs')
        sock = socket.socket(); sock.settimeout(.1)
        try:
            sock.connect(('127.0.0.1', args.port))
            break
        except OSError:
            time.sleep(.1)
        finally:
            sock.close()
    else:
        os.killpg(proc.pid, signal.SIGTERM)
        raise RuntimeError('Workspace did not become ready')
    broker_cmd = ['/usr/bin/sandbox-exec', '-f', str(worker_profile), str(python), '-B', '-m',
                  'backend.local_captions_broker', '--data-dir', str(data), '--server-pid', str(proc.pid)]
    with (runtime / 'broker.stdout').open('ab') as out, (runtime / 'broker.stderr').open('ab') as err:
        broker = subprocess.Popen(broker_cmd, cwd=ROOT, env=env, stdout=out, stderr=err, start_new_session=True)
    time.sleep(.2)
    if broker.poll() is not None:
        os.kill(proc.pid, signal.SIGTERM)
        raise RuntimeError('Offline broker failed to start')
    socket_dir = Path(tempfile.mkdtemp(prefix='ac-model-', dir='/private/tmp'))
    channel = socket_dir / 'model.sock'
    ai_profile = runtime / 'analysis.sb'
    ai_profile.write_text(base + f'(allow network-bind network-inbound (local unix-socket (literal {q(channel)})))\n'
                          f'(allow network-outbound (remote unix-socket (literal {q(channel)})))\n')
    ai_env = {**env, 'AUTOCLIP_MODEL_SOCKET': str(channel),
              'AUTOCLIP_LLAMA_SERVER': str(ROOT / '.local/prepared/ollama-0.40.18/llama-server'),
              'AUTOCLIP_QWEN_MODEL': str(ROOT / '.local/downloads/qwen/Qwen3-8B-Q4_K_M.gguf')}
    with (runtime / 'analysis-broker.log').open('ab') as log:
        analysis = subprocess.Popen([*broker_cmd[:2], str(ai_profile), *broker_cmd[3:], '--analysis'],
                                    cwd=ROOT, env=ai_env, stdout=log, stderr=log, start_new_session=True)
    state = {'pid': proc.pid, 'broker_pid': broker.pid, 'analysis_pid': analysis.pid,
             'socket_directory': str(socket_dir), 'url': f'http://127.0.0.1:{args.port}', 'status': 'running'}
    statefile.write_text(json.dumps(state) + '\n')
    print(state['url'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
