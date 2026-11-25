#!/bin/sh
set -eu
cd "$(dirname "$0")"
/usr/bin/python3 scripts/local_captions_control.py stop
