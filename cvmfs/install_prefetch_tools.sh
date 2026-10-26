#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo 'Run with sudo: bash cvmfs/install_prefetch_tools.sh' >&2
    exit 2
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
prefix=/opt/neurodesk-prefetch
runtime=$(command -v apptainer || command -v singularity)
definition_hash=$(sha256sum "$source_dir/prefetch_x11.def" | cut -d ' ' -f 1)
image="$prefix/x11-$definition_hash.sif"
mkdir -p "$prefix/bin"
temporary=$(mktemp -d "$prefix/.install-XXXXXX")
trap 'rm -rf -- "$temporary"' EXIT

if [[ ! -f "$image" ]]; then
    "$runtime" build "$temporary/x11.sif" "$source_dir/prefetch_x11.def"
    chmod 644 "$temporary/x11.sif"
    mv -- "$temporary/x11.sif" "$image"
fi
ln -sfn -- "$(basename -- "$image")" "$prefix/x11.sif"

# Run the portable shell launcher on the host so it can start the host's Python
# and Apptainer. Only the X11 executables run inside the toolkit image.
"$runtime" exec --cleanenv "$image" cat /usr/bin/xvfb-run > "$temporary/xvfb-run"
python3 - "$temporary/xvfb-run" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
original = '    wait || :\n'
if source.count(original) != 1:
    raise SystemExit('The toolkit xvfb-run readiness protocol has changed')
# Xvfb signals its Apptainer parent, rather than xvfb-run. Poll the authenticated
# display instead, and reap a failed server before the launcher's existing retry.
replacement = '''    ready=
    for attempt in $(seq 1 50); do
        if DISPLAY=:$SERVERNUM XAUTHORITY=$AUTHFILE xwininfo -root >/dev/null 2>&1; then
            ready=1
            break
        fi
        kill -0 "$XVFBPID" 2>/dev/null || break
        sleep 0.1
    done
    if [ -z "$ready" ]; then
        kill "$XVFBPID" 2>/dev/null || :
        wait "$XVFBPID" 2>/dev/null || :
    fi
'''
path.write_text(source.replace(original, replacement))
PY
install -m 755 "$temporary/xvfb-run" "$prefix/bin/xvfb-run"
cat > "$temporary/tool" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
prefix=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runtime=${NEURODESK_CONTAINER_RUNTIME:-$(command -v apptainer || command -v singularity)}
exec "$runtime" exec --cleanenv --bind /tmp:/tmp \
    --env "DISPLAY=${DISPLAY:-}" --env "XAUTHORITY=${XAUTHORITY:-}" \
    "$prefix/x11.sif" "$(basename -- "$0")" "$@"
EOF
for tool in Xvfb xauth xwininfo glxinfo; do
    install -m 755 "$temporary/tool" "$prefix/bin/$tool"
done

PATH="$prefix/bin:$PATH" timeout 45 xvfb-run --auto-servernum \
    --server-args='-screen 0 1280x1024x24 +extension GLX -noreset' glxinfo -B
echo "Installed and verified the FSL profiling tools in $prefix."
