import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "upload_containers_simg.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "update-neurocontainers.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test-neurocommand.yml"
APPSJSON_QUEUE_WORKFLOW = ROOT / ".github" / "workflows" / "appsjson-queue.yml"
SYNC_ICONS_WORKFLOW = ROOT / ".github" / "workflows" / "sync-icons.yml"


def run_bash(script):
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_upload_containers_simg_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_update_neurocontainers_has_explicit_timeouts():
    workflow = WORKFLOW.read_text()
    assert ".github/workflows/upload_containers_simg.sh" in workflow
    assert "timeout-minutes: 360" in workflow
    assert "timeout-minutes: 330" in workflow


def test_unit_test_workflow_installs_distutils_provider():
    workflow = TEST_WORKFLOW.read_text()
    assert "python-version: \"3.12\"" in workflow
    assert "python -m pip install pytest setuptools cairosvg" in workflow


def test_icon_sync_workflows_install_svg_converter():
    assert "python -m pip install cairosvg" in SYNC_ICONS_WORKFLOW.read_text()
    assert "python -m pip install cairosvg" in APPSJSON_QUEUE_WORKFLOW.read_text()


def test_neurocommand_image_test_asserts_configured_container_root():
    workflow = TEST_WORKFLOW.read_text()
    assert 'container_root="${NEURODESKTOP_LOCAL_CONTAINERS:-local/containers}"' in workflow
    assert 'test -f "${container_root}/niimath_1.0.0_20240902/niimath_1.0.0_20240902.simg"' in workflow
    assert 'test -f "${container_root}/niimath_1.0.0_20240902/niimath"' in workflow


def test_singularity_build_retries_with_temp_output(tmp_path):
    image_home = tmp_path / "images"
    image_home.mkdir()
    calls = tmp_path / "calls.log"
    first_failure = tmp_path / "first_failure"
    target = image_home / "qsmxt_8.3.2_20260421.simg"
    temp_glob = str(image_home / "qsmxt_8.3.2_20260421.simg.tmp.*")

    script = f"""
set -euo pipefail
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(image_home))}
SINGULARITY_BUILD_RETRIES=2
SINGULARITY_BUILD_RETRY_DELAY=0
SINGULARITY_BUILD_TIMEOUT=

df() {{
    printf 'Avail\\n99999999\\n'
}}

singularity() {{
    case "$1" in
        build)
            echo "build:$2:$3" >> {shlex.quote(str(calls))}
            if [[ ! -f {shlex.quote(str(first_failure))} ]]; then
                touch {shlex.quote(str(first_failure))}
                echo partial > "$2"
                return 42
            fi
            echo final > "$2"
            return 0
            ;;
        inspect)
            echo "inspect:$2" >> {shlex.quote(str(calls))}
            grep -q '^final$' "$2"
            ;;
        *)
            return 1
            ;;
    esac
}}

build_singularity_image qsmxt_8.3.2_20260421 qsmxt_8.3.2 20260421
test "$(cat {shlex.quote(str(target))})" = final
! compgen -G {shlex.quote(temp_glob)} > /dev/null
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout
    call_log = calls.read_text()
    assert call_log.count("build:") == 2
    assert call_log.count("inspect:") == 2


def test_rclone_copy_uses_retry_flags_and_retries_failures(tmp_path):
    image = tmp_path / "image.simg"
    image.write_text("image")
    calls = tmp_path / "rclone.log"
    first_failure = tmp_path / "first_failure"

    script = f"""
set -euo pipefail
source {shlex.quote(str(SCRIPT))}
RCLONE_COPY_RETRIES=2
RCLONE_COPY_RETRY_DELAY=0
RCLONE_LOW_LEVEL_RETRIES=7

rclone() {{
    echo "$*" >> {shlex.quote(str(calls))}
    if [[ ! -f {shlex.quote(str(first_failure))} ]]; then
        touch {shlex.quote(str(first_failure))}
        return 19
    fi
    return 0
}}

rclone_copy_image {shlex.quote(str(image))} nectar:/neurodesk/ "upload test image"
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout
    call_log = calls.read_text()
    assert call_log.count("copy ") == 2
    assert "--retries 2" in call_log
    assert "--low-level-retries 7" in call_log
    assert "--checksum" in call_log
