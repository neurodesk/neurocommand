import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "upload_containers_simg.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "update-neurocontainers.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test-neurocommand.yml"
APPSJSON_QUEUE_WORKFLOW = ROOT / ".github" / "workflows" / "appsjson-queue.yml"
SYNC_ICONS_WORKFLOW = ROOT / ".github" / "workflows" / "sync-icons.yml"
SYNC_MENU_CATEGORIES_WORKFLOW = (
    ROOT / ".github" / "workflows" / "sync-menu-categories.yml"
)
RUNTIME_REQUIREMENTS = ROOT / "neurodesk" / "requirements.txt"
TOOLCHAIN_REQUIREMENTS = ROOT / "test" / "requirements.txt"


def requirement_names(path):
    names = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("-r "):
            names |= requirement_names(path.parent / line[3:].strip())
        elif line and not line.startswith("#"):
            names.add(line.split("==")[0].lower())
    return names


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


def test_update_neurocontainers_installs_the_swift_client():
    workflow = WORKFLOW.read_text()
    assert "python -m pip install -r maintenance/upload-requirements.txt" in workflow
    assert "'maintenance/upload-requirements.txt'" in workflow


def test_publish_needs_no_revision_from_the_upload_job():
    workflow = WORKFLOW.read_text()
    jobs = workflow[workflow.index("\njobs:") :]
    upload = jobs[: jobs.index("  publish_metadata:")]

    # Job outputs are dropped when they contain a masked secret value.
    assert "outputs:" not in upload
    assert "needs.upload_containers_simg.outputs" not in workflow
    assert "ref: main" in upload


def test_publish_rerun_cannot_cancel_a_newer_upload():
    workflow = WORKFLOW.read_text()
    jobs = workflow[workflow.index("\njobs:") :]
    publish = jobs[jobs.index("  publish_metadata:") :]

    assert "concurrency:" not in workflow[: workflow.index("\njobs:")]
    assert "concurrency:" not in publish


def test_workflows_install_the_pinned_toolchain_on_python_3_12():
    for workflow in (
        TEST_WORKFLOW,
        APPSJSON_QUEUE_WORKFLOW,
        SYNC_ICONS_WORKFLOW,
        SYNC_MENU_CATEGORIES_WORKFLOW,
    ):
        text = workflow.read_text()
        assert 'python-version: "3.12"' in text, workflow
        assert "python -m pip install -r test/requirements.txt" in text, workflow


def test_runtime_requirements_carry_the_distutils_provider():
    assert requirement_names(RUNTIME_REQUIREMENTS) == {"setuptools"}


def test_toolchain_requirements_extend_the_runtime_set_with_ci_tools():
    runtime = requirement_names(RUNTIME_REQUIREMENTS)
    toolchain = requirement_names(TOOLCHAIN_REQUIREMENTS)

    assert runtime < toolchain
    assert toolchain - runtime == {
        "cairosvg",
        "pytest",
        "python-swiftclient",
        "keystoneauth1",
    }


def test_unit_test_workflow_builds_the_slim_image():
    assert "docker build -f singularity/Dockerfile-slim ." in TEST_WORKFLOW.read_text()


def test_appsjson_queue_does_not_run_on_pr_close_events():
    workflow = APPSJSON_QUEUE_WORKFLOW.read_text()

    assert "- closed" not in workflow


def test_neurocommand_image_test_asserts_configured_container_root():
    workflow = TEST_WORKFLOW.read_text()
    assert (
        'container_root="${NEURODESKTOP_LOCAL_CONTAINERS:-local/containers}"'
        in workflow
    )
    assert "bash local/fetch_containers.sh niimath 1.0.0 20250617 niimath" in workflow
    assert (
        'image="${container_root}/niimath_1.0.0_20250617/niimath_1.0.0_20250617.simg"'
        in workflow
    )
    assert 'test -f "$image"' in workflow
    assert 'test -f "${container_root}/niimath_1.0.0_20250617/niimath"' in workflow


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
        sif)
            [[ "$3" == --help ]] && return 0
            echo "validate:$3" >> {shlex.quote(str(calls))}
            grep -q '^final$' "$3" || return 1
            echo "1    |1       |NONE    |0-6                       |FS"
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
    assert call_log.count("validate:") == 2


ARM64_SIF_LISTING = """\
------------------------------------------------------------------------------
ID   |GROUP   |LINK    |SIF POSITION (start-end)  |TYPE
------------------------------------------------------------------------------
1    |1       |NONE    |32176-32271               |Def.FILE
4    |1       |NONE    |40960-177324032           |FS (Squashfs/*System/arm64)
"""
# An FSL image's data ends beyond 2 GiB.
LARGE_SIF_LISTING = """\
1    |1       |NONE    |32176-32271               |Def.FILE
4    |1       |NONE    |40960-8260636672          |FS (Squashfs/*System/amd64)
"""


def _validate(tmp_path, size, listing_status=0, listing=ARM64_SIF_LISTING):
    image = tmp_path / "image.simg"
    with image.open("wb") as handle:
        handle.truncate(size)
    script = f"""
source {shlex.quote(str(SCRIPT))}
apptainer() {{
    # inspect would refuse this arm64 image on an amd64 host.
    [[ "$1 $2" == "sif list" ]] || return 255
    printf '%s' {shlex.quote(listing)}
    return {listing_status}
}}
container_runtime() {{ echo apptainer; }}
validate_singularity_image {shlex.quote(str(image))}
"""
    return run_bash(script)


def test_foreign_architecture_image_validates_from_its_sif_descriptors(tmp_path):
    result = _validate(tmp_path, 177324032)
    assert result.returncode == 0, result.stderr + result.stdout


