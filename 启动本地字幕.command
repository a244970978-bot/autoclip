#!/bin/sh
set -eu
cd "$(dirname "$0")"
url=$(/usr/bin/python3 scripts/local_captions_control.py start)
printf '%s\n' "$url"
/usr/bin/open "$url"
