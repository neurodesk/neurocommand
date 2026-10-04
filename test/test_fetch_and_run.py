import os
import shutil
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
        env={k: v for k, v in os.environ.items() if k != "BASH_ENV"},
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
    (container_bin / "demo.simg").touch()
    (local_containers / "modules").mkdir(parents=True)

    script = f"""
set -euo pipefail
calls={shlex.quote(str(calls))}
container_bin={shlex.quote(str(container_bin))}
local_containers={shlex.quote(str(local_containers))}
export calls container_bin local_containers
export NEURODESKTOP_LOCAL_CONTAINERS="$local_containers"
export CVMFS_DISABLE=true
export LMOD_VERSION=stub

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
                export NEURODESK_IMAGE_64656D6F2F312E30="$container_bin/demo.simg"
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


def test_fetch_and_run_explicit_builddate_enforces_dated_container(tmp_path):
    isolated_neurodesk = tmp_path / "neurodesk"
    isolated_neurodesk.mkdir()
    shutil.copytree(ROOT / "neurodesk/transparent-singularity", isolated_neurodesk / "transparent-singularity")
    for name in ["apps.json", "bundles.json"]:
        shutil.copy2(ROOT / "neurodesk" / name, isolated_neurodesk / name)
    isolated_script = isolated_neurodesk / "fetch_and_run.sh"
    isolated_script.write_text(SCRIPT.read_text())
    isolated_script.chmod(0o755)
    (isolated_neurodesk / "configparser.sh").write_text("return 0\n")
    (isolated_neurodesk / "config.ini").write_text("")

    calls = tmp_path / "calls.log"
    fetch_marker = tmp_path / "fetch-called"
    old_container = tmp_path / "demo_1.0_20260518"
    local_containers = tmp_path / "neurodesktop-containers"
    new_container = local_containers / "demo_1.0_20260519"
    old_container.mkdir()
    (local_containers / "modules").mkdir(parents=True)

    fake_fetch_containers = isolated_neurodesk / "fetch_containers.sh"
    fake_fetch_containers.write_text(
        """#!/usr/bin/env bash
printf 'fetch %s %s %s\\n' "$1" "$2" "$3" >> "$calls"
mkdir -p "$NEURODESKTOP_LOCAL_CONTAINERS/$1_$2_$3" "$NEURODESKTOP_LOCAL_CONTAINERS/modules/$1"
touch "$NEURODESKTOP_LOCAL_CONTAINERS/$1_$2_$3/$1_$2_$3.simg"
touch "$fetch_marker"
"""
    )
    fake_fetch_containers.chmod(0o755)

    script = f"""
set -euo pipefail
calls={shlex.quote(str(calls))}
fetch_marker={shlex.quote(str(fetch_marker))}
old_container={shlex.quote(str(old_container))}
new_container={shlex.quote(str(new_container))}
local_containers={shlex.quote(str(local_containers))}
export calls fetch_marker old_container new_container local_containers
export NEURODESKTOP_LOCAL_CONTAINERS="$local_containers"
export CVMFS_DISABLE=true
export LMOD_VERSION=stub

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
        load)
            if [[ "$2" == "demo/1.0" ]]; then
                if [[ -f "$fetch_marker" ]]; then
                    export PATH="$new_container:$PATH"
                    export NEURODESK_IMAGE_64656D6F2F312E30="$new_container/$(basename "$new_container").simg"
                else
                    export PATH="$old_container:$PATH"
                    export NEURODESK_IMAGE_64656D6F2F312E30="$old_container/$(basename "$old_container").simg"
                fi
                return 0
            fi
            ;;
    esac
    printf 'unexpected module call: %s\\n' "$*" >&2
    return 42
}}
export -f module

bash {shlex.quote(str(isolated_script))} demo 1.0 20260519 true
grep -qx -- 'fetch demo 1.0 20260519' "$calls"
! grep -qx -- '--ignore-cache avail demo/1.0' "$calls"
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout


def test_fetch_containers_rejects_missing_builddate():
    result = subprocess.run(
        ["bash", str(FETCH_CONTAINERS), "brainvisa", "6.0.36"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stderr + result.stdout
    assert result.returncode == 2, output
    assert "invalid build date '<empty>'" in output
    assert "IMG_NAME=brainvisa_6.0.36_" not in output


def test_fetch_containers_rejects_malformed_builddate():
    result = subprocess.run(
        ["bash", str(FETCH_CONTAINERS), "brainvisa", "6.0.36", "latest"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stderr + result.stdout
    assert result.returncode == 2, output
    assert "invalid build date 'latest'" in output


def test_fetch_containers_honors_neurodesktop_local_containers_override():
    assert (
        "CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}"
        in FETCH_CONTAINERS.read_text()
    )


def test_environment_modules_availability_and_failed_load_stop_command(tmp_path):
    marker = tmp_path / "command-ran"
    script = f'''
export CVMFS_DISABLE=true
unset LMOD_CMD LMOD_VERSION
module() {{
    case "$1" in
        use) return 0 ;;
        avail) echo demo/1.0; return 0 ;;
        load) return 19 ;;
        *) return 42 ;;
    esac
}}
export -f module
bash {shlex.quote(str(SCRIPT))} demo 1.0 touch {shlex.quote(str(marker))}
'''
    result = run_bash(script)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not marker.exists()


def test_failed_fetch_stops_before_load_or_command(tmp_path):
    directory = tmp_path / "neurodesk"
    directory.mkdir()
    shutil.copytree(ROOT / "neurodesk/transparent-singularity", directory / "transparent-singularity")
    for name in ["apps.json", "bundles.json"]:
        shutil.copy2(ROOT / "neurodesk" / name, directory / name)
    (directory / "fetch_and_run.sh").write_text(SCRIPT.read_text())
    (directory / "configparser.sh").write_text("return 0\n")
    (directory / "fetch_containers.sh").write_text("exit 23\n")
    marker = tmp_path / "loaded"
    script = f'''
export CVMFS_DISABLE=true
module() {{
    if [[ $1 == load ]]; then touch {shlex.quote(str(marker))}; fi
    return 0
}}
export -f module
bash {shlex.quote(str(directory / "fetch_and_run.sh"))} demo 1.0 20260629 true
'''
    result = run_bash(script)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not marker.exists()


def test_missing_exact_version_does_not_accept_available_prefix(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    shutil.copy2(SCRIPT, install / SCRIPT.name)
    shutil.copy2(ROOT / 'neurodesk/configparser.sh', install / 'configparser.sh')
    shutil.copytree(ROOT / 'neurodesk/transparent-singularity', install / 'transparent-singularity')
    (install / 'config.ini').write_text('')
    (install / 'apps.json').write_text('{"first":{"apps":{"first 1.0":{"version":"20260629"}}}}')
    (install / 'bundles.json').write_text('{"schema_version":1,"bundles":[]}')
    (install / 'fetch_containers.sh').write_text('echo EXACT_MISSING >&2; exit 91\n')
    result = run_bash(f"""module() {{ case \"$1\" in avail|--ignore-cache) echo first/1.01;; *) return 0;; esac; }}
export -f module
CVMFS_DISABLE=true bash {shlex.quote(str(install / SCRIPT.name))} first 1.0 true
""")
    assert result.returncode == 2
    assert 'EXACT_MISSING' in result.stderr
