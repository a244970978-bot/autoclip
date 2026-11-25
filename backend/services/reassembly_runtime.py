"""Existing approved llama-server + pinned GGUF via one private Unix socket, no IP."""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time

from backend.services.local_captions import CaptionError, digest

PINS = Path(__file__).resolve().parents[1] / 'assets/local_captions/runtime-pins.json'


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('localhost', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


@contextmanager
def local_model(log_path):
    runner, model, channel = (os.environ.get(k) for k in
                             ('AUTOCLIP_LLAMA_SERVER', 'AUTOCLIP_QWEN_MODEL', 'AUTOCLIP_MODEL_SOCKET'))
    if not all((runner, model, channel)):
        raise CaptionError('analysis_model_unavailable')
    pins = json.loads(PINS.read_text())['reassembly']
    for p, expected in ((Path(runner), pins['runner']), (Path(model), pins['model'])):
        if not p.is_file():
            raise CaptionError('analysis_model_unavailable')
        if digest(p) != expected:
            raise CaptionError('analysis_model_changed')
    path = Path(channel)
    if path.exists() or not path.is_absolute() or path.parent.stat().st_mode & 0o077:
        raise CaptionError('analysis_model_unavailable')
    token = secrets.token_hex(24)
    key_file = path.parent / 'request.key'
    with key_file.open('x') as f:
        key_file.chmod(0o600)
        f.write(token + '\n')
    proc = None

    def request(method, endpoint, data=None, timeout=125):
        if proc.poll() is not None:
            raise CaptionError('analysis_model_unavailable')
        with_connection = UnixHTTP(channel, timeout)
        try:
            with_connection.request(method, endpoint, json.dumps(data, ensure_ascii=False).encode() if data else None,
                                    {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
            res = with_connection.getresponse()
            payload = res.read(512 * 1024 + 1)
            if res.status != 200 or len(payload) > 512 * 1024:
                raise ValueError('local model response error')
            return json.loads(payload)
        finally:
            with_connection.close()

    try:
        with log_path.open('wb') as log:
            proc = subprocess.Popen([runner, '-m', model, '--offline', '--host', channel, '--no-webui',
                '--no-webui-mcp-proxy', '--api-key-file', str(key_file), '-c', '12288', '-np', '1',
                '--reasoning', 'off', '--log-disable'], stdout=log, stderr=log)
            for _ in range(240):
                try:
                    request('GET', '/health', timeout=.7)
                    break
                except (ValueError, OSError, http.client.HTTPException):
                    time.sleep(.25)
            else:
                raise CaptionError('analysis_model_unavailable')

            def call(system, payload, *, schema=None, max_tokens=560):
                response_format = {'type': 'json_object'} if schema is None else {
                    'type': 'json_schema', 'json_schema': {'name': 'local_edit', 'schema': schema}}
                data = request('POST', '/v1/chat/completions', {'model': 'local', 'stream': False,
                    'temperature': .15, 'max_tokens': min(950, max(64, max_tokens)), 'response_format': response_format,
                    'messages': [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]})
                try:
                    answer = data['choices'][0]
                    if answer.get('finish_reason') == 'length':
                        raise ValueError('incomplete proposal')
                    return json.loads(answer['message']['content'])
                except (KeyError, IndexError, TypeError) as fault:
                    raise ValueError('bad proposal JSON') from fault
            yield call
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        key_file.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def detect_silence(video, scope, run):
    """Return absolute source ranges. Never infer a removable pause from ASR alone."""
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    start, end = scope
    done = run([get_ffmpeg_path(), '-nostdin', '-v', 'info', '-protocol_whitelist', 'file,pipe',
                '-ss', str(start), '-i', str(video), '-t', str(end-start), '-map', '0:a:0',
                '-vn', '-sn', '-dn', '-af', 'silencedetect=noise=-37dB:d=0.62', '-f', 'null', '-'],
               capture_output=True, check=True, text=True, timeout=180)
    intervals, opened = [], None
    for tag, number in re.findall(r'silence_(start|end): (-?\d+(?:\.\d+)?)', done.stderr):
        timepoint = min(end, max(start, start+float(number)))
        if tag == 'start':
            opened = timepoint
        elif opened is not None:
            if timepoint > opened:
                intervals.append((opened, timepoint))
            opened = None
    return intervals