def test_truncated_or_unparseable_sif_image_is_rejected(tmp_path):
    assert _validate(tmp_path, 100000000).returncode == 1
    assert _validate(tmp_path, 177324032, listing_status=1).returncode == 1


def test_truncated_image_beyond_2_gib_is_rejected(tmp_path):
    truncated = _validate(tmp_path, 100000000, listing=LARGE_SIF_LISTING)
    complete = _validate(tmp_path, 8260636672, listing=LARGE_SIF_LISTING)

    assert truncated.returncode == 1, truncated.stderr + truncated.stdout
    assert complete.returncode == 0, complete.stderr + complete.stdout


def test_runtime_without_sif_list_is_not_selected(tmp_path):
    script = f"""
source {shlex.quote(str(SCRIPT))}
singularity() {{ return 1; }}  # Singularity < 3.3 has no "sif list".
apptainer() {{ return 0; }}
container_runtime
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "apptainer"


def test_named_arm64_rebuild_uses_full_image_name_and_last_build_date(tmp_path):
    calls = tmp_path / "build.log"

    script = f"""
set -euo pipefail
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path))}

url_exists() {{
    return 1
}}

ensure_valid_local_image() {{
    return 1
}}

build_singularity_image() {{
    printf '%s\\n' "$*" > {shlex.quote(str(calls))}
}}

ensure_released() {{
    return 0
}}

process_container_line "neurodesktop-lite_arm64_20260428_20260808 categories:programming,"
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout
    assert calls.read_text().strip() == (
        "neurodesktop-lite_arm64_20260428_20260808 "
        "neurodesktop-lite_arm64_20260428 20260808"
    )


def test_transient_head_failure_does_not_trigger_large_recovery_download(tmp_path):
    class TransientHeadHandler(BaseHTTPRequestHandler):
        nectar_attempts = 0
        missing_attempts = 0

        def do_HEAD(self):
            if self.path.startswith("/missing/"):
                type(self).missing_attempts += 1
                self.send_response(404)
                self.end_headers()
                return

            if self.path.startswith("/nectar/"):
                type(self).nectar_attempts += 1
                if type(self).nectar_attempts == 1:
                    self.send_response(503)
                    self.end_headers()
                    return

            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), TransientHeadHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    recovery_calls = tmp_path / "recovery.log"

    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        script = f"""
set -euo pipefail
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path))}
NECTAR_BASE_URL={shlex.quote(base_url + "/nectar")}
AWS_BASE_URL={shlex.quote(base_url + "/aws")}
URL_CHECK_RETRIES=2
URL_CHECK_RETRY_DELAY=0

ensure_valid_local_image() {{
    return 1
}}

download_image() {{
    printf 'download:%s\n' "$1" >> {shlex.quote(str(recovery_calls))}
    return 0
}}

build_singularity_image() {{
    printf 'build:%s\n' "$*" >> {shlex.quote(str(recovery_calls))}
    return 0
}}

ensure_released() {{
    return 0
}}

if url_exists {shlex.quote(base_url + "/missing/image.simg")}; then
    exit 91
fi

process_container_line "neurodesktop-lite_arm64_20260428_20260808 categories:programming,"
"""

        result = run_bash(script)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert result.returncode == 0, result.stderr + result.stdout
    assert TransientHeadHandler.missing_attempts == 1
    assert TransientHeadHandler.nectar_attempts == 2
    assert not recovery_calls.exists()


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


def test_exhausted_retries_return_the_command_failure():
    script = f"""
source {shlex.quote(str(SCRIPT))}
run_with_retries "always fails" 2 0 bash -c 'exit 7'
"""

    result = run_bash(script)

    assert result.returncode == 7, result.stderr + result.stdout
    assert result.stdout.count("failed with exit code 7") == 2


def test_main_processes_generated_inventory_without_writing_published_metadata(
    tmp_path,
):
    processed = tmp_path / "processed.txt"
    published = [ROOT / "cvmfs" / "log.txt", ROOT / "cvmfs" / "applist.json"]
    before = [path.read_bytes() for path in published]

    script = f"""
set -euo pipefail
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path / "images"))}
process_container_line() {{
    echo "$1" >> {shlex.quote(str(processed))}
}}
main
"""

    result = run_bash(script)

    assert result.returncode == 0, result.stderr + result.stdout
    expected = subprocess.run(
        [
            sys.executable,
            str(ROOT / "cvmfs" / "generate_metadata.py"),
            "--log",
            "/dev/stdout",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert processed.read_text() == expected
    assert [path.read_bytes() for path in published] == before
    assert not (ROOT / "log.txt").exists()


def test_scheduled_metadata_retry_skips_upload_and_installs_signed_client():
    workflow = WORKFLOW.read_text()
    assert "cron: '*/30 * * * *'" in workflow
    assert "if: github.event_name != 'schedule'" in workflow
    assert (
        "if: always() && (github.event_name == 'schedule' || needs.upload_containers_simg.result == 'success')"
        in workflow
    )
    assert (
        "TRIGGER_SHA: ${{ github.event_name != 'schedule' && github.sha || '' }}"
        in workflow
    )
    assert (
        "cvmfs-contrib/github-action-cvmfs@72f2a8d366043978d6aebee154bc9ed321c2f2a8"
        in workflow
    )
    assert (
        "cvmfs_public_key: ${{ github.workspace }}/cvmfs/neurodesk.ardc.edu.au.pub"
        in workflow
    )
    assert "cvmfs_auto_update: 'no'" in workflow
