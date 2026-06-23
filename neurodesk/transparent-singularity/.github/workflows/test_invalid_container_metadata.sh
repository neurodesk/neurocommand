#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
workdir="$(mktemp -d)"

cleanup() {
    rm -rf "$workdir"
}
trap cleanup EXIT

fail() {
    echo "[ERROR] $*" >&2
    exit 1
}

copy_runtime() {
    local target="$1"
    mkdir -p "$target"
    cp "$repo_root/run_transparent_singularity.sh" "$target/"
    cp "$repo_root/ts_deactivate_" "$target/"
    cp "$repo_root/ts_binaryFinder.sh" "$target/"
    cp "$repo_root/ts_binaryFinderExcludes.txt" "$target/"
}

assert_no_modulefile() {
    local root="$1"
    local modulefile="$root/containers/modules/brainvisa/6.0.36.lua"
    if [[ -e "$modulefile" ]]; then
        echo "[ERROR] Unexpected modulefile content:" >&2
        cat "$modulefile" >&2
        exit 1
    fi
}

invalid_root="$workdir/invalid"
invalid_container_dir="$invalid_root/containers/brainvisa_6.0.36_"
copy_runtime "$invalid_container_dir"

set +e
invalid_output="$(cd "$invalid_container_dir" && bash run_transparent_singularity.sh --container brainvisa_6.0.36_.simg 2>&1)"
invalid_status=$?
set -e

if [[ "$invalid_status" -eq 0 ]]; then
    echo "$invalid_output" >&2
    fail "Malformed container name unexpectedly succeeded."
fi
if [[ "$invalid_output" != *"Container name must match name_version_YYYYMMDD"* ]]; then
    echo "$invalid_output" >&2
    fail "Malformed container name did not report the validation error."
fi
assert_no_modulefile "$invalid_root"

binary_finder_root="$workdir/binary-finder"
binary_finder_bin="$binary_finder_root/bin"
mkdir -p "$binary_finder_bin"
copy_runtime "$binary_finder_root"
cat > "$binary_finder_bin/itksnap" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$binary_finder_bin/itksnap"

set +e
binary_finder_output="$(
    cd "$binary_finder_root" &&
    env -i PATH="/usr/bin:/bin" DEPLOY_PATH="$binary_finder_bin" DEPLOY_BINS="" bash ./ts_binaryFinder.sh 2>&1
)"
binary_finder_status=$?
set -e

if [[ "$binary_finder_status" -ne 0 ]]; then
    echo "$binary_finder_output" >&2
    fail "Binary finder failed when no DEPLOY_ENV variables were present."
fi
if [[ "$(cat "$binary_finder_root/commands.txt")" != "itksnap" ]]; then
    echo "$binary_finder_output" >&2
    cat "$binary_finder_root/commands.txt" >&2
    fail "Binary finder did not write the expected command."
fi
if [[ -s "$binary_finder_root/env.txt" ]]; then
    cat "$binary_finder_root/env.txt" >&2
    fail "Binary finder should write an empty env.txt when no DEPLOY_ENV variables are present."
fi

introspection_root="$workdir/introspection"
introspection_container_dir="$introspection_root/containers/brainvisa_6.0.36_20260613"
copy_runtime "$introspection_container_dir"

fake_bin="$workdir/fake-bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/singularity" <<'EOF'
#!/usr/bin/env bash
case "$1" in
    pull)
        touch "$3"
        exit 0
        ;;
    exec)
        if [[ "$*" == *" cat /README.md" ]]; then
            exit 1
        fi
        if [[ "$*" == *"ts_binaryFinder.sh"* ]]; then
            exit 98
        fi
        exit 0
        ;;
    version)
        echo "3.10.0"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
EOF
cat > "$fake_bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 22
EOF
chmod +x "$fake_bin/singularity" "$fake_bin/curl"

set +e
introspection_output="$(
    cd "$introspection_container_dir" &&
    PATH="$fake_bin:$PATH" bash run_transparent_singularity.sh --container brainvisa_6.0.36_20260613.simg 2>&1
)"
introspection_status=$?
set -e

if [[ "$introspection_status" -eq 0 ]]; then
    echo "$introspection_output" >&2
    fail "Failed container introspection unexpectedly succeeded."
fi
if [[ "$introspection_output" != *"Could not inspect executables"* ]]; then
    echo "$introspection_output" >&2
    fail "Failed container introspection did not report the executable discovery error."
fi
assert_no_modulefile "$introspection_root"

echo "[INFO] Invalid container metadata tests passed."
