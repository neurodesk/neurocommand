import os
from pathlib import Path
import subprocess
import tempfile

from cvmfs import reconcile_wrapper_xauthority as reconcile


def legacy_wrapper(container_dir, command, bind_option=""):
    container_name = container_dir.name
    return (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        f'{bind_option} $neurodesk_singularity_opts --pwd "$PWD" '
        f'{container_dir}/{container_name}.simg {command} "$@"\n'
    )


def fixed_wrapper(container_dir, command, bind_option=""):
    return reconcile._fixed_wrapper(
        legacy_wrapper(container_dir, command, bind_option).encode()
    ).decode()


def make_container(repo_root, commands):
    container = repo_root / "containers" / "demo_1.0_20260101"
    container.mkdir(parents=True)
    (container / (container.name + ".simg")).touch()
    (container / "commands.txt").write_text("".join(f"{name}\n" for name in commands))
    return container


def assert_gpu_environment(wrapper):
    # Override only the driver-file check; execute the wrapper and a child
    # runtime process to verify exports without requiring NVIDIA hardware.
    shell = """
function [() {
    if [[ "$1" == -f && "$2" == /proc/driver/nvidia/version ]]; then
        return "$DRIVER_STATUS"
    fi
    builtin [ "$@"
}
source "$1" "argument with spaces"
"""
    with tempfile.TemporaryDirectory() as directory:
        runtime = Path(directory) / "singularity"
        runtime.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s|%s\\n" "${APPTAINER_NV-unset}" "${SINGULARITY_NV-unset}"\n'
            'printf "%s\\n" "${@: -1}"\n'
        )
        runtime.chmod(0o755)
        base_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"APPTAINER_NV", "SINGULARITY_NV", "BASH_ENV"}
        }
        base_env["NEURODESK_CONTAINER_RUNTIME"] = "singularity"
        base_env["PATH"] = f"{directory}:{os.environ['PATH']}"
        overrides = [{}, {"APPTAINER_NV": "0", "SINGULARITY_NV": "1"}]
        for name in ("APPTAINER_NV", "SINGULARITY_NV"):
            overrides.extend({name: value} for value in ("0", "1", "false", ""))
        for driver_present in (False, True):
            for override in overrides:
                expected = override or (
                    {"APPTAINER_NV": "1", "SINGULARITY_NV": "1"}
                    if driver_present
                    else {}
                )
                result = subprocess.run(
                    ["bash", "-c", shell, "test-wrapper", str(wrapper)],
                    env={
                        **base_env,
                        **override,
                        "DRIVER_STATUS": "0" if driver_present else "1",
                    },
                    capture_output=True,
                    text=True,
                )
                assert result.returncode == 0, result.stderr
                assert result.stdout.splitlines() == [
                    f"{expected.get('APPTAINER_NV', 'unset')}|{expected.get('SINGULARITY_NV', 'unset')}",
                    "argument with spaces",
                ], (driver_present, override, result.stdout)
