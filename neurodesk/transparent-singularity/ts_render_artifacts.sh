#!/usr/bin/env bash
set -e
base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
python3 "$base/artifact_renderer.py" "$@"
