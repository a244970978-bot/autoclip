"""Explicit local-only caption entry, separate from cloud-capable legacy startup."""
from contextlib import asynccontextmanager
from pathlib import Path
import argparse
import json
import os
import tempfile

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.services.local_captions import CaptionError, CaptionWorkspace, caption_track

ASSETS = Path(__file__).parent / 'assets/local_captions'


class Edits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    edits: list[dict[str, str]] = Field(max_length=5000)


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    scenes: list[dict] = Field(min_length=1, max_length=30)
    aspect: str = 'original'
    subtitle_style: str = 'clean'
    subtitles: bool = True


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_hash: str = Field(pattern='^[a-f0-9]{64}$')
    approved: bool = False


class AnalysisScope(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    start: float | None = None
    end: float | None = None


def create_caption_app(workspace: CaptionWorkspace, port: int = 19784):
    @asynccontextmanager
    async def lifespan(_app):
        yield
        await run_in_threadpool(workspace.close)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    origin = f'http://127.0.0.1:{port}'

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        if (request.headers.get('host') != f'127.0.0.1:{port}'
                or not request.client or request.client.host not in ('127.0.0.1', '::1')
                or request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none')
                or request.headers.get('origin') not in (None, origin)):
            return JSONResponse({'error': 'local_only'}, status_code=403)
        if request.method not in ('GET', 'HEAD') and (
                request.headers.get('origin') != origin or request.headers.get('x-autoclip-local') != '1'):
            return JSONResponse({'error': 'local_only'}, status_code=403)
        try:
            length = int(request.headers.get('content-length', '0') or '0')
        except ValueError:
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        if length < 0 or length > 8 * 1024**3 + 1024**2:
            return JSONResponse({'error': 'file_too_large'}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Cross-Origin-Resource-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; connect-src 'self'; script-src 'self'; "
                "style-src 'self'; font-src 'self'; img-src 'self' data:; media-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(CaptionError)
    async def caption_error(_request, error):
        status = 404 if error.code == 'not_found' else 409 if error.code in ('busy', 'revision_conflict', 'approval_required') else 422
        return JSONResponse({'error': error.code}, status_code=status)

    @app.exception_handler(OSError)
    async def io_error(_request, error):
        return JSONResponse({'error': 'disk_full' if error.errno == 28 else 'io_failed'}, status_code=507)

    @app.exception_handler(ValueError)
    async def validation_error(_request, _error):
        return JSONResponse({'error': 'invalid_plan'}, status_code=422)

    @app.get('/')
    def index():
        return FileResponse(ASSETS / 'index.html')

    @app.get('/assets/{name}')
    def asset(name: str):
        if name == 'font.ttf':
            return FileResponse(ASSETS.parent / 'fonts/NotoSansSC-Medium.ttf')
        if name not in ('app.js', 'style.css', 'locales.json'):
            raise CaptionError('not_found')
        return FileResponse(ASSETS / name)

    @app.get('/api/projects')
    def projects():
        return {'projects': workspace.listing()}

    @app.post('/api/import')
    def import_video(video: UploadFile = File(...)):
        try:
            return workspace.import_stream(video.file, video.filename or '')
        finally:
            video.file.close()

    @app.get('/api/projects/{pid}')
    def project(pid: str):
        data = workspace.load(pid)
        if data['cues']:
            data['track'] = caption_track(data['cues'], data['draft'], data['info'])
        active = next((j for j in reversed(data['jobs']) if j['status'] in ('queued','running')), None)
        if active:
            progress = workspace.path(pid) / 'metadata' / ('job-' + active['id']) / 'progress.json'
            if progress.is_file():
                active['progress'] = json.loads(progress.read_text())
        return data

    @app.post('/api/projects/{pid}/analysis')
    def analyze(pid: str, scope: AnalysisScope):
        return workspace.start_job(pid, 'analyze', scope=(scope.start, scope.end))

    @app.get('/api/projects/{pid}/analysis')
    def analysis(pid: str, job_id: str | None = None):
        return {'analysis': workspace.analysis(pid, job_id)}

    @app.post('/api/projects/{pid}/one-click')
    def one_click(pid: str):
        return {'job': workspace.one_click(pid)}

    @app.get('/api/projects/{pid}/analysis/{jid}/candidates/{cid}/preview')
    def candidate_preview(pid: str, jid: str, cid: str):
        return workspace.candidate_preview(pid, jid, cid)

    @app.get('/api/projects/{pid}/source')
    def source(pid: str):
        path = workspace.path(pid) / 'raw/input.mp4'
        if path.is_symlink():
            raise CaptionError('source_changed')
        return FileResponse(path, media_type='video/mp4')

    @app.post('/api/projects/{pid}/captions')
    def edit_captions(pid: str, body: Edits):
        return workspace.edit_captions(pid, body.revision, body.edits)

    @app.post('/api/projects/{pid}/plan')
    def save_plan(pid: str, body: Plan):
        return workspace.plan(pid, body.revision, body.model_dump(exclude={'revision'}))

    @app.post('/api/projects/{pid}/export')
    def export(pid: str, body: Approval):
        return workspace.start_job(pid, 'render', plan_hash=body.plan_hash, approved=body.approved)

    @app.post('/api/projects/{pid}/transcribe')
    def retry_transcribe(pid: str):
        return workspace.start_job(pid, 'transcribe')

    @app.post('/api/projects/{pid}/jobs/{jid}/cancel')
    def cancel(pid: str, jid: str):
        workspace.cancel(pid, jid)
        return {'status': 'cancelling'}

    @app.get('/api/projects/{pid}/jobs/{jid}/{kind}')
    def download(pid: str, jid: str, kind: str):
        path = workspace.download(pid, jid, kind)
        media = {'video': 'video/mp4', 'subtitles': 'application/x-subrip', 'plan': 'application/json'}[kind]
        return FileResponse(path, media_type=media,
                            filename=None if kind == 'video' else ('captions.srt' if kind == 'subtitles' else 'plan.json'))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--worker-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=19784)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid local port')
    (args.data_dir / 'tmp').mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.data_dir / 'tmp')
    from backend.services.local_runner import setup_logging
    workspace = CaptionWorkspace(args.data_dir, worker_profile=args.worker_profile)
    setup_logging(False)
    # This process needs to accept local browser connections, not initiate any.
    def no_outbound(event, _args):
        if event == 'socket.connect':
            raise PermissionError('Caption workspace never initiates network connections')
    import sys
    sys.addaudithook(no_outbound)
    import uvicorn
    uvicorn.run(create_caption_app(workspace, args.port), host='127.0.0.1', port=args.port,
                access_log=False, proxy_headers=False, log_level='warning', timeout_graceful_shutdown=20)


if __name__ == '__main__':
    main()
