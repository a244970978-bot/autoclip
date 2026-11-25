"""Offline job broker, independently sandboxed by the launcher (never nested)."""
from pathlib import Path
import argparse
import os
import signal
import subprocess
import sys
import time

from backend.services.local_captions import CaptionWorkspace, atomic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--server-pid', type=int, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    running = True
    proc = None

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    def parent_alive():
        try:
            os.kill(args.server_pid, 0)
            return True
        except ProcessLookupError:
            return False
    try:
        while running and parent_alive():
            for marker in root.glob('projects/*/metadata/job-*/dispatch.ready'):
                if not marker.resolve().is_relative_to(root) or marker.is_symlink():
                    continue
                folder = marker.parent
                marker.rename(folder / 'dispatch.claimed')
                with (folder / 'worker.stdout').open('wb') as out, (folder / 'worker.stderr').open('wb') as err:
                    proc = subprocess.Popen([sys.executable, '-B', '-m', 'backend.local_captions_worker',
                        str(folder / 'task.json'), str(os.getpid())], stdout=out, stderr=err, start_new_session=True)
                atomic(folder / 'worker-pid.json', {'pid': proc.pid})
                deadline = time.monotonic() + 4 * 3600
                while proc.poll() is None:
                    if not running or not parent_alive() or (folder / 'cancel').exists() or time.monotonic() > deadline:
                        CaptionWorkspace._kill(proc)
                        break
                    time.sleep(.1)
                if not (folder / 'result.json').exists():
                    atomic(folder / 'result.json', {'status': 'failed', 'error': 'cancelled'
                           if (folder / 'cancel').exists() else 'interrupted'})
                proc = None
            time.sleep(.15)
    finally:
        if proc is not None:
            CaptionWorkspace._kill(proc)


if __name__ == '__main__':
    main()
