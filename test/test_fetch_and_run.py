import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "neurodesk" / "fetch_and_run.sh"
FETCH_CONTAINERS = ROOT / "neurodesk" / "fetch_containers.sh"


def run_bash(script):
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_fetch_and_run_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_fetch_and_run_module_availability_ignores_cache(tmp_path):
    calls = tmp_path / "module-calls.log"
    container_bin = tmp_path / "demo_1.0"
    local_containers = tmp_path / "neurodesktop-containers"
    container_bin.mkdir()
    (local_containers / "modules").mkdir(parents=True)

    script = f"""
set -euo pipefail
calls={shlex.quote(str(calls))}
container_bin={shlex.quote(str(container_bin))}
local_containers={shlex.quote(str(local_containers))}
export calls container_bin local_containers
export NEURODESKTOP_LOCAL_CONTAINERS="$local_containers"

module() {{
    printf '%s\\n' "$*" >> "$calls"
    case "$1" in
        use)
            return 0
            ;;
        --ignore-cache)
            if [[ "$2" == "avail" && "$3" == "demo/1.0" ]]; then
                printf 'demo/1.0\\n'
                return 0
            fi
            ;;
        avail)
            if [[ "$2" == "demo/1.0" ]]; then
                return 1
            fi
            ;;
        load)
            if [[ "$2" == "demo/1.0" ]]; then
                export PATH="$container_bin:$PATH"
                return 0
            fi
            ;;
    esac
    printf 'unexpected module call: %s\\n' "$*" >&2
    return 42
}}
export -f module

bash {shlex.quote(str(SCRIPT))} demo 1.0 true
grep -qx -- "use $local_containers/modules" "$calls"
grep -qx -- '--ignore-cache avail demo/1.0' "$calls"
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout


def test_fetch_containers_honors_neurodesktop_local_containers_override():
    assert (
        "CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}"
        in FETCH_CONTAINERS.read_text()
    )
