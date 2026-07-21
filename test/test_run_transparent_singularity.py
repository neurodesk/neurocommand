import os
import shutil
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSPARENT_SINGULARITY = ROOT / "neurodesk" / "transparent-singularity"


def write_executable(path, text):
    path.write_text(textwrap.dedent(text).lstrip())
    path.chmod(0o755)


def test_oras_pull_failure_falls_back_to_nectar(tmp_path):
    workdir = tmp_path / "transparent-singularity"
    shutil.copytree(TRANSPARENT_SINGULARITY, workdir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    container = "demo_arm64_1.0_20260629.simg"

    write_executable(
        bin_dir / "apptainer",
        f"""
        #!/usr/bin/env bash
        echo "apptainer $*" >> {calls}
        if [[ "$1" = "pull" ]]; then
            exit 42
        fi
        exit 0
        """,
    )
    write_executable(
        bin_dir / "curl",
        f"""
        #!/usr/bin/env bash
        echo "curl $*" >> {calls}
        args=" $* "

        if [[ "$args" == *"ghcr.io/token"* ]]; then
            echo '{{"token":"token"}}'
            exit 0
        fi

        if [[ "$args" == *"ghcr.io/v2/neurodesk/demo_arm64/manifests/1.0_20260629"* && "$args" == *"-sIL"* ]]; then
            printf 'HTTP/1.1 200 OK\\r\\nDocker-Content-Digest: sha256:docker\\r\\n\\r\\n'
            exit 0
        fi

        if [[ "$args" == *"ghcr.io/v2/neurodesk/demo_arm64/manifests/sha256-docker"* ]]; then
            echo '{{"manifests":[{{"artifactType":"application/vnd.sylabs.sif.layer.v1.sif","digest":"sha256:sif"}}]}}'
            exit 0
        fi

        if [[ "$args" == *"object-store.rc.nectar.org.au"* && "$args" == *"--head"* ]]; then
            exit 0
        fi

        if [[ "$args" == *"object-store.rc.nectar.org.au"* ]]; then
            output=""
            previous=""
            for arg in "$@"; do
                if [[ "$previous" = "--output" ]]; then
                    output="$arg"
                fi
                previous="$arg"
            done
            if [[ -z "$output" ]]; then
                exit 1
            fi
            echo "nectar-download $output ${{@: -1}}" >> {calls}
            echo "sif" > "$output"
            exit 0
        fi

        exit 1
        """,
    )
    write_executable(
        bin_dir / "jq",
        """
        #!/usr/bin/env bash
        cat >/dev/null
        if [[ "$*" == *".token"* ]]; then
            echo token
            exit 0
        fi
        if [[ "$*" == *".manifests"* ]]; then
            echo sha256:sif
            exit 0
        fi
        exit 1
        """,
    )
    write_executable(
        bin_dir / "singularity",
        f"""
        #!/usr/bin/env bash
        echo "singularity $*" >> {calls}

        if [[ "$1" = "version" ]]; then
            echo "3.10.0"
            exit 0
        fi

        if [[ "$1" = "build" ]]; then
            mkdir -p "$3"
            exit 0
        fi

        if [[ "$1" = "exec" ]]; then
            if [[ " $* " == *" cat /README.md "* ]]; then
                echo "demo readme"
                exit 0
            fi

            for arg in "$@"; do
                case "$arg" in
                    */ts_binaryFinder.sh)
                        base="$(dirname "$arg")"
                        printf 'demo\\n' > "$base/commands.txt"
                        : > "$base/env.txt"
                        exit 0
                        ;;
                esac
            done
        fi

        exit 0
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(workdir / "run_transparent_singularity.sh"),
            container.removesuffix(".simg"),
            "--unpack",
            "true",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    call_log = calls.read_text()
    assert (
        "apptainer pull --name demo_arm64_1.0_20260629.simg "
        "oras://ghcr.io/neurodesk/demo_arm64@sha256:sif"
    ) in call_log
    assert (
        "nectar-download demo_arm64_1.0_20260629.simg "
        "https://object-store.rc.nectar.org.au/v1/"
    ) in call_log
    assert (workdir / "demo_arm64_1.0_20260629.simg").is_dir()
    assert (tmp_path / "modules" / "demo_arm64" / "1.0.lua").is_file()
