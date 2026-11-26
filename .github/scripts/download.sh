#!/usr/bin/env bash
# URL FILE
set -euo pipefail
(( $# == 2 )) || { echo "usage: download.sh URL FILE" >&2; exit 2; }
curl --fail --silent --show-error --location \
  --connect-timeout 10 --max-time 60 \
  --retry 8 --retry-all-errors --retry-max-time 300 \
  --output "$2" "$1"
[[ -s "$2" ]] || { echo "download.sh: no file at $2" >&2; exit 1; }
