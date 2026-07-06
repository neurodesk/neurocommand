#!/usr/bin/env bash
set -euo pipefail

# Lmod 6.6 serializes cached help text as a Lua [[...]] long string. Avoid
# emitting its close delimiter in help text so `ml av` can parse the cache.
sed 's/]]/] ]/g' "$@"
